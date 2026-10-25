//! Encrypted project vault backed by XChaCha20-Poly1305.
//!
//! The vault stores one encrypted file per secret under
//! `<workspace>/.openbad/blackbox` and a small plaintext metadata index at
//! `index.json`. The index contains only `id`, `name`, `created`, `updated`,
//! and `algorithm`; secret values never appear in the index.
//!
//! A 32-byte key is read from `OPENBAD_VAULT_KEY` (base64) or from
//! `~/.config/openbaddesktop/vault.key`. The key file is generated with mode
//! 0600 on first use and contains standard base64 followed by a newline.
//! Values are encrypted with XChaCha20-Poly1305 and the entry id/name are
//! bound as associated data, so swapping or renaming ciphertext files fails
//! authentication. Losing the key makes the vault unrecoverable.

#![forbid(unsafe_code)]

use std::fmt;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{LazyLock, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

use base64::Engine;
use base64::engine::general_purpose::{STANDARD, STANDARD_NO_PAD, URL_SAFE, URL_SAFE_NO_PAD};
use chacha20poly1305::aead::{Aead, Payload};
use chacha20poly1305::{KeyInit, XChaCha20Poly1305, XNonce};
use serde::{Deserialize, Serialize};
use zeroize::Zeroizing;

/// Environment variable that may hold a base64-encoded 32-byte vault key.
pub const KEY_ENV: &str = "OPENBAD_VAULT_KEY";
/// Algorithm recorded in vault metadata.
pub const ALGORITHM: &str = "XChaCha20-Poly1305";
/// Directory, relative to a workspace root, that contains the vault.
pub const VAULT_DIRECTORY: &str = ".openbad/blackbox";
/// Maximum plaintext secret size accepted by [`Vault::put`].
pub const MAX_SECRET_BYTES: usize = 1024 * 1024;

const INDEX_FILE: &str = "index.json";
const KEY_FILE_MAX_BYTES: u64 = 4096;
const INDEX_MAX_BYTES: u64 = 8 * 1024 * 1024;
const MAX_INDEX_ENTRIES: usize = 10_000;
const MAGIC: &[u8; 8] = b"OBVAULT1";
const NONCE_LEN: usize = 24;
const HEADER_LEN: usize = MAGIC.len() + NONCE_LEN;
const MAX_CIPHERTEXT_BYTES: u64 = (HEADER_LEN + MAX_SECRET_BYTES + 16) as u64;
const MAX_NAME_BYTES: usize = 128;

static WRITE_LOCK: LazyLock<Mutex<()>> = LazyLock::new(|| Mutex::new(()));
static TEMP_COUNTER: AtomicU64 = AtomicU64::new(0);

/// Errors returned by the vault.
///
/// No variant contains a secret value. Names, ids, paths, and algorithms are
/// metadata and may appear in diagnostics.
#[derive(Debug, thiserror::Error)]
pub enum VaultError {
    #[error("vault key must be base64-encoded 32 bytes")]
    InvalidKeyFormat,
    #[error("vault key decoded to {0} bytes; expected 32")]
    InvalidKeyLength(usize),
    #[error("vault key environment variable is not valid UTF-8")]
    NonUtf8KeyEnvironment,
    #[error("vault key file is not a regular file: {0}")]
    KeyFileNotRegular(PathBuf),
    #[error("vault key file permissions are too open (expected 0600): {0}")]
    InsecureKeyPermissions(PathBuf),
    #[error("vault key file is too large: {0}")]
    KeyFileTooLarge(PathBuf),
    #[error("could not determine the home directory for the vault key")]
    NoHomeDirectory,
    #[error("invalid vault path: {0}")]
    InvalidVaultPath(String),
    #[error("invalid vault index: {0}")]
    InvalidIndex(String),
    #[error("invalid secret name: {0}")]
    InvalidName(String),
    #[error("secret not found: {0}")]
    NotFound(String),
    #[error("secret value is too large: {0} bytes (limit 1 MiB)")]
    ValueTooLarge(usize),
    #[error("unsupported vault algorithm: {0}")]
    UnsupportedAlgorithm(String),
    #[error("secret value file is missing: {0}")]
    MissingValue(String),
    #[error("vault encryption failed")]
    Encryption,
    #[error("vault authentication failed (wrong key or corrupted value)")]
    Authentication,
    #[error("secret value is not valid UTF-8 and cannot be passed as an environment variable")]
    NotUtf8,
    #[error("secret value contains a NUL byte and cannot be passed as an environment variable")]
    ContainsNul,
    #[error("could not gather random bytes: {0}")]
    Random(String),
    #[error("io error: {0}")]
    Io(#[from] io::Error),
    #[error("vault index json error: {0}")]
    Json(#[from] serde_json::Error),
}

/// Metadata for one secret. This is exactly what the index stores.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct VaultEntry {
    pub id: String,
    pub name: String,
    pub created: u64,
    pub updated: u64,
    pub algorithm: String,
}

/// A decrypted secret. Intentional accessors only; `Debug` is redacted.
pub struct SecretValue {
    bytes: Zeroizing<Vec<u8>>,
}

impl SecretValue {
    /// Explicitly expose plaintext bytes to a trusted caller.
    #[must_use]
    pub fn expose_secret(&self) -> &[u8] {
        self.bytes.as_slice()
    }

    /// Explicitly expose plaintext as UTF-8.
    pub fn expose_str(&self) -> Result<&str, VaultError> {
        std::str::from_utf8(self.bytes.as_slice()).map_err(|_| VaultError::NotUtf8)
    }

    /// Expose plaintext as a value that may be placed in an environment
    /// variable. Rejects non-UTF-8 and NUL bytes.
    pub fn expose_env_str(&self) -> Result<&str, VaultError> {
        let text = self.expose_str()?;
        if text.contains('\0') {
            return Err(VaultError::ContainsNul);
        }
        Ok(text)
    }

    #[must_use]
    pub fn len(&self) -> usize {
        self.bytes.len()
    }

    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.bytes.is_empty()
    }
}

impl fmt::Debug for SecretValue {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("SecretValue([REDACTED])")
    }
}

