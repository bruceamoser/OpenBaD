//! Portable project bundles.
//!
//! A bundle is a single zip file containing:
//!
//! - `openbad-project.json` — manifest with format version, app version, the
//!   project name, and counts.
//! - `state/workspace.json` — chat sessions (events, messages, turns),
//!   memories, knowledge documents/chunks, and file checkpoints.
//! - `workspace/...` — every workspace file worth carrying: source, `.openbad`
//!   metadata, and `.git`. Rebuildable output (`node_modules`, `target`,
//!   virtualenvs, caches) and `.gitignore`d paths are skipped.
//!
//! Reference folders are intentionally excluded: their absolute paths belong
//! to the exporting machine. Secrets live in the OS keychain and are never part
//! of a bundle.

use std::fs::{self, File};
use std::io::{Read, Write};
use std::path::{Component, Path, PathBuf};

use ignore::WalkBuilder;
use openbad_core::WorkspaceId;
use openbad_store::Store;
use openbad_store::transfer::{ImportCounts, WorkspaceState};
use serde::{Deserialize, Serialize};
use zip::write::SimpleFileOptions;
use zip::{CompressionMethod, ZipArchive, ZipWriter};

pub const BUNDLE_FORMAT_VERSION: u32 = 1;
pub const BUNDLE_EXTENSION: &str = "openbad-project";
pub const MANIFEST_NAME: &str = "openbad-project.json";
pub const STATE_NAME: &str = "state/workspace.json";
pub const WORKSPACE_PREFIX: &str = "workspace/";

const MAX_MANIFEST_BYTES: u64 = 1024 * 1024;
const MAX_STATE_BYTES: u64 = 512 * 1024 * 1024;

