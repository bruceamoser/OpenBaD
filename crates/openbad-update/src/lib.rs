//! Update checks against GitHub releases and platform installer handling.
//!
//! The crate is transport-injectable through [`UpdateService::with_api_base`],
//! so release parsing, asset selection, downloads, and installation can be
//! tested without network access. Release checksums are verified when the
//! release publishes a `SHA256SUMS` asset.

use std::fs;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::time::Duration;

use semver::Version;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};

/// Repository that publishes OpenBaD releases.
pub const DEFAULT_REPO: &str = "bruceamoser/OpenBaD";
pub const DEFAULT_API_BASE: &str = "https://api.github.com";
const DEFAULT_TIMEOUT: Duration = Duration::from_secs(60);
const MAX_DOWNLOAD_BYTES: u64 = 2 * 1024 * 1024 * 1024;
const CHECKSUM_ASSET_NAMES: [&str; 4] = [
    "sha256sums",
    "sha256sums.txt",
    "checksums.txt",
    "sha256.txt",
];

#[derive(Debug, thiserror::Error)]
pub enum UpdateError {
    #[error("network error: {0}")]
    Network(String),
    #[error("invalid release metadata: {0}")]
    InvalidRelease(String),
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
    #[error("no published release was found")]
    NoRelease,
    #[error("no installer asset matches {platform}")]
    NoAsset { platform: String },
    #[error("the download exceeds the {0}-byte limit")]
    TooLarge(u64),
    #[error("downloaded asset size does not match the release metadata")]
    SizeMismatch,
    #[error("checksum verification failed: {0}")]
    Checksum(String),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Os {
    Linux,
    Macos,
    Windows,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Arch {
    #[serde(rename = "x86_64")]
    X8664,
    Aarch64,
    Other,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct Target {
    pub os: Os,
    pub arch: Arch,
}

impl Target {
    #[must_use]
    pub fn current() -> Self {
        let os = match std::env::consts::OS {
            "macos" => Os::Macos,
            "windows" => Os::Windows,
            _ => Os::Linux,
        };
        let arch = match std::env::consts::ARCH {
            "x86_64" => Arch::X8664,
            "aarch64" => Arch::Aarch64,
            _ => Arch::Other,
        };
        Self { os, arch }
    }

    #[must_use]
    pub fn label(&self) -> String {
        let os = match self.os {
            Os::Linux => "linux",
            Os::Macos => "macos",
            Os::Windows => "windows",
        };
        let arch = match self.arch {
            Arch::X8664 => "x86_64",
            Arch::Aarch64 => "aarch64",
            Arch::Other => "unknown",
        };
        format!("{os}-{arch}")
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AssetKind {
    AppImage,
    Deb,
    Rpm,
    Msi,
    Exe,
    Dmg,
}

impl AssetKind {
    #[must_use]
    pub fn is_self_installable(self) -> bool {
        matches!(self, Self::AppImage)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ReleaseAsset {
    pub name: String,
    pub url: String,
    #[serde(default)]
    pub size: Option<u64>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ReleaseInfo {
    pub tag: String,
    pub version: String,
    pub name: String,
    pub notes: String,
    pub html_url: String,
    pub published_at: Option<String>,
    pub assets: Vec<ReleaseAsset>,
    #[serde(default)]
    pub checksums: Option<ReleaseAsset>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum UpdateStatus {
    NoRelease {
        current: String,
    },
    UpToDate {
        current: String,
        latest: String,
    },
    Available {
        current: String,
        latest: String,
        release: Box<ReleaseInfo>,
        asset: ReleaseAsset,
        installer: AssetKind,
    },
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum InstallOutcome {
    /// The running AppImage was replaced; restart to use the new version.
    AppImageReplaced { path: PathBuf },
    /// The installer was handed to the operating system.
    InstallerLaunched { path: PathBuf, installer: AssetKind },
    /// The installer was saved but could not be launched automatically.
    InstallerSaved { path: PathBuf, installer: AssetKind },
}

/// Builds release checks and downloads for one repository.
pub struct UpdateService {
    repo: String,
    api_base: String,
    target: Target,
    timeout: Duration,
}

impl Default for UpdateService {
    fn default() -> Self {
        Self::new(DEFAULT_REPO)
    }
}

impl UpdateService {
    #[must_use]
    pub fn new(repo: impl Into<String>) -> Self {
        Self {
            repo: repo.into(),
            api_base: DEFAULT_API_BASE.to_owned(),
            target: Target::current(),
            timeout: DEFAULT_TIMEOUT,
        }
    }

    #[must_use]
    pub fn with_target(mut self, target: Target) -> Self {
        self.target = target;
        self
    }

    #[must_use]
    pub fn with_api_base(mut self, api_base: impl Into<String>) -> Self {
        self.api_base = api_base.into();
        self
    }

    #[must_use]
    pub fn with_timeout(mut self, timeout: Duration) -> Self {
        self.timeout = timeout;
        self
    }

    #[must_use]
    pub fn target(&self) -> Target {
        self.target
    }

    /// Fetches the latest published release and compares it with `current`.
    pub fn check(&self, current: &str) -> Result<UpdateStatus, UpdateError> {
        let Some(release) = self.latest_release()? else {
            return Ok(UpdateStatus::NoRelease {
                current: current.to_owned(),
            });
        };
        self.check_release_json(current, &release)
    }

    /// Compares a GitHub release payload with the running version.
    pub fn check_release_json(
        &self,
        current: &str,
        value: &Value,
    ) -> Result<UpdateStatus, UpdateError> {
        let current_version = Version::parse(current)
            .map_err(|error| UpdateError::InvalidRelease(error.to_string()))?;
        let release = ReleaseInfo::parse(value)?;
        let latest = Version::parse(&release.version)
            .map_err(|error| UpdateError::InvalidRelease(error.to_string()))?;
        if latest <= current_version {
            return Ok(UpdateStatus::UpToDate {
                current: current.to_owned(),
                latest: release.version,
            });
        }
        let (asset, installer) =
            select_asset(&release.assets, self.target).ok_or_else(|| UpdateError::NoAsset {
                platform: self.target.label(),
            })?;
        let latest = release.version.clone();
        Ok(UpdateStatus::Available {
            current: current.to_owned(),
            latest,
            release: Box::new(release),
            asset,
            installer,
        })
    }

    /// Downloads a release asset into `directory` and verifies its checksum
    /// when the release publishes one.
    pub fn download(
        &self,
        release: &ReleaseInfo,
        asset: &ReleaseAsset,
        directory: &Path,
    ) -> Result<PathBuf, UpdateError> {
        if !secure_url(&asset.url) {
            return Err(UpdateError::Network(format!(
                "refusing to download from an insecure URL: {}",
                asset.url
            )));
        }
        fs::create_dir_all(directory)?;
        let path = directory.join(safe_file_name(&asset.name));
        let response = self
            .client()?
            .get(&asset.url)
            .send()
            .map_err(|error| UpdateError::Network(error.to_string()))?;
        if !response.status().is_success() {
            return Err(UpdateError::Network(format!(
                "download failed with HTTP {}",
                response.status()
            )));
        }
        if response.content_length().unwrap_or(0) > MAX_DOWNLOAD_BYTES {
            return Err(UpdateError::TooLarge(MAX_DOWNLOAD_BYTES));
        }
        let mut file = fs::File::create(&path)?;
        let mut body = response.take(MAX_DOWNLOAD_BYTES + 1);
        let copied = match std::io::copy(&mut body, &mut file) {
            Ok(copied) => copied,
            Err(error) => {
                let _ = fs::remove_file(&path);
                return Err(error.into());
            }
        };
        file.flush()?;
        if copied > MAX_DOWNLOAD_BYTES {
            let _ = fs::remove_file(&path);
            return Err(UpdateError::TooLarge(MAX_DOWNLOAD_BYTES));
        }
        if let Some(expected) = asset.size {
            if expected != copied {
                let _ = fs::remove_file(&path);
                return Err(UpdateError::SizeMismatch);
            }
        }
        if let Err(error) = self.verify_checksums(release, asset, &path) {
            let _ = fs::remove_file(&path);
            return Err(error);
        }
        Ok(path)
    }

    /// Installs a downloaded asset. AppImages replace the running AppImage
    /// atomically; other installers are saved to `downloads` and handed to the
    /// operating system.
    pub fn install(
        &self,
        asset: &ReleaseAsset,
        installer: AssetKind,
        downloaded: &Path,
        app_image: Option<&Path>,
        downloads: &Path,
    ) -> Result<InstallOutcome, UpdateError> {
        if installer.is_self_installable() {
            if let Some(app_image) = app_image {
                replace_app_image(downloaded, app_image)?;
                return Ok(InstallOutcome::AppImageReplaced {
                    path: app_image.to_path_buf(),
                });
            }
        }
        let path = stage_installer(downloaded, downloads, asset)?;
        if launch_installer(&path) {
            Ok(InstallOutcome::InstallerLaunched { path, installer })
        } else {
            Ok(InstallOutcome::InstallerSaved { path, installer })
        }
    }

    fn verify_checksums(
        &self,
        release: &ReleaseInfo,
        asset: &ReleaseAsset,
        path: &Path,
    ) -> Result<(), UpdateError> {
        let Some(checksums) = &release.checksums else {
            return Ok(());
        };
        if !secure_url(&checksums.url) {
            return Err(UpdateError::Network(format!(
                "refusing to download checksums from an insecure URL: {}",
                checksums.url
            )));
        }
        let text = self
            .client()?
            .get(&checksums.url)
            .send()
            .map_err(|error| UpdateError::Network(error.to_string()))?
            .error_for_status()
            .map_err(|error| UpdateError::Network(error.to_string()))?
            .text()
            .map_err(|error| UpdateError::Network(error.to_string()))?;
        let expected = parse_checksum(&text, &asset.name).ok_or_else(|| {
            UpdateError::Checksum(format!(
                "the release checksum manifest does not list {}",
                asset.name
            ))
        })?;
        let actual = sha256_file(path)?;
        if actual.eq_ignore_ascii_case(&expected) {
            Ok(())
        } else {
            Err(UpdateError::Checksum(format!(
                "expected {expected}, downloaded {actual}"
            )))
        }
    }

    fn latest_release(&self) -> Result<Option<Value>, UpdateError> {
        let url = format!(
            "{}/repos/{}/releases/latest",
            self.api_base.trim_end_matches('/'),
            self.repo
        );
        if !secure_url(&url) {
            return Err(UpdateError::Network(format!(
                "refusing to query an insecure update endpoint: {url}"
            )));
        }
        let response = self
            .client()?
            .get(&url)
            .header("Accept", "application/vnd.github+json")
            .send()
            .map_err(|error| UpdateError::Network(error.to_string()))?;
        if response.status() == reqwest::StatusCode::NOT_FOUND {
            return Ok(None);
        }
        if !response.status().is_success() {
            return Err(UpdateError::Network(format!(
                "GitHub returned HTTP {} for {url}",
                response.status()
            )));
        }
        response
            .json()
            .map(Some)
            .map_err(|error| UpdateError::Network(error.to_string()))
    }

    fn client(&self) -> Result<reqwest::blocking::Client, UpdateError> {
        let user_agent = format!("OpenBaD/{}", env!("CARGO_PKG_VERSION"));
        reqwest::blocking::Client::builder()
            .timeout(self.timeout)
            .user_agent(user_agent)
            .build()
            .map_err(|error| UpdateError::Network(error.to_string()))
    }
}

impl ReleaseInfo {
    pub fn parse(value: &Value) -> Result<Self, UpdateError> {
        let tag = value
            .get("tag_name")
            .and_then(Value::as_str)
            .ok_or_else(|| UpdateError::InvalidRelease("missing tag_name".to_owned()))?;
        let version = tag.trim_start_matches(['v', 'V']).to_owned();
        Version::parse(&version).map_err(|error| {
            UpdateError::InvalidRelease(format!("tag '{tag}' is not a version: {error}"))
        })?;
        let assets = value
            .get("assets")
            .and_then(Value::as_array)
            .map(|assets| {
                assets
                    .iter()
                    .filter_map(|asset| {
                        let name = asset.get("name").and_then(Value::as_str)?;
                        let url = asset.get("browser_download_url").and_then(Value::as_str)?;
                        Some(ReleaseAsset {
                            name: name.to_owned(),
                            url: url.to_owned(),
                            size: asset.get("size").and_then(Value::as_u64),
                        })
                    })
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();
        let checksums = assets
            .iter()
            .find(|asset| {
                let name = asset.name.to_ascii_lowercase();
                CHECKSUM_ASSET_NAMES
                    .iter()
                    .any(|candidate| name == *candidate)
            })
            .cloned();
        Ok(Self {
            tag: tag.to_owned(),
            version,
            name: value
                .get("name")
                .and_then(Value::as_str)
                .filter(|name| !name.is_empty())
                .unwrap_or(tag)
                .to_owned(),
            notes: value
                .get("body")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_owned(),
            html_url: value
                .get("html_url")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_owned(),
            published_at: value
                .get("published_at")
                .and_then(Value::as_str)
                .map(str::to_owned),
            assets,
            checksums,
        })
    }
}

/// Picks the best installer asset for the target platform.
#[must_use]
pub fn select_asset(assets: &[ReleaseAsset], target: Target) -> Option<(ReleaseAsset, AssetKind)> {
    let preferences = match target.os {
        Os::Linux => vec![
            (".appimage", AssetKind::AppImage),
            (".deb", AssetKind::Deb),
            (".rpm", AssetKind::Rpm),
        ],
        Os::Windows => vec![(".msi", AssetKind::Msi), (".exe", AssetKind::Exe)],
        Os::Macos => vec![(".dmg", AssetKind::Dmg)],
    };
    let mut best: Option<(usize, usize, &ReleaseAsset, AssetKind)> = None;
    for asset in assets {
        let name = asset.name.to_ascii_lowercase();
        if name.ends_with(".sig")
            || name.ends_with(".sha256")
            || name.ends_with(".sha256sum")
            || name.ends_with(".json")
            || name.ends_with(".txt")
            || name.ends_with(".blockmap")
        {
            continue;
        }
        for (rank, (extension, kind)) in preferences.iter().enumerate() {
            if !name.ends_with(extension) {
                continue;
            }
            if *kind == AssetKind::Exe && !(name.contains("setup") || name.contains("install")) {
                continue;
            }
            let Some(arch_rank) = arch_rank(&name, target.arch) else {
                continue;
            };
            let candidate = (rank, arch_rank, asset, *kind);
            let better = match best {
                None => true,
                Some((best_rank, best_arch, _, _)) => (rank, arch_rank) < (best_rank, best_arch),
            };
            if better {
                best = Some(candidate);
            }
        }
    }
    best.map(|(_, _, asset, kind)| (asset.clone(), kind))
}

fn arch_rank(name: &str, arch: Arch) -> Option<usize> {
    let x86 = name.contains("x86_64")
        || name.contains("amd64")
        || name.contains("x64")
        || name.contains("x86-64");
    let arm = name.contains("aarch64") || name.contains("arm64");
    match arch {
        Arch::X8664 => {
            if x86 {
                Some(0)
            } else if arm {
                None
            } else if name.contains("universal") {
                Some(1)
            } else {
                Some(2)
            }
        }
        Arch::Aarch64 => {
            if arm {
                Some(0)
            } else if x86 {
                None
            } else if name.contains("universal") {
                Some(1)
            } else {
                Some(2)
            }
        }
        Arch::Other => None,
    }
}

fn secure_url(raw: &str) -> bool {
    let Ok(url) = reqwest::Url::parse(raw) else {
        return false;
    };
    match url.scheme() {
        "https" => true,
        "http" => matches!(
            url.host_str(),
            Some("127.0.0.1") | Some("localhost") | Some("[::1]")
        ),
        _ => false,
    }
}

fn safe_file_name(name: &str) -> String {
    let name = name.rsplit(['/', '\\']).next().unwrap_or(name);
    let sanitized = name
        .chars()
        .map(|character| {
            if character.is_ascii_alphanumeric() || "._-+".contains(character) {
                character
            } else {
                '_'
            }
        })
        .collect::<String>();
    if sanitized.is_empty() || sanitized.chars().all(|character| character == '.') {
        "openbad-installer".to_owned()
    } else {
        sanitized
    }
}

fn parse_checksum(manifest: &str, asset_name: &str) -> Option<String> {
    for line in manifest.lines() {
        let mut parts = line.split_whitespace();
        let (Some(hash), Some(file)) = (parts.next(), parts.next()) else {
            continue;
        };
        if hash.len() != 64 || !hash.chars().all(|character| character.is_ascii_hexdigit()) {
            continue;
        }
        let file = file.trim_start_matches('*');
        let file = file.rsplit('/').next().unwrap_or(file);
        if file == asset_name {
            return Some(hash.to_ascii_lowercase());
        }
    }
    None
}

fn sha256_file(path: &Path) -> Result<String, UpdateError> {
    let mut file = fs::File::open(path)?;
    let mut hasher = Sha256::new();
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        let read = file.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
    }
    Ok(format!("{:x}", hasher.finalize()))
}

fn stage_installer(
    downloaded: &Path,
    downloads: &Path,
    asset: &ReleaseAsset,
) -> Result<PathBuf, UpdateError> {
    fs::create_dir_all(downloads)?;
    let destination = downloads.join(safe_file_name(&asset.name));
    if downloaded == destination {
        return Ok(destination);
    }
    if destination.exists() {
        fs::remove_file(&destination)?;
    }
    fs::copy(downloaded, &destination)?;
    Ok(destination)
}

#[cfg(unix)]
fn replace_app_image(downloaded: &Path, app_image: &Path) -> Result<(), UpdateError> {
    use std::os::unix::fs::PermissionsExt;

    let directory = app_image.parent().ok_or_else(|| {
        UpdateError::Io(std::io::Error::other(
            "the running AppImage has no parent directory",
        ))
    })?;
    let file_name = app_image
        .file_name()
        .and_then(|name| name.to_str())
        .ok_or_else(|| {
            UpdateError::Io(std::io::Error::other(
                "the running AppImage has no valid file name",
            ))
        })?;
    let staging = directory.join(format!(".{file_name}.new"));
    fs::copy(downloaded, &staging)?;
    fs::set_permissions(&staging, fs::Permissions::from_mode(0o755))?;
    fs::rename(&staging, app_image)?;
    Ok(())
}

#[cfg(not(unix))]
fn replace_app_image(downloaded: &Path, app_image: &Path) -> Result<(), UpdateError> {
    fs::copy(downloaded, app_image)?;
    Ok(())
}

fn launch_installer(path: &Path) -> bool {
    #[cfg(target_os = "linux")]
    {
        std::process::Command::new("xdg-open")
            .arg(path)
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()
            .is_ok()
    }
    #[cfg(target_os = "macos")]
    {
        std::process::Command::new("open")
            .arg(path)
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()
            .is_ok()
    }
    #[cfg(target_os = "windows")]
    {
        std::process::Command::new("cmd")
            .args(["/C", "start", ""])
            .arg(path)
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()
            .is_ok()
    }
    #[cfg(not(any(target_os = "linux", target_os = "macos", target_os = "windows")))]
    {
        let _ = path;
        false
    }
}

/// Returns the user's downloads directory, falling back to the temp directory.
#[must_use]
pub fn downloads_directory() -> PathBuf {
    directories::UserDirs::new()
        .and_then(|dirs| dirs.download_dir().map(Path::to_path_buf))
        .unwrap_or_else(std::env::temp_dir)
}

/// Returns the AppImage currently being executed, when the app was packaged as
/// an AppImage.
#[must_use]
pub fn running_app_image() -> Option<PathBuf> {
    std::env::var_os("APPIMAGE")
        .map(PathBuf::from)
        .filter(|path| path.is_file())
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    fn release_json(tag: &str, assets: &[(&str, u64)]) -> Value {
        serde_json::json!({
            "tag_name": tag,
            "name": format!("OpenBaD {tag}"),
            "body": "Release notes",
            "html_url": "https://github.com/bruceamoser/OpenBaD/releases/tag/v0.2.0",
            "published_at": "2026-10-01T00:00:00Z",
            "assets": assets.iter().map(|(name, size)| serde_json::json!({
                "name": name,
                "browser_download_url": format!("https://example.com/{name}"),
                "size": size
            })).collect::<Vec<_>>()
        })
    }

    fn linux_x64() -> Target {
        Target {
            os: Os::Linux,
            arch: Arch::X8664,
        }
    }

    #[test]
    fn up_to_date_when_the_latest_version_is_not_newer() {
        let service = UpdateService::new(DEFAULT_REPO).with_target(linux_x64());
        let value = release_json("v0.2.0", &[("OpenBaD_0.2.0_amd64.AppImage", 10)]);
        let status = service.check_release_json("0.2.0", &value).expect("check");
        assert!(matches!(status, UpdateStatus::UpToDate { .. }));
        let status = service.check_release_json("0.3.0", &value).expect("check");
        assert!(matches!(status, UpdateStatus::UpToDate { .. }));
    }

    #[test]
    fn available_when_a_newer_release_has_a_matching_asset() {
        let service = UpdateService::new(DEFAULT_REPO).with_target(linux_x64());
        let value = release_json(
            "v0.3.0",
            &[
                ("OpenBaD_0.3.0_amd64.deb", 20),
                ("OpenBaD_0.3.0_amd64.AppImage", 10),
                ("OpenBaD_0.3.0_aarch64.AppImage", 11),
                ("SHA256SUMS", 4),
            ],
        );
        let status = service.check_release_json("0.2.0", &value).expect("check");
        let UpdateStatus::Available {
            latest,
            asset,
            installer,
            release,
            ..
        } = status
        else {
            panic!("expected an available update");
        };
        assert_eq!(latest, "0.3.0");
        assert_eq!(asset.name, "OpenBaD_0.3.0_amd64.AppImage");
        assert_eq!(installer, AssetKind::AppImage);
        assert_eq!(release.notes, "Release notes");
        assert_eq!(release.checksums.expect("checksums").name, "SHA256SUMS");
    }

    #[test]
    fn asset_selection_respects_platform_and_architecture() {
        let assets = vec![
            ReleaseAsset {
                name: "OpenBaD_0.3.0_aarch64.AppImage".to_owned(),
                url: String::new(),
                size: None,
            },
            ReleaseAsset {
                name: "OpenBaD_0.3.0_x86_64.dmg".to_owned(),
                url: String::new(),
                size: None,
            },
            ReleaseAsset {
                name: "OpenBaD_0.3.0_aarch64.dmg".to_owned(),
                url: String::new(),
                size: None,
            },
        ];
        let mac_arm = Target {
            os: Os::Macos,
            arch: Arch::Aarch64,
        };
        let (asset, kind) = select_asset(&assets, mac_arm).expect("mac asset");
        assert_eq!(asset.name, "OpenBaD_0.3.0_aarch64.dmg");
        assert_eq!(kind, AssetKind::Dmg);
        assert!(select_asset(&assets, linux_x64()).is_none());
        assert!(
            select_asset(
                &assets,
                Target {
                    os: Os::Linux,
                    arch: Arch::Other,
                }
            )
            .is_none()
        );
    }

    #[test]
    fn windows_prefers_msi_and_requires_setup_executables() {
        let assets = vec![
            ReleaseAsset {
                name: "OpenBaD_0.3.0_x64-setup.exe".to_owned(),
                url: String::new(),
                size: None,
            },
            ReleaseAsset {
                name: "OpenBaD_0.3.0_x64_en-US.msi".to_owned(),
                url: String::new(),
                size: None,
            },
            ReleaseAsset {
                name: "OpenBaD_0.3.0_x64.zip".to_owned(),
                url: String::new(),
                size: None,
            },
        ];
        let target = Target {
            os: Os::Windows,
            arch: Arch::X8664,
        };
        let (asset, kind) = select_asset(&assets, target).expect("windows asset");
        assert_eq!(asset.name, "OpenBaD_0.3.0_x64_en-US.msi");
        assert_eq!(kind, AssetKind::Msi);
        assert!(select_asset(&assets[..1], target).is_some());
        assert!(select_asset(&assets[2..], target).is_none());
    }

    #[test]
    fn invalid_release_metadata_is_rejected() {
        let value = serde_json::json!({"name": "no tag"});
        assert!(matches!(
            ReleaseInfo::parse(&value),
            Err(UpdateError::InvalidRelease(_))
        ));
        let value = serde_json::json!({"tag_name": "nightly"});
        assert!(matches!(
            ReleaseInfo::parse(&value),
            Err(UpdateError::InvalidRelease(_))
        ));
    }

    #[test]
    fn checksum_manifests_are_parsed() {
        let hash = "a".repeat(64);
        let manifest = format!(
            "{}  other.AppImage\n{hash} *OpenBaD_0.3.0_amd64.AppImage\n",
            "b".repeat(64)
        );
        assert_eq!(
            parse_checksum(&manifest, "OpenBaD_0.3.0_amd64.AppImage"),
            Some(hash)
        );
        assert!(parse_checksum(&manifest, "missing.AppImage").is_none());
    }

    fn serve_once(status: &'static str, body: String) -> String {
        use std::io::{Read, Write};
        use std::net::TcpListener;

        let listener = TcpListener::bind("127.0.0.1:0").expect("listener");
        let address = listener.local_addr().expect("address");
        std::thread::spawn(move || {
            if let Ok((mut stream, _)) = listener.accept() {
                let mut buffer = [0_u8; 2048];
                let _ = stream.read(&mut buffer);
                let response = format!(
                    "HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                );
                let _ = stream.write_all(response.as_bytes());
            }
        });
        format!("http://127.0.0.1:{}", address.port())
    }

    #[test]
    fn checks_a_local_release_endpoint() {
        let body = release_json("v0.3.0", &[("OpenBaD_0.3.0_amd64.AppImage", 10)]).to_string();
        let api_base = serve_once("200 OK", body);
        let service = UpdateService::new(DEFAULT_REPO)
            .with_api_base(api_base)
            .with_target(linux_x64());
        let status = service.check("0.2.0").expect("check");
        let UpdateStatus::Available { latest, asset, .. } = status else {
            panic!("expected an available update");
        };
        assert_eq!(latest, "0.3.0");
        assert_eq!(asset.name, "OpenBaD_0.3.0_amd64.AppImage");
    }

    #[test]
    fn missing_release_is_reported_as_no_release() {
        let api_base = serve_once("404 Not Found", String::new());
        let service = UpdateService::new(DEFAULT_REPO)
            .with_api_base(api_base)
            .with_target(linux_x64());
        let status = service.check("0.2.0").expect("check");
        assert!(matches!(status, UpdateStatus::NoRelease { .. }));
    }

    #[test]
    fn update_urls_must_use_tls_except_on_loopback() {
        assert!(secure_url("https://github.com/bruceamoser/OpenBaD"));
        assert!(secure_url("http://127.0.0.1:8080/releases/latest"));
        assert!(secure_url("http://localhost:8080/releases/latest"));
        assert!(!secure_url("http://example.com/releases/latest"));
        assert!(!secure_url("ftp://example.com/asset"));
        assert!(!secure_url("not a url"));
    }

    #[test]
    fn file_names_are_sanitized() {
        assert_eq!(safe_file_name("../../evil.AppImage"), "evil.AppImage");
        assert_eq!(safe_file_name("a b?.deb"), "a_b_.deb");
        assert_eq!(safe_file_name(""), "openbad-installer");
        assert_eq!(safe_file_name(".."), "openbad-installer");
        assert_eq!(safe_file_name("."), "openbad-installer");
    }

    #[cfg(unix)]
    #[test]
    fn app_images_are_replaced_atomically() {
        use std::os::unix::fs::PermissionsExt;

        let root = tempdir().expect("tempdir");
        let running = root.path().join("OpenBaD.AppImage");
        fs::write(&running, "old").expect("old");
        let downloaded = root.path().join("OpenBaD_0.3.0_amd64.AppImage");
        fs::write(&downloaded, "new").expect("new");
        let asset = ReleaseAsset {
            name: "OpenBaD_0.3.0_amd64.AppImage".to_owned(),
            url: String::new(),
            size: None,
        };
        let service = UpdateService::new(DEFAULT_REPO);
        let outcome = service
            .install(
                &asset,
                AssetKind::AppImage,
                &downloaded,
                Some(&running),
                &root.path().join("Downloads"),
            )
            .expect("install");
        assert_eq!(
            outcome,
            InstallOutcome::AppImageReplaced {
                path: running.clone()
            }
        );
        assert_eq!(fs::read_to_string(&running).expect("read"), "new");
        let mode = fs::metadata(&running)
            .expect("metadata")
            .permissions()
            .mode();
        assert_eq!(mode & 0o111, 0o111);
        assert!(!root.path().join(".OpenBaD.AppImage.new").exists());
    }

    #[test]
    fn installers_are_staged_in_the_downloads_directory() {
        let root = tempdir().expect("tempdir");
        let downloaded = root.path().join("tmp").join("OpenBaD_0.3.0_amd64.deb");
        fs::create_dir_all(downloaded.parent().expect("parent")).expect("dir");
        fs::write(&downloaded, "package").expect("package");
        let downloads = root.path().join("Downloads");
        let asset = ReleaseAsset {
            name: "OpenBaD_0.3.0_amd64.deb".to_owned(),
            url: String::new(),
            size: None,
        };
        let path = stage_installer(&downloaded, &downloads, &asset).expect("stage");
        assert_eq!(path, downloads.join("OpenBaD_0.3.0_amd64.deb"));
        assert_eq!(fs::read_to_string(path).expect("read"), "package");
    }
}