/// An opened project vault. Keeping a vault instance holds the key in memory;
/// it zeroizes the key on drop.
pub struct Vault {
    root: PathBuf,
    key: Zeroizing<[u8; 32]>,
}

impl Vault {
    /// Open a vault using `OPENBAD_VAULT_KEY` or the default key file.
    pub fn open(workspace_root: impl AsRef<Path>) -> Result<Self, VaultError> {
        let key = load_key()?;
        Ok(Self {
            root: workspace_root.as_ref().to_path_buf(),
            key,
        })
    }

    /// Open a vault with an explicit key. Intended for hosts and tests that
    /// already resolved the key out of band.
    #[must_use]
    pub fn with_key(workspace_root: impl AsRef<Path>, key: [u8; 32]) -> Self {
        Self {
            root: workspace_root.as_ref().to_path_buf(),
            key: Zeroizing::new(key),
        }
    }

    /// Absolute or workspace-relative vault directory.
    #[must_use]
    pub fn vault_dir(&self) -> PathBuf {
        vault_dir(&self.root)
    }

    /// Create or replace a secret by name. The operation is atomic from the
    /// index's perspective and never writes plaintext to disk.
    pub fn put(&self, name: &str, value: &[u8]) -> Result<VaultEntry, VaultError> {
        validate_name(name)?;
        if value.len() > MAX_SECRET_BYTES {
            return Err(VaultError::ValueTooLarge(value.len()));
        }

        let _guard = write_guard();
        let directory = self.vault_dir();
        ensure_private_dir(&directory)?;
        let mut entries = read_index(&directory)?;
        let now = now_secs();

        let (index, entry, is_new) = match entries.iter().position(|entry| entry.name == name) {
            Some(index) => {
                let mut entry = entries[index].clone();
                entry.updated = now;
                entry.algorithm = ALGORITHM.to_owned();
                (index, entry, false)
            }
            None => {
                let entry = VaultEntry {
                    id: new_id(&entries)?,
                    name: name.to_owned(),
                    created: now,
                    updated: now,
                    algorithm: ALGORITHM.to_owned(),
                };
                entries.push(entry.clone());
                (entries.len() - 1, entry, true)
            }
        };

        let encrypted = encrypt_value(&self.key, &entry, value)?;
        let value_path = value_path(&directory, &entry.id);
        atomic_write(&value_path, &encrypted)?;

        entries[index] = entry.clone();
        if let Err(error) = write_index(&directory, &entries) {
            if is_new {
                let _ = fs::remove_file(&value_path);
            }
            return Err(error);
        }
        Ok(entry)
    }

    /// Decrypt a secret by name.
    pub fn get(&self, name: &str) -> Result<SecretValue, VaultError> {
        validate_name(name)?;
        let directory = self.vault_dir();
        let entries = read_index(&directory)?;
        let entry = entries
            .into_iter()
            .find(|entry| entry.name == name)
            .ok_or_else(|| VaultError::NotFound(name.to_owned()))?;
        if entry.algorithm != ALGORITHM {
            return Err(VaultError::UnsupportedAlgorithm(entry.algorithm));
        }
        let bytes = read_regular_file(
            &value_path(&directory, &entry.id),
            MAX_CIPHERTEXT_BYTES,
        )?
        .ok_or_else(|| VaultError::MissingValue(name.to_owned()))?;
        decrypt_value(&self.key, &entry, &bytes)
    }