#[derive(Debug, thiserror::Error)]
pub enum TransferError {
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
    #[error("zip error: {0}")]
    Zip(#[from] zip::result::ZipError),
    #[error("store error: {0}")]
    Store(#[from] openbad_store::StoreError),
    #[error("invalid bundle: {0}")]
    InvalidBundle(String),
    #[error(
        "this bundle uses format version {found}; this build supports up to {supported}. Update OpenBaD to import it"
    )]
    UnsupportedVersion { found: u32, supported: u32 },
    #[error("the destination must be empty or not exist yet: {0}")]
    DestinationNotEmpty(String),
    #[error("bundle payload is too large: {0}")]
    TooLarge(String),
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BundleManifest {
    pub format_version: u32,
    pub app_version: String,
    pub project_name: String,
    pub exported_at: i64,
    #[serde(default)]
    pub counts: BundleCounts,
    #[serde(default)]
    pub warnings: Vec<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct BundleCounts {
    pub files: u64,
    pub bytes: u64,
    pub sessions: u64,
    pub memories: u64,
    pub knowledge_documents: u64,
    pub checkpoints: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ExportReport {
    pub path: String,
    pub project_name: String,
    pub files: u64,
    pub bytes: u64,
    pub sessions: u64,
    pub memories: u64,
    pub knowledge_documents: u64,
    pub checkpoints: u64,
    pub warnings: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ImportReport {
    pub path: String,
    pub project_name: String,
    pub files: u64,
    pub sessions: u64,
    pub memories: u64,
    pub knowledge_documents: u64,
    pub checkpoints: u64,
    pub warnings: Vec<String>,
}

struct WalkEntry {
    absolute: PathBuf,
    relative: String,
    size: u64,
    mode: Option<u32>,
}

/// Exports a project into a single bundle. The archive is written to a
/// temporary file next to the destination and renamed into place.
pub fn export_project(
    store: &Store,
    workspace_id: WorkspaceId,
    destination: &Path,
    app_version: &str,
) -> Result<ExportReport, TransferError> {
    let workspace = store
        .workspace_summary(workspace_id)?
        .ok_or_else(|| TransferError::InvalidBundle("workspace is not registered".to_owned()))?;
    let root = PathBuf::from(&workspace.path);
    if !root.is_dir() {
        return Err(TransferError::InvalidBundle(format!(
            "workspace directory is missing: {}",
            workspace.path
        )));
    }
    let state = store.export_workspace_state(workspace_id)?;
    let mut warnings = vec![
        "Reference folders are not included; re-add them on the new machine.".to_owned(),
        "The model API key is not included; enter it in Settings after importing.".to_owned(),
    ];
    let (entries, skipped_symlinks) = collect_workspace_files(&root, destination, &mut warnings)?;
    if skipped_symlinks > 0 {
        warnings.push(format!("Skipped {skipped_symlinks} symbolic links."));
    }
    let bytes = entries.iter().map(|entry| entry.size).sum::<u64>();
    let counts = BundleCounts {
        files: entries.len() as u64,
        bytes,
        sessions: state.sessions.len() as u64,
        memories: state.memories.len() as u64,
        knowledge_documents: state.knowledge.len() as u64,
        checkpoints: state.checkpoints.len() as u64,
    };
    let manifest = BundleManifest {
        format_version: BUNDLE_FORMAT_VERSION,
        app_version: app_version.to_owned(),
        project_name: workspace.name.clone(),
        exported_at: now_millis(),
        counts: counts.clone(),
        warnings: warnings.clone(),
    };

    if let Some(parent) = destination.parent() {
        fs::create_dir_all(parent)?;
    }
    let temporary = destination.with_file_name(format!(
        ".{}.{}.partial",
        destination
            .file_name()
            .and_then(|name| name.to_str())
            .unwrap_or(BUNDLE_EXTENSION),
        std::process::id()
    ));
    let file = File::create(&temporary)?;
    let mut zip = ZipWriter::new(file);
    zip.start_file(MANIFEST_NAME, SimpleFileOptions::default())?;
    zip.write_all(
        serde_json::to_string_pretty(&manifest)
            .map_err(|error| TransferError::InvalidBundle(error.to_string()))?
            .as_bytes(),
    )?;
    zip.start_file(
        STATE_NAME,
        SimpleFileOptions::default().compression_method(CompressionMethod::Deflated),
    )?;
    zip.write_all(
        &serde_json::to_vec(&state)
            .map_err(|error| TransferError::InvalidBundle(error.to_string()))?,
    )?;
    for entry in &entries {
        let mut options =
            SimpleFileOptions::default().compression_method(compression_for(&entry.relative));
        if let Some(mode) = entry.mode {
            options = options.unix_permissions(mode);
        }
        zip.start_file(format!("{WORKSPACE_PREFIX}{}", entry.relative), options)?;
        let mut source = File::open(&entry.absolute)?;
        std::io::copy(&mut source, &mut zip)?;
    }
    zip.finish()?;

    if destination.exists() {
        fs::remove_file(destination)?;
    }
    fs::rename(&temporary, destination)?;

    Ok(ExportReport {
        path: destination.to_string_lossy().into_owned(),
        project_name: workspace.name,
        files: counts.files,
        bytes: counts.bytes,
        sessions: counts.sessions,
        memories: counts.memories,
        knowledge_documents: counts.knowledge_documents,
        checkpoints: counts.checkpoints,
        warnings,
    })
}

/// Imports a bundle into a brand-new project directory.
pub fn import_project(
    store: &Store,
    bundle: &Path,
    destination: &Path,
) -> Result<ImportReport, TransferError> {
    ensure_destination_is_free(destination)?;
    let manifest = read_manifest(bundle)?;
    let state = read_state(bundle)?;
    let imported: Result<(u64, ImportCounts), TransferError> = (|| {
        fs::create_dir_all(destination)?;
        let files = extract_workspace(bundle, destination)?;
        let workspace_id =
            store.create_workspace(&manifest.project_name, &path_string(destination))?;
        let counts = store.import_workspace_state(workspace_id, state)?;
        Ok((files, counts))
    })();
    let (files, counts) = match imported {
        Ok(imported) => imported,
        Err(error) => {
            // The destination was empty before this import, so a failed import
            // must not leave a half-restored project behind.
            let _ = fs::remove_dir_all(destination);
            return Err(error);
        }
    };
    Ok(build_import_report(destination, &manifest, files, counts))
}

/// Imports a bundle into an existing project: conflicting files are
/// overwritten, local-only files are kept, and chats are appended.
pub fn import_project_merge(
    store: &Store,
    workspace_id: WorkspaceId,
    bundle: &Path,
) -> Result<ImportReport, TransferError> {
    let manifest = read_manifest(bundle)?;
    let state = read_state(bundle)?;
    let workspace = store
        .workspace_summary(workspace_id)?
        .ok_or_else(|| TransferError::InvalidBundle("workspace is not registered".to_owned()))?;
    let destination = PathBuf::from(&workspace.path);
    if !destination.is_dir() {
        return Err(TransferError::InvalidBundle(format!(
            "workspace directory is missing: {}",
            workspace.path
        )));
    }
    let files = extract_workspace(bundle, &destination)?;
    let counts = store.import_workspace_state(workspace_id, state)?;
    Ok(build_import_report(&destination, &manifest, files, counts))
}

fn build_import_report(
    destination: &Path,
    manifest: &BundleManifest,
    files: u64,
    counts: ImportCounts,
) -> ImportReport {
    let mut warnings = vec![
        "Reference folders are not part of the bundle; re-add them for this machine.".to_owned(),
        "The model API key is not part of the bundle; enter it in Settings.".to_owned(),
    ];
    warnings.extend(manifest.warnings.iter().cloned());
    ImportReport {
        path: destination.to_string_lossy().into_owned(),
        project_name: manifest.project_name.clone(),
        files,
        sessions: counts.sessions,
        memories: counts.memories,
        knowledge_documents: counts.knowledge_documents,
        checkpoints: counts.checkpoints,
        warnings,
    }
}

fn ensure_destination_is_free(destination: &Path) -> Result<(), TransferError> {
    if !destination.exists() {
        return Ok(());
    }
    if !destination.is_dir() {
        return Err(TransferError::DestinationNotEmpty(path_string(destination)));
    }
    let empty = fs::read_dir(destination)?.next().is_none();
    if empty {
        Ok(())
    } else {
        Err(TransferError::DestinationNotEmpty(path_string(destination)))
    }
}

/// Reads a bundle manifest without importing it, so the UI can preview the
/// project name and version.
pub fn bundle_manifest(bundle: &Path) -> Result<BundleManifest, TransferError> {
    read_manifest(bundle)
}

/// Turns a project name into a portable directory name.
#[must_use]
pub fn safe_project_directory_name(name: &str) -> String {
    let cleaned: String = name
        .trim()
        .chars()
        .map(|character| {
            if character.is_control()
                || matches!(
                    character,
                    '/' | '\\' | ':' | '*' | '?' | '"' | '<' | '>' | '|'
                )
            {
                '-'
            } else {
                character
            }
        })
        .collect();
    let cleaned = cleaned.trim_matches(['.', ' ']).to_owned();
    if cleaned.is_empty() {
        "imported-project".to_owned()
    } else {
        cleaned
    }
}

fn read_manifest(bundle: &Path) -> Result<BundleManifest, TransferError> {
    let mut archive = open_bundle(bundle)?;
    let mut entry = archive
        .by_name(MANIFEST_NAME)
        .map_err(|_| TransferError::InvalidBundle("manifest is missing".to_owned()))?;
    let bytes = read_entry(&mut entry, MAX_MANIFEST_BYTES, "manifest")?;
    let manifest: BundleManifest = serde_json::from_slice(&bytes)
        .map_err(|error| TransferError::InvalidBundle(format!("manifest is invalid: {error}")))?;
    if manifest.format_version > BUNDLE_FORMAT_VERSION {
        return Err(TransferError::UnsupportedVersion {
            found: manifest.format_version,
            supported: BUNDLE_FORMAT_VERSION,
        });
    }
    if manifest.project_name.trim().is_empty() {
        return Err(TransferError::InvalidBundle(
            "manifest has no project name".to_owned(),
        ));
    }
    Ok(manifest)
}

fn read_state(bundle: &Path) -> Result<WorkspaceState, TransferError> {
    let mut archive = open_bundle(bundle)?;
    let mut entry = archive
        .by_name(STATE_NAME)
        .map_err(|_| TransferError::InvalidBundle("project state is missing".to_owned()))?;
    let bytes = read_entry(&mut entry, MAX_STATE_BYTES, "project state")?;
    serde_json::from_slice(&bytes)
        .map_err(|error| TransferError::InvalidBundle(format!("project state is invalid: {error}")))
}

fn open_bundle(bundle: &Path) -> Result<ZipArchive<File>, TransferError> {
    let file = File::open(bundle)?;
    Ok(ZipArchive::new(file)?)
}

fn read_entry(entry: &mut impl Read, limit: u64, label: &str) -> Result<Vec<u8>, TransferError> {
    let mut bytes = Vec::new();
    entry
        .take(limit + 1)
        .read_to_end(&mut bytes)
        .map_err(|error| {
            TransferError::InvalidBundle(format!("could not read {label}: {error}"))
        })?;
    if bytes.len() as u64 > limit {
        return Err(TransferError::TooLarge(label.to_owned()));
    }
    Ok(bytes)
}

fn extract_workspace(bundle: &Path, destination: &Path) -> Result<u64, TransferError> {
    let mut archive = open_bundle(bundle)?;
    let mut files = 0_u64;
    for index in 0..archive.len() {
        let mut entry = archive.by_index(index)?;
        let name = entry.name().to_owned();
        let Some(relative) = name.strip_prefix(WORKSPACE_PREFIX) else {
            continue;
        };
        let relative = safe_relative_path(relative)
            .ok_or_else(|| TransferError::InvalidBundle(format!("unsafe archive path: {name}")))?;
        let target = destination.join(&relative);
        if entry.is_dir() {
            fs::create_dir_all(&target)?;
            continue;
        }
        if let Some(parent) = target.parent() {
            fs::create_dir_all(parent)?;
        }
        let mut output = File::create(&target)?;
        std::io::copy(&mut entry, &mut output)?;
        drop(output);
        set_permissions(&target, entry.unix_mode())?;
        files += 1;
    }
    Ok(files)
}

fn safe_relative_path(name: &str) -> Option<PathBuf> {
    if name.is_empty() || name.starts_with('/') || name.contains('\\') {
        return None;
    }
    let mut path = PathBuf::new();
    for component in Path::new(name).components() {
        match component {
            Component::Normal(part) => path.push(part),
            Component::CurDir => {}
            _ => return None,
        }
    }
    (!path.as_os_str().is_empty()).then_some(path)
}

#[cfg(unix)]
fn set_permissions(path: &Path, mode: Option<u32>) -> Result<(), TransferError> {
    use std::os::unix::fs::PermissionsExt as _;
    if let Some(mode) = mode {
        fs::set_permissions(path, fs::Permissions::from_mode(mode & 0o777))?;
    }
    Ok(())
}

#[cfg(not(unix))]
fn set_permissions(_path: &Path, _mode: Option<u32>) -> Result<(), TransferError> {
    Ok(())
}

fn collect_workspace_files(
    root: &Path,
    destination: &Path,
    warnings: &mut Vec<String>,
) -> Result<(Vec<WalkEntry>, u64), TransferError> {
    let destination = absolute_lexical(destination);
    let mut entries = Vec::new();
    let mut skipped_symlinks = 0;
    let mut builder = WalkBuilder::new(root);
    builder
        .hidden(false)
        .parents(false)
        .git_ignore(true)
        .git_global(false)
        .git_exclude(true)
        .require_git(false)
        .follow_links(false)
        .filter_entry(|entry| {
            if entry.depth() == 0 {
                return true;
            }
            let name = entry.file_name().to_string_lossy();
            if name == ".git" || name == ".openbad" {
                return true;
            }
            if entry.file_type().is_some_and(|kind| kind.is_dir()) {
                !is_rebuildable_dir(&name)
            } else {
                !is_ignorable_file(&name)
            }
        });
    for result in builder.build() {
        let entry = match result {
            Ok(entry) => entry,
            Err(error) => {
                warnings.push(format!("Skipped unreadable path: {error}"));
                continue;
            }
        };
        if entry.depth() == 0 || entry.file_type().is_some_and(|kind| kind.is_dir()) {
            continue;
        }
        if entry.path_is_symlink() {
            skipped_symlinks += 1;
            continue;
        }
        if absolute_lexical(entry.path()) == destination {
            continue;
        }
        let relative = entry
            .path()
            .strip_prefix(root)
            .map_err(|_| TransferError::InvalidBundle("path escaped the workspace".to_owned()))?;
        let Some(relative) = relative.to_str() else {
            warnings.push("Skipped a file with a non-UTF-8 name.".to_owned());
            continue;
        };
        let metadata = match entry.metadata() {
            Ok(metadata) => metadata,
            Err(error) => {
                warnings.push(format!("Skipped {relative}: {error}"));
                continue;
            }
        };
        entries.push(WalkEntry {
            absolute: entry.path().to_path_buf(),
            relative: relative.replace('\\', "/"),
            size: metadata.len(),
            mode: mode_of(&metadata),
        });
    }
    entries.sort_by(|left, right| left.relative.cmp(&right.relative));
    Ok((entries, skipped_symlinks))
}

#[cfg(unix)]
fn mode_of(metadata: &fs::Metadata) -> Option<u32> {
    use std::os::unix::fs::PermissionsExt as _;
    Some(metadata.permissions().mode())
}

#[cfg(not(unix))]
fn mode_of(_metadata: &fs::Metadata) -> Option<u32> {
    None
}

fn absolute_lexical(path: &Path) -> PathBuf {
    if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()
            .map(|cwd| cwd.join(path))
            .unwrap_or_else(|_| path.to_path_buf())
    }
}

fn compression_for(relative: &str) -> CompressionMethod {
    let extension = Path::new(relative)
        .extension()
        .and_then(|extension| extension.to_str())
        .unwrap_or_default()
        .to_lowercase();
    if matches!(
        extension.as_str(),
        "mp4"
            | "m4v"
            | "mov"
            | "webm"
            | "ogv"
            | "mp3"
            | "m4a"
            | "aac"
            | "flac"
            | "ogg"
            | "opus"
            | "png"
            | "jpg"
            | "jpeg"
            | "gif"
            | "webp"
            | "avif"
            | "zip"
            | "gz"
            | "bz2"
            | "xz"
            | "zst"
            | "7z"
            | "jar"
            | "pdf"
    ) {
        CompressionMethod::Stored
    } else {
        CompressionMethod::Deflated
    }
}

fn is_rebuildable_dir(name: &str) -> bool {
    matches!(
        name,
        "node_modules"
            | "target"
            | ".venv"
            | "venv"
            | "__pycache__"
            | ".pytest_cache"
            | ".mypy_cache"
            | ".ruff_cache"
            | ".next"
            | ".nuxt"
            | ".svelte-kit"
            | ".turbo"
            | ".parcel-cache"
            | ".cache"
            | ".gradle"
            | ".pnpm-store"
            | ".yarn"
            | "coverage"
            | ".nyc_output"
            | ".terraform"
            | "Pods"
            | "DerivedData"
    )
}

fn is_ignorable_file(name: &str) -> bool {
    matches!(name, ".DS_Store" | "Thumbs.db" | "desktop.ini")
        || name.ends_with(".pyc")
        || name.ends_with(".pyo")
        || name.ends_with(".swp")
        || name.ends_with(".swo")
}

fn path_string(path: &Path) -> String {
    path.to_string_lossy().into_owned()
}

fn now_millis() -> i64 {
    use std::time::{SystemTime, UNIX_EPOCH};
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |duration| duration.as_millis() as i64)
}

#[cfg(test)]
mod tests {
    use super::*;
    use openbad_store::transfer::WorkspaceState;
    use tempfile::tempdir;

    fn workspace_with_files() -> (tempfile::TempDir, Store, WorkspaceId) {
        let root = tempdir().expect("workspace");
        fs::create_dir_all(root.path().join("src")).expect("src");
        fs::write(root.path().join("src/main.rs"), "fn main() {}").expect("file");
        fs::create_dir_all(root.path().join(".git/objects")).expect("git");
        fs::write(root.path().join(".git/config"), "[core]").expect("git config");
        fs::create_dir_all(root.path().join(".openbad")).expect("openbad");
        fs::write(root.path().join(".openbad/plan.json"), "{}").expect("plan");
        fs::create_dir_all(root.path().join("node_modules/pkg")).expect("deps");
        fs::write(root.path().join("node_modules/pkg/index.js"), "x").expect("dep");
        fs::create_dir_all(root.path().join("target/debug")).expect("target");
        fs::write(root.path().join("target/debug/app"), "bin").expect("bin");
        fs::write(root.path().join(".gitignore"), "generated/\n").expect("gitignore");
        fs::create_dir_all(root.path().join("generated")).expect("generated dir");
        fs::write(root.path().join("generated/out.txt"), "ignored").expect("ignored");
        let store = Store::open_in_memory().expect("store");
        let workspace = store
            .create_workspace("demo", &path_string(root.path()))
            .expect("workspace row");
        (root, store, workspace)
    }

    #[test]
    fn export_skips_rebuildable_paths_and_keeps_project_state() {
        let (root, store, workspace) = workspace_with_files();
        store
            .remember_memory(workspace, "remember me", Vec::new(), false)
            .expect("memory");
        let session = store
            .create_session(Some(workspace), Some("chat"))
            .expect("session");
        store
            .append_event(
                session,
                openbad_core::event::NewEvent::new(
                    openbad_core::event::SessionEventKind::TurnStart,
                )
                .with_turn(1),
            )
            .expect("event");
        let bundle = root.path().join("export.openbad-project");
        let report = export_project(&store, workspace, &bundle, "0.1.4").expect("export");
        assert!(bundle.is_file());
        assert_eq!(report.files, 4, "source, git config, plan, gitignore");

        let mut archive = ZipArchive::new(File::open(&bundle).expect("bundle")).expect("zip");
        let names = (0..archive.len())
            .map(|index| archive.by_index(index).expect("entry").name().to_owned())
            .collect::<Vec<_>>();
        assert!(names.iter().any(|name| name == "workspace/src/main.rs"));
        assert!(names.iter().any(|name| name == "workspace/.git/config"));
        assert!(
            names
                .iter()
                .any(|name| name == "workspace/.openbad/plan.json")
        );
        assert!(names.iter().any(|name| name == MANIFEST_NAME));
        assert!(names.iter().any(|name| name == STATE_NAME));
        assert!(!names.iter().any(|name| name.contains("node_modules")));
        assert!(!names.iter().any(|name| name.contains("target/")));
        assert!(!names.iter().any(|name| name.contains("generated")));
    }

    #[test]
    fn import_creates_a_project_and_restores_state() {
        let (root, store, workspace) = workspace_with_files();
        store
            .remember_memory(workspace, "remember me", Vec::new(), false)
            .expect("memory");
        store
            .add_knowledge_document(workspace, "Notes", "note", None, Vec::new(), "hello")
            .expect("knowledge");
        let bundle = root.path().join("export.openbad-project");
        export_project(&store, workspace, &bundle, "0.1.4").expect("export");

        let target_root = tempdir().expect("target");
        let destination = target_root.path().join("imported");
        let report = import_project(&store, &bundle, &destination).expect("import");
        assert_eq!(report.files, 4);
        assert_eq!(report.memories, 1);
        assert_eq!(report.knowledge_documents, 1);
        assert!(destination.join("src/main.rs").is_file());
        assert!(destination.join(".git/config").is_file());
        assert_eq!(
            fs::read_to_string(destination.join("src/main.rs")).expect("read"),
            "fn main() {}"
        );
        assert!(
            store
                .list_workspaces()
                .expect("workspaces")
                .iter()
                .any(|workspace| workspace.path == path_string(&destination))
        );
    }

    #[test]
    fn failed_import_removes_the_partial_directory() {
        let (root, store, workspace) = workspace_with_files();
        let bundle = root.path().join("export.openbad-project");
        export_project(&store, workspace, &bundle, "0.1.4").expect("export");

        let parent = tempdir().expect("parent");
        let destination = parent.path().join("taken");
        store
            .create_workspace("taken", &path_string(&destination))
            .expect("registered path");

        import_project(&store, &bundle, &destination).expect_err("path already registered");
        assert!(
            !destination.exists(),
            "a failed import must not leave a half-restored project"
        );
    }

    #[test]
    fn merge_overwrites_files_keeps_local_extras_and_appends_chats() {
        let (root, store, workspace) = workspace_with_files();
        let bundle = root.path().join("export.openbad-project");
        export_project(&store, workspace, &bundle, "0.1.4").expect("export");

        fs::write(root.path().join("src/main.rs"), "// local change").expect("local edit");
        fs::write(root.path().join("local-only.txt"), "keep me").expect("local file");
        let report = import_project_merge(&store, workspace, &bundle).expect("merge");
        assert_eq!(report.files, 4);
        assert_eq!(
            fs::read_to_string(root.path().join("src/main.rs")).expect("read"),
            "fn main() {}"
        );
        assert_eq!(
            fs::read_to_string(root.path().join("local-only.txt")).expect("read"),
            "keep me"
        );
    }

    #[test]
    fn merge_appends_chats_once_and_keeps_state_idempotent() {
        let (root, store, source) = workspace_with_files();
        let session = store
            .create_session(Some(source), Some("carried chat"))
            .expect("session");
        store
            .append_event(
                session,
                openbad_core::event::NewEvent::new(
                    openbad_core::event::SessionEventKind::TurnStart,
                )
                .with_turn(1),
            )
            .expect("event");
        store
            .remember_memory(source, "carry me", Vec::new(), false)
            .expect("memory");
        let bundle = root.path().join("export.openbad-project");
        export_project(&store, source, &bundle, "0.1.4").expect("export");

        let target_root = tempdir().expect("target");
        fs::create_dir_all(target_root.path().join("src")).expect("src");
        fs::write(target_root.path().join("src/main.rs"), "// local").expect("file");
        fs::write(target_root.path().join("local-only.txt"), "keep").expect("local");
        let target = store
            .create_workspace("target", &path_string(target_root.path()))
            .expect("target workspace");

        let first = import_project_merge(&store, target, &bundle).expect("merge");
        assert_eq!(first.sessions, 1);
        assert_eq!(first.memories, 1);
        let second = import_project_merge(&store, target, &bundle).expect("merge again");
        assert_eq!(second.sessions, 0);
        let sessions = store.list_sessions(10).expect("sessions");
        assert_eq!(
            sessions.len(),
            2,
            "each project keeps its own copy of the chat"
        );
        let copied = sessions
            .iter()
            .find(|summary| summary.workspace_id == Some(target))
            .expect("target chat");
        assert_eq!(
            store.load_events(copied.id).expect("events").len(),
            1,
            "a repeated merge must not duplicate events"
        );
        let memories = store.recall_memories(target, None, 10).expect("memories");
        assert_eq!(memories.len(), 1);
        assert_eq!(
            fs::read_to_string(target_root.path().join("src/main.rs")).expect("read"),
            "fn main() {}"
        );
        assert_eq!(
            fs::read_to_string(target_root.path().join("local-only.txt")).expect("read"),
            "keep"
        );
    }

    #[test]
    fn unsafe_archive_paths_are_rejected() {
        let root = tempdir().expect("root");
        let bundle = root.path().join("evil.openbad-project");
        let file = File::create(&bundle).expect("file");
        let mut zip = ZipWriter::new(file);
        zip.start_file(MANIFEST_NAME, SimpleFileOptions::default())
            .expect("manifest");
        zip.write_all(
            br#"{"format_version":1,"app_version":"0","project_name":"evil","exported_at":0}"#,
        )
        .expect("write");
        zip.start_file(STATE_NAME, SimpleFileOptions::default())
            .expect("state");
        zip.write_all(br#"{"memories":[],"knowledge":[],"sessions":[],"checkpoints":[]}"#)
            .expect("write");
        zip.start_file("workspace/../escape.txt", SimpleFileOptions::default())
            .expect("evil entry");
        zip.write_all(b"nope").expect("write");
        zip.finish().expect("finish");

        let store = Store::open_in_memory().expect("store");
        let destination = root.path().join("imported");
        let error = import_project(&store, &bundle, &destination).expect_err("rejected");
        assert!(matches!(error, TransferError::InvalidBundle(_)));
    }

    #[test]
    fn future_bundle_versions_are_rejected() {
        let root = tempdir().expect("root");
        let bundle = root.path().join("future.openbad-project");
        let file = File::create(&bundle).expect("file");
        let mut zip = ZipWriter::new(file);
        zip.start_file(MANIFEST_NAME, SimpleFileOptions::default())
            .expect("manifest");
        zip.write_all(
            br#"{"format_version":99,"app_version":"9","project_name":"future","exported_at":0}"#,
        )
        .expect("write");
        zip.finish().expect("finish");
        let store = Store::open_in_memory().expect("store");
        let error =
            import_project(&store, &bundle, &root.path().join("new")).expect_err("unsupported");
        assert!(matches!(error, TransferError::UnsupportedVersion { .. }));
    }

    #[test]
    fn empty_destination_requirement_is_enforced() {
        let root = tempdir().expect("root");
        let (bundle_root, store, workspace) = workspace_with_files();
        let bundle = bundle_root.path().join("export.openbad-project");
        export_project(&store, workspace, &bundle, "0.1.4").expect("export");
        let destination = root.path().join("busy");
        fs::create_dir_all(&destination).expect("dir");
        fs::write(destination.join("existing.txt"), "x").expect("file");
        assert!(matches!(
            import_project(&store, &bundle, &destination),
            Err(TransferError::DestinationNotEmpty(_))
        ));
    }

    #[test]
    fn project_directory_names_are_portable() {
        assert_eq!(safe_project_directory_name("OpenBaD"), "OpenBaD");
        assert_eq!(safe_project_directory_name("a/b:c"), "a-b-c");
        assert_eq!(safe_project_directory_name(" .. "), "imported-project");
        assert_eq!(safe_project_directory_name(""), "imported-project");
    }

    #[test]
    fn state_serialization_round_trips() {
        let state = WorkspaceState::default();
        let json = serde_json::to_vec(&state).expect("json");
        let decoded: WorkspaceState = serde_json::from_slice(&json).expect("decode");
        assert_eq!(decoded, state);
    }
}