    /// List secret metadata only. Values are never read or returned.
    pub fn list(&self) -> Result<Vec<VaultEntry>, VaultError> {
        list_metadata(&self.root)
    }

    /// Remove a secret by name. Returns the removed metadata, or `None` when
    /// the name does not exist.
    pub fn remove(&self, name: &str) -> Result<Option<VaultEntry>, VaultError> {
        validate_name(name)?;
        let _guard = write_guard();
        let directory = self.vault_dir();
        let mut entries = read_index(&directory)?;
        let Some(index) = entries.iter().position(|entry| entry.name == name) else {
            return Ok(None);
        };
        ensure_private_dir(&directory)?;
        let entry = entries.remove(index);
        write_index(&directory, &entries)?;
        // The index no longer references the value. Failure to delete the
        // ciphertext leaves only an authenticated orphan, not plaintext.
        let _ = fs::remove_file(value_path(&directory, &entry.id));
        Ok(Some(entry))
    }
}

/// Metadata-only listing that does not need the key and never creates one.
/// This is used by read-only model tools so listing cannot mutate key state.
pub fn list_metadata(workspace_root: impl AsRef<Path>) -> Result<Vec<VaultEntry>, VaultError> {
    let mut entries = read_index(&vault_dir(workspace_root.as_ref()))?;
    entries.sort_by(|left, right| left.name.cmp(&right.name));
    Ok(entries)
}

/// Resolve the vault directory for a workspace root.
#[must_use]
pub fn vault_dir(workspace_root: impl AsRef<Path>) -> PathBuf {
    workspace_root.as_ref().join(VAULT_DIRECTORY)
}

/// Default key file path, required by the product contract to live under
/// `~/.config/openbaddesktop/vault.key` on every platform.
pub fn default_key_file_path() -> Result<PathBuf, VaultError> {
    let home = directories::BaseDirs::new()
        .ok_or(VaultError::NoHomeDirectory)?
        .home_dir()
        .to_path_buf();
    Ok(home.join(".config").join("openbaddesktop").join("vault.key"))
}

fn load_key() -> Result<Zeroizing<[u8; 32]>, VaultError> {
    if let Some(value) = std::env::var_os(KEY_ENV) {
        let value = Zeroizing::new(
            value
                .into_string()
                .map_err(|_| VaultError::NonUtf8KeyEnvironment)?,
        );
        return decode_key(&value).map(Zeroizing::new);
    }
    load_or_create_key_at(&default_key_file_path()?)
}

fn decode_key(text: &str) -> Result<[u8; 32], VaultError> {
    let trimmed = text.trim();
    if trimmed.is_empty() {
        return Err(VaultError::InvalidKeyFormat);
    }
    let decoded = STANDARD
        .decode(trimmed)
        .or_else(|_| STANDARD_NO_PAD.decode(trimmed))
        .or_else(|_| URL_SAFE.decode(trimmed))
        .or_else(|_| URL_SAFE_NO_PAD.decode(trimmed))
        .map_err(|_| VaultError::InvalidKeyFormat)?;
    let length = decoded.len();
    let decoded = Zeroizing::new(decoded);
    decoded
        .as_slice()
        .try_into()
        .map_err(|_| VaultError::InvalidKeyLength(length))
}

fn load_or_create_key_at(path: &Path) -> Result<Zeroizing<[u8; 32]>, VaultError> {
    match fs::symlink_metadata(path) {
        Ok(metadata) => {
            if metadata.file_type().is_symlink() || !metadata.file_type().is_file() {
                return Err(VaultError::KeyFileNotRegular(path.to_path_buf()));
            }
            if metadata.len() > KEY_FILE_MAX_BYTES {
                return Err(VaultError::KeyFileTooLarge(path.to_path_buf()));
            }
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                let mode = metadata.permissions().mode() & 0o777;
                if mode & 0o077 != 0 {
                    return Err(VaultError::InsecureKeyPermissions(path.to_path_buf()));
                }
            }
            let text = Zeroizing::new(fs::read_to_string(path)?);
            decode_key(&text).map(Zeroizing::new)
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            let parent = path.parent().ok_or_else(|| {
                VaultError::InvalidVaultPath(format!(
                    "key path has no parent: {}",
                    path.display()
                ))
            })?;
            ensure_private_dir(parent)?;
            let mut key = Zeroizing::new([0u8; 32]);
            fill_random(&mut *key)?;
            let encoded = Zeroizing::new(STANDARD.encode(&key[..]).into_bytes());
            let mut contents = encoded;
            contents.push(b'\n');

            let mut options = OpenOptions::new();
            options.write(true).create_new(true);
            #[cfg(unix)]
            {
                use std::os::unix::fs::OpenOptionsExt;
                options.mode(0o600);
            }
            match options.open(path) {
                Ok(mut file) => {
                    if let Err(error) = file.write_all(&contents).and_then(|()| file.sync_all()) {
                        drop(file);
                        let _ = fs::remove_file(path);
                        return Err(error.into());
                    }
                    #[cfg(unix)]
                    {
                        use std::os::unix::fs::PermissionsExt;
                        file.set_permissions(fs::Permissions::from_mode(0o600))?;
                    }
                    drop(file);
                    sync_directory(parent);
                    Ok(key)
                }
                Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {
                    // Another process won the creation race. Read its key;
                    // an incomplete file fails closed in the recursive call.
                    load_or_create_key_at(path)
                }
                Err(error) => Err(error.into()),
            }
        }
        Err(error) => Err(error.into()),
    }
}

fn validate_name(name: &str) -> Result<(), VaultError> {
    let bytes = name.as_bytes();
    let valid = !bytes.is_empty()
        && bytes.len() <= MAX_NAME_BYTES
        && bytes[0].is_ascii_alphanumeric()
        && bytes
            .iter()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-'));
    if valid {
        Ok(())
    } else {
        Err(VaultError::InvalidName(
            "use 1-128 ASCII letters, digits, '.', '_' or '-', starting with a letter or digit"
                .to_owned(),
        ))
    }
}

fn validate_id(id: &str) -> Result<(), VaultError> {
    if id.len() == 32 && id.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        Ok(())
    } else {
        Err(VaultError::InvalidIndex(
            "entry id must be 32 hexadecimal characters".to_owned(),
        ))
    }
}

fn read_index(directory: &Path) -> Result<Vec<VaultEntry>, VaultError> {
    let Some(bytes) = read_regular_file(&directory.join(INDEX_FILE), INDEX_MAX_BYTES)? else {
        return Ok(Vec::new());
    };
    let entries: Vec<VaultEntry> = if bytes.iter().all(|byte| byte.is_ascii_whitespace()) {
        Vec::new()
    } else {
        serde_json::from_slice(&bytes)
            .map_err(|error| VaultError::InvalidIndex(error.to_string()))?
    };
    if entries.len() > MAX_INDEX_ENTRIES {
        return Err(VaultError::InvalidIndex(format!(
            "index has more than {MAX_INDEX_ENTRIES} entries"
        )));
    }

    let mut ids = std::collections::HashSet::with_capacity(entries.len());
    let mut names = std::collections::HashSet::with_capacity(entries.len());
    for entry in &entries {
        validate_id(&entry.id)?;
        validate_name(&entry.name)?;
        if !ids.insert(entry.id.as_str()) {
            return Err(VaultError::InvalidIndex(format!(
                "duplicate entry id: {}",
                entry.id
            )));
        }
        if !names.insert(entry.name.as_str()) {
            return Err(VaultError::InvalidIndex(format!(
                "duplicate entry name: {}",
                entry.name
            )));
        }
        if entry.algorithm.is_empty() {
            return Err(VaultError::InvalidIndex(
                "entry algorithm must not be empty".to_owned(),
            ));
        }
    }
    Ok(entries)
}

fn write_index(directory: &Path, entries: &[VaultEntry]) -> Result<(), VaultError> {
    let mut sorted = entries.to_vec();
    sorted.sort_by(|left, right| left.name.cmp(&right.name));
    let mut bytes = serde_json::to_vec_pretty(&sorted)?;
    bytes.push(b'\n');
    atomic_write(&directory.join(INDEX_FILE), &bytes)
}

fn read_regular_file(path: &Path, max_bytes: u64) -> Result<Option<Vec<u8>>, VaultError> {
    let metadata = match fs::symlink_metadata(path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error.into()),
    };
    if metadata.file_type().is_symlink() || !metadata.file_type().is_file() {
        return Err(VaultError::InvalidVaultPath(format!(
            "not a regular file: {}",
            path.display()
        )));
    }
    if metadata.len() > max_bytes {
        return Err(VaultError::InvalidVaultPath(format!(
            "file is too large: {}",
            path.display()
        )));
    }
    Ok(Some(fs::read(path)?))
}

fn ensure_private_dir(path: &Path) -> Result<(), VaultError> {
    match fs::symlink_metadata(path) {
        Ok(metadata) => {
            if metadata.file_type().is_symlink() || !metadata.is_dir() {
                return Err(VaultError::InvalidVaultPath(format!(
                    "vault path is not a directory: {}",
                    path.display()
                )));
            }
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                let mode = metadata.permissions().mode() & 0o777;
                if mode & 0o077 != 0 {
                    fs::set_permissions(path, fs::Permissions::from_mode(0o700))?;
                }
            }
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            let mut builder = fs::DirBuilder::new();
            builder.recursive(true);
            #[cfg(unix)]
            {
                use std::os::unix::fs::DirBuilderExt;
                builder.mode(0o700);
            }
            builder.create(path)?;
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                fs::set_permissions(path, fs::Permissions::from_mode(0o700))?;
            }
        }
        Err(error) => return Err(error.into()),
    }
    Ok(())
}

fn atomic_write(path: &Path, bytes: &[u8]) -> Result<(), VaultError> {
    let parent = path.parent().ok_or_else(|| {
        VaultError::InvalidVaultPath(format!("path has no parent: {}", path.display()))
    })?;
    ensure_private_dir(parent)?;

    let suffix = TEMP_COUNTER.fetch_add(1, Ordering::Relaxed);
    let temp = parent.join(format!(
        ".tmp-{}-{}-{suffix}",
        std::process::id(),
        now_nanos()
    ));
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = match options.open(&temp) {
        Ok(file) => file,
        Err(error) => return Err(error.into()),
    };
    if let Err(error) = file.write_all(bytes).and_then(|()| file.sync_all()) {
        drop(file);
        let _ = fs::remove_file(&temp);
        return Err(error.into());
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if let Err(error) = file.set_permissions(fs::Permissions::from_mode(0o600)) {
            drop(file);
            let _ = fs::remove_file(&temp);
            return Err(error.into());
        }
    }
    drop(file);

    if let Err(error) = fs::rename(&temp, path) {
        let _ = fs::remove_file(&temp);
        return Err(error.into());
    }
    sync_directory(parent);
    Ok(())
}

fn encrypt_value(
    key: &[u8; 32],
    entry: &VaultEntry,
    value: &[u8],
) -> Result<Vec<u8>, VaultError> {
    let cipher = XChaCha20Poly1305::new(chacha20poly1305::Key::from_slice(key));
    let mut nonce = [0u8; NONCE_LEN];
    fill_random(&mut nonce)?;
    let aad = associated_data(entry);
    let ciphertext = cipher
        .encrypt(
            XNonce::from_slice(&nonce),
            Payload {
                msg: value,
                aad: &aad,
            },
        )
        .map_err(|_| VaultError::Encryption)?;
    let mut output = Vec::with_capacity(HEADER_LEN + ciphertext.len());
    output.extend_from_slice(MAGIC);
    output.extend_from_slice(&nonce);
    output.extend_from_slice(&ciphertext);
    Ok(output)
}

fn decrypt_value(
    key: &[u8; 32],
    entry: &VaultEntry,
    bytes: &[u8],
) -> Result<SecretValue, VaultError> {
    if bytes.len() < HEADER_LEN || &bytes[..MAGIC.len()] != MAGIC {
        return Err(VaultError::Authentication);
    }
    let cipher = XChaCha20Poly1305::new(chacha20poly1305::Key::from_slice(key));
    let nonce = XNonce::from_slice(&bytes[MAGIC.len()..HEADER_LEN]);
    let aad = associated_data(entry);
    let plaintext = cipher
        .decrypt(
            nonce,
            Payload {
                msg: &bytes[HEADER_LEN..],
                aad: &aad,
            },
        )
        .map_err(|_| VaultError::Authentication)?;
    Ok(SecretValue {
        bytes: Zeroizing::new(plaintext),
    })
}

fn associated_data(entry: &VaultEntry) -> Vec<u8> {
    let mut aad = Vec::with_capacity(entry.id.len() + entry.name.len() + 20);
    aad.extend_from_slice(b"openbad-vault:v1\0");
    aad.extend_from_slice(entry.id.as_bytes());
    aad.push(0);
    aad.extend_from_slice(entry.name.as_bytes());
    aad
}

fn value_path(directory: &Path, id: &str) -> PathBuf {
    directory.join(format!("{id}.secret"))
}

fn new_id(entries: &[VaultEntry]) -> Result<String, VaultError> {
    for _ in 0..8 {
        let mut bytes = [0u8; 16];
        fill_random(&mut bytes)?;
        let id = hex_lower(&bytes);
        if entries.iter().all(|entry| entry.id != id) {
            return Ok(id);
        }
    }
    Err(VaultError::Random(
        "could not generate a unique secret id".to_owned(),
    ))
}

fn hex_lower(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut encoded = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        encoded.push(HEX[(byte >> 4) as usize] as char);
        encoded.push(HEX[(byte & 0x0f) as usize] as char);
    }
    encoded
}

fn fill_random(buffer: &mut [u8]) -> Result<(), VaultError> {
    getrandom::getrandom(buffer).map_err(|error| VaultError::Random(error.to_string()))
}

fn now_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .unwrap_or(0)
}

fn now_nanos() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .unwrap_or(0)
}

fn write_guard() -> std::sync::MutexGuard<'static, ()> {
    WRITE_LOCK
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}

fn sync_directory(path: &Path) {
    #[cfg(unix)]
    {
        if let Ok(file) = File::open(path) {
            let _ = file.sync_all();
        }
    }
    #[cfg(not(unix))]
    {
        let _ = path;
    }
}

#[cfg(test)]
mod tests {
    use std::fs;

    use tempfile::tempdir;

    use super::*;

    fn test_vault(root: &Path, key: [u8; 32]) -> Vault {
        Vault::with_key(root, key)
    }

    fn scan_for_plaintext(path: &Path, needle: &[u8]) -> bool {
        for entry in fs::read_dir(path).expect("read_dir") {
            let entry = entry.expect("entry");
            let path = entry.path();
            if path.is_dir() {
                if scan_for_plaintext(&path, needle) {
                    return true;
                }
            } else if let Ok(bytes) = fs::read(&path) {
                if bytes.windows(needle.len()).any(|window| window == needle) {
                    return true;
                }
            }
        }
        false
    }

    fn index_json(root: &Path) -> serde_json::Value {
        let path = vault_dir(root).join("index.json");
        serde_json::from_slice(&fs::read(path).expect("index")).expect("json")
    }

    #[test]
    fn roundtrip_and_metadata_only_index() {
        let directory = tempdir().expect("tempdir");
        let vault = test_vault(directory.path(), [7u8; 32]);
        let secret = b"correct horse battery staple 798";

        let entry = vault.put("db-password", secret).expect("put");
        assert_eq!(entry.name, "db-password");
        assert_eq!(entry.algorithm, ALGORITHM);
        assert_eq!(entry.id.len(), 32);

        let value = vault.get("db-password").expect("get");
        assert_eq!(value.expose_secret(), secret);
        assert_eq!(format!("{value:?}"), "SecretValue([REDACTED])");

        let listed = vault.list().expect("list");
        assert_eq!(listed.len(), 1);
        assert_eq!(listed[0], entry);

        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = |path: &Path| {
                fs::metadata(path)
                    .expect("metadata")
                    .permissions()
                    .mode()
                    & 0o777
            };
            let vault_path = vault_dir(directory.path());
            assert_eq!(mode(&vault_path), 0o700);
            assert_eq!(mode(&vault_path.join("index.json")), 0o600);
            assert_eq!(
                mode(&vault_path.join(format!("{}.secret", entry.id))),
                0o600
            );
        }

        let index = index_json(directory.path());
        let entries = index.as_array().expect("index array");
        assert_eq!(entries.len(), 1);
        let object = entries[0].as_object().expect("entry object");
        let mut keys = object.keys().cloned().collect::<Vec<_>>();
        keys.sort();
        assert_eq!(
            keys,
            vec!["algorithm", "created", "id", "name", "updated"]
        );
        assert_eq!(object["algorithm"], ALGORITHM);
        assert!(!index.to_string().contains("correct horse"));
        assert!(!scan_for_plaintext(
            &vault_dir(directory.path()),
            secret
        ));
    }

    #[test]
    fn wrong_key_fails_authentication() {
        let directory = tempdir().expect("tempdir");
        let writer = test_vault(directory.path(), [1u8; 32]);
        writer.put("api-token", b"not-for-the-wrong-key").expect("put");

        let reader = test_vault(directory.path(), [2u8; 32]);
        let error = reader.get("api-token").expect_err("wrong key must fail");
        assert!(matches!(error, VaultError::Authentication));
    }

    #[test]
    fn tampering_and_value_swaps_are_detected() {
        let directory = tempdir().expect("tempdir");
        let vault = test_vault(directory.path(), [3u8; 32]);
        let first = vault.put("first", b"first-secret-value").expect("put first");
        let second = vault.put("second", b"second-secret-value").expect("put second");

        let first_path = vault_dir(directory.path()).join(format!("{}.secret", first.id));
        let second_path = vault_dir(directory.path()).join(format!("{}.secret", second.id));
        let original = fs::read(&first_path).expect("read first ciphertext");

        let mut tampered = original.clone();
        let last = tampered.len() - 1;
        tampered[last] ^= 0x80;
        fs::write(&first_path, &tampered).expect("tamper");
        assert!(matches!(
            vault.get("first").expect_err("tamper must fail"),
            VaultError::Authentication
        ));

        let mut bad_nonce = original.clone();
        bad_nonce[10] ^= 0x01;
        fs::write(&first_path, &bad_nonce).expect("tamper nonce");
        assert!(matches!(
            vault.get("first").expect_err("nonce tamper must fail"),
            VaultError::Authentication
        ));

        fs::write(&first_path, &original).expect("restore");
        assert_eq!(
            vault.get("first").expect("restored").expose_secret(),
            b"first-secret-value"
        );

        // Swap complete ciphertext files. AAD binds the entry id and name, so
        // the swapped value must not authenticate under the other name.
        fs::copy(&second_path, &first_path).expect("swap ciphertext");
        assert!(matches!(
            vault.get("first").expect_err("swapped value must fail"),
            VaultError::Authentication
        ));
    }

    #[test]
    fn renaming_an_index_entry_breaks_authentication() {
        let directory = tempdir().expect("tempdir");
        let vault = test_vault(directory.path(), [10u8; 32]);
        vault.put("original-name", b"rename-bound-value").expect("put");

        let index_path = vault_dir(directory.path()).join("index.json");
        let mut index = index_json(directory.path());
        index[0]["name"] = serde_json::Value::String("changed-name".to_owned());
        fs::write(
            &index_path,
            serde_json::to_vec_pretty(&index).expect("index json"),
        )
        .expect("rewrite index");

        let error = vault.get("changed-name").expect_err("AAD name binding");
        assert!(matches!(error, VaultError::Authentication));
        assert!(matches!(
            vault.get("original-name").expect_err("old name gone"),
            VaultError::NotFound(_)
        ));
    }

    #[test]
    fn index_with_unknown_fields_or_duplicate_names_is_rejected() {
        let directory = tempdir().expect("tempdir");
        let vault_path = vault_dir(directory.path());
        fs::create_dir_all(&vault_path).expect("vault dir");
        let index_path = vault_path.join("index.json");

        fs::write(
            &index_path,
            r#"[{"id":"00000000000000000000000000000000","name":"alpha","created":1,"updated":2,"algorithm":"XChaCha20-Poly1305","value":"must-not-be-here"}]"#,
        )
        .expect("write index");
        assert!(matches!(
            list_metadata(directory.path()).expect_err("unknown field"),
            VaultError::InvalidIndex(_)
        ));

        fs::write(
            &index_path,
            r#"[{"id":"00000000000000000000000000000000","name":"alpha","created":1,"updated":2,"algorithm":"XChaCha20-Poly1305"},{"id":"11111111111111111111111111111111","name":"alpha","created":1,"updated":2,"algorithm":"XChaCha20-Poly1305"}]"#,
        )
        .expect("write duplicate index");
        assert!(matches!(
            list_metadata(directory.path()).expect_err("duplicate names"),
            VaultError::InvalidIndex(_)
        ));
    }

    #[test]
    fn no_plaintext_in_any_vault_file_or_index() {
        let directory = tempdir().expect("tempdir");
        let vault = test_vault(directory.path(), [4u8; 32]);
        let secret = b"p@ssw0rd-that-must-not-appear-in-plaintext-798";
        vault.put("production-db", secret).expect("put");

        let vault_path = vault_dir(directory.path());
        assert!(!scan_for_plaintext(&vault_path, secret));
        let index = fs::read_to_string(vault_path.join("index.json")).expect("index");
        assert!(!index.contains("p@ssw0rd"));
        assert!(index.contains("production-db")); // names are metadata

        for entry in fs::read_dir(&vault_path).expect("read_dir") {
            let entry = entry.expect("entry");
            assert!(
                !entry.file_name().to_string_lossy().starts_with(".tmp-"),
                "atomic writes must not leave temporary files"
            );
        }
    }

    #[test]
    fn remove_deletes_metadata_and_ciphertext() {
        let directory = tempdir().expect("tempdir");
        let vault = test_vault(directory.path(), [5u8; 32]);
        let entry = vault.put("to-remove", b"bye").expect("put");
        let value_path = vault_dir(directory.path()).join(format!("{}.secret", entry.id));
        assert!(value_path.is_file());

        let removed = vault.remove("to-remove").expect("remove");
        assert_eq!(removed, Some(entry));
        assert!(vault.remove("to-remove").expect("second remove").is_none());
        assert!(!value_path.exists());
        assert!(vault.list().expect("list").is_empty());
        assert!(matches!(
            vault.get("to-remove").expect_err("gone"),
            VaultError::NotFound(_)
        ));
    }

    #[test]
    fn put_updates_existing_entry_metadata() {
        let directory = tempdir().expect("tempdir");
        let vault = test_vault(directory.path(), [6u8; 32]);
        let first = vault.put("token", b"one").expect("first");
        let second = vault.put("token", b"two").expect("second");
        assert_eq!(first.id, second.id);
        assert_eq!(first.created, second.created);
        assert!(second.updated >= first.updated);
        assert_eq!(vault.get("token").expect("get").expose_secret(), b"two");
        assert_eq!(vault.list().expect("list").len(), 1);
    }

    #[test]
    fn rejects_unsafe_names_and_large_values() {
        let directory = tempdir().expect("tempdir");
        let vault = test_vault(directory.path(), [8u8; 32]);
        for name in ["", "../escape", "with space", "slash/name", "-leading"] {
            assert!(matches!(
                vault.put(name, b"x").expect_err("invalid name"),
                VaultError::InvalidName(_)
            ));
        }
        let too_large = vec![0u8; MAX_SECRET_BYTES + 1];
        assert!(matches!(
            vault.put("too-large", &too_large).expect_err("too large"),
            VaultError::ValueTooLarge(_)
        ));
    }

    #[test]
    fn key_file_is_created_private_and_reused() {
        let directory = tempdir().expect("tempdir");
        let path = directory.path().join("openbaddesktop/vault.key");
        let first = load_or_create_key_at(&path).expect("create key");
        let second = load_or_create_key_at(&path).expect("reuse key");
        assert_eq!(*first, *second);
        assert!(path.is_file());

        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = fs::metadata(&path)
                .expect("key metadata")
                .permissions()
                .mode()
                & 0o777;
            assert_eq!(mode, 0o600);
            let parent_mode = fs::metadata(path.parent().expect("parent"))
                .expect("parent metadata")
                .permissions()
                .mode()
                & 0o777;
            assert_eq!(parent_mode, 0o700);
        }
    }

    #[cfg(unix)]
    #[test]
    fn insecure_key_file_permissions_are_rejected() {
        use std::os::unix::fs::PermissionsExt;

        let directory = tempdir().expect("tempdir");
        let path = directory.path().join("vault.key");
        load_or_create_key_at(&path).expect("create key");
        fs::set_permissions(&path, fs::Permissions::from_mode(0o644)).expect("chmod");
        assert!(matches!(
            load_or_create_key_at(&path).expect_err("insecure permissions"),
            VaultError::InsecureKeyPermissions(_)
        ));
    }

    #[test]
    fn key_encoding_accepts_base64_variants_and_rejects_bad_lengths() {
        let key = [42u8; 32];
        let standard = STANDARD.encode(key);
        assert_eq!(decode_key(&standard).expect("standard"), key);
        assert_eq!(
            decode_key(&STANDARD_NO_PAD.encode(key)).expect("no pad"),
            key
        );
        assert_eq!(decode_key(&URL_SAFE.encode(key)).expect("url"), key);
        assert_eq!(
            decode_key(&URL_SAFE_NO_PAD.encode(key)).expect("url no pad"),
            key
        );
        assert!(matches!(
            decode_key("").expect_err("empty"),
            VaultError::InvalidKeyFormat
        ));
        assert!(matches!(
            decode_key("not base64!").expect_err("invalid"),
            VaultError::InvalidKeyFormat
        ));
        assert!(matches!(
            decode_key(&STANDARD.encode([1u8; 31])).expect_err("short"),
            VaultError::InvalidKeyLength(31)
        ));
    }

    #[test]
    fn env_exposure_rejects_nul_and_non_utf8_secrets() {
        let ok = SecretValue {
            bytes: Zeroizing::new(b"ok".to_vec()),
        };
        assert_eq!(ok.expose_env_str().expect("utf8"), "ok");

        let nul = SecretValue {
            bytes: Zeroizing::new(b"a\0b".to_vec()),
        };
        assert!(matches!(
            nul.expose_env_str().expect_err("nul"),
            VaultError::ContainsNul
        ));

        let invalid = SecretValue {
            bytes: Zeroizing::new(vec![0xff, 0xfe]),
        };
        assert!(matches!(
            invalid.expose_env_str().expect_err("non-utf8"),
            VaultError::NotUtf8
        ));
    }

    #[test]
    fn metadata_listing_does_not_require_a_key() {
        let directory = tempdir().expect("tempdir");
        let vault = test_vault(directory.path(), [9u8; 32]);
        vault.put("alpha", b"a").expect("put");
        let entries = list_metadata(directory.path()).expect("metadata");
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].name, "alpha");
        assert_eq!(entries[0].algorithm, ALGORITHM);
    }
}
