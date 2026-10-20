//! Incremental workspace file, symbol, and full-text index.

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::LazyLock;
use std::time::{Instant, UNIX_EPOCH};

use ignore::WalkBuilder;
use openbad_core::WorkspaceId;
use openbad_store::Store;
use openbad_store::error::StoreError;
use openbad_store::types::{IndexSummary, IndexedFile, IndexedSymbol};

const DEFAULT_MAX_FILE_BYTES: u64 = 512 * 1024;

#[derive(Debug, thiserror::Error)]
pub enum IndexError {
    #[error("workspace path does not exist: {0}")]
    MissingRoot(PathBuf),
    #[error("store error: {0}")]
    Store(#[from] StoreError),
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct IndexReport {
    pub seen: u64,
    pub added: u64,
    pub updated: u64,
    pub removed: u64,
    pub elapsed_ms: u64,
    pub summary: IndexSummary,
}

#[derive(Clone)]
pub struct WorkspaceIndexer<'a> {
    store: &'a Store,
    workspace_id: WorkspaceId,
    root: PathBuf,
    max_file_bytes: u64,
}

impl<'a> WorkspaceIndexer<'a> {
    #[must_use]
    pub fn new(store: &'a Store, workspace_id: WorkspaceId, root: impl Into<PathBuf>) -> Self {
        Self {
            store,
            workspace_id,
            root: root.into(),
            max_file_bytes: DEFAULT_MAX_FILE_BYTES,
        }
    }

    #[must_use]
    pub fn with_max_file_bytes(mut self, max_file_bytes: u64) -> Self {
        self.max_file_bytes = max_file_bytes;
        self
    }

    pub fn refresh(&self) -> Result<IndexReport, IndexError> {
        if !self.root.is_dir() {
            return Err(IndexError::MissingRoot(self.root.clone()));
        }
        let started = Instant::now();
        let existing: HashMap<String, String> = self
            .store
            .workspace_index_hashes(self.workspace_id)?
            .into_iter()
            .collect();
        let mut seen = HashSet::new();
        let mut changed = Vec::new();
        let mut added = 0;
        let mut updated = 0;

        for entry in WalkBuilder::new(&self.root)
            .max_depth(Some(64))
            .build()
            .filter_map(Result::ok)
        {
            if !entry.file_type().is_some_and(|kind| kind.is_file()) {
                continue;
            }
            let path = entry.path();
            let Some(relative) = relative_path(&self.root, path) else {
                continue;
            };
            if !is_indexable(path) {
                continue;
            }
            let metadata = match entry.metadata() {
                Ok(metadata) if metadata.len() <= self.max_file_bytes => metadata,
                _ => continue,
            };
            let bytes = match std::fs::read(path) {
                Ok(bytes) => bytes,
                Err(_) => continue,
            };
            if bytes.contains(&0) {
                continue;
            }
            let Ok(content) = String::from_utf8(bytes) else {
                continue;
            };
            seen.insert(relative.clone());
            let hash = blake3::hash(content.as_bytes()).to_hex().to_string();
            if existing.get(&relative).is_some_and(|known| known == &hash) {
                continue;
            }
            if existing.contains_key(&relative) {
                updated += 1;
            } else {
                added += 1;
            }
            changed.push(IndexedFile {
                path: relative,
                hash,
                size: metadata.len(),
                modified_at: modified_millis(&metadata),
                language: language_for(path).map(str::to_owned),
                symbols: extract_symbols(&content),
                content,
            });
        }

        let removed: Vec<String> = existing
            .keys()
            .filter(|path| !seen.contains(*path))
            .cloned()
            .collect();
        if !changed.is_empty() || !removed.is_empty() {
            self.store
                .update_workspace_index(self.workspace_id, changed, removed.clone())?;
        }
        let summary = self.store.workspace_index_summary(self.workspace_id)?;
        Ok(IndexReport {
            seen: seen.len() as u64,
            added,
            updated,
            removed: removed.len() as u64,
            elapsed_ms: started.elapsed().as_millis() as u64,
            summary,
        })
    }
}

#[must_use]
pub fn extract_symbols(content: &str) -> Vec<IndexedSymbol> {
    let mut symbols = Vec::new();
    for (index, line) in content.lines().enumerate() {
        if line.len() > 500 {
            continue;
        }
        let Some(captures) = symbol_pattern().captures(line) else {
            continue;
        };
        let Some(name) = captures.name("name") else {
            continue;
        };
        let Some(kind) = captures.name("kind") else {
            continue;
        };
        symbols.push(IndexedSymbol {
            name: name.as_str().to_owned(),
            kind: kind.as_str().to_lowercase(),
            line: (index + 1) as u32,
            signature: line.trim().chars().take(300).collect(),
        });
    }
    symbols
}

fn symbol_pattern() -> &'static regex::Regex {
    static PATTERN: LazyLock<regex::Regex> = LazyLock::new(|| {
        regex::Regex::new(
            r"(?x)
        ^\s*
        (?:pub(?:\([^)]*\))?\s+)?
        (?:(?:async|unsafe|export|default|abstract|final)\s+)*
        (?P<kind>fn|func|def|struct|enum|trait|impl|mod|const|static|type|class|interface|function)\s+
        (?:\([^)]*\)\s*)?
        (?P<name>[A-Za-z_][A-Za-z0-9_]*)",
        )
        .expect("symbol regex")
    });
    &PATTERN
}

fn relative_path(root: &Path, path: &Path) -> Option<String> {
    let relative = path.strip_prefix(root).ok()?;
    Some(
        relative
            .components()
            .map(|component| component.as_os_str().to_string_lossy())
            .collect::<Vec<_>>()
            .join("/"),
    )
}

fn modified_millis(metadata: &std::fs::Metadata) -> i64 {
    metadata
        .modified()
        .ok()
        .and_then(|time| time.duration_since(UNIX_EPOCH).ok())
        .map_or(0, |duration| duration.as_millis() as i64)
}

#[must_use]
pub fn language_for(path: &Path) -> Option<&'static str> {
    match path.extension().and_then(|extension| extension.to_str())? {
        "rs" => Some("rust"),
        "ts" | "tsx" | "mts" | "cts" => Some("typescript"),
        "js" | "jsx" | "mjs" | "cjs" => Some("javascript"),
        "py" | "pyi" => Some("python"),
        "go" => Some("go"),
        "java" => Some("java"),
        "kt" | "kts" => Some("kotlin"),
        "c" | "h" => Some("c"),
        "cc" | "cpp" | "cxx" | "hpp" | "hh" => Some("cpp"),
        "cs" => Some("csharp"),
        "rb" => Some("ruby"),
        "php" => Some("php"),
        "swift" => Some("swift"),
        "scala" => Some("scala"),
        "lua" => Some("lua"),
        "sql" => Some("sql"),
        "sh" | "bash" | "zsh" => Some("shell"),
        "md" | "markdown" => Some("markdown"),
        "toml" => Some("toml"),
        "yaml" | "yml" => Some("yaml"),
        "json" | "jsonc" => Some("json"),
        "html" | "htm" => Some("html"),
        "css" => Some("css"),
        "scss" => Some("scss"),
        "proto" => Some("protobuf"),
        "graphql" | "gql" => Some("graphql"),
        "xml" => Some("xml"),
        "tf" => Some("terraform"),
        "nix" => Some("nix"),
        _ => None,
    }
}

fn is_indexable(path: &Path) -> bool {
    if language_for(path).is_some() {
        return true;
    }
    let name = path
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or_default();
    matches!(
        name,
        "Dockerfile"
            | "Containerfile"
            | "Makefile"
            | "Cargo.toml"
            | "Cargo.lock"
            | "package.json"
            | "pnpm-lock.yaml"
            | "go.mod"
            | "go.sum"
            | ".gitignore"
            | ".dockerignore"
            | "README"
            | "LICENSE"
    ) || name.starts_with("Dockerfile.")
}

#[cfg(test)]
mod tests {
    use std::fs;

    use openbad_store::Store;
    use tempfile::tempdir;

    use super::*;

    #[test]
    fn indexes_text_and_symbols_incrementally() {
        let directory = tempdir().expect("tempdir");
        let root = directory.path();
        fs::create_dir_all(root.join("src")).expect("src");
        fs::write(
            root.join("src/lib.rs"),
            "pub struct Config {}\npub fn load_config() {}\n",
        )
        .expect("rust");
        fs::write(root.join("README.md"), "OpenBaD workspace index\n").expect("readme");
        fs::write(root.join("image.png"), b"\x89PNG\0\0").expect("image");

        let store = Store::open_in_memory().expect("store");
        let workspace = store
            .create_workspace("index", root.to_str().expect("path"))
            .expect("workspace");
        let indexer = WorkspaceIndexer::new(&store, workspace, root);

        let report = indexer.refresh().expect("refresh");
        assert_eq!(report.summary.files, 2);
        assert_eq!(report.summary.symbols, 2);
        assert_eq!(report.added, 2);

        let hits = store
            .search_workspace(workspace, "load config", 10)
            .expect("search");
        assert_eq!(hits.len(), 1);
        assert_eq!(hits[0].path, "src/lib.rs");

        let symbols = store
            .find_indexed_symbols(workspace, "Config", None, 10)
            .expect("symbols");
        assert_eq!(symbols[0].name, "Config");
        assert_eq!(symbols[0].line, 1);

        let stable = indexer.refresh().expect("stable refresh");
        assert_eq!(stable.added, 0);
        assert_eq!(stable.updated, 0);

        fs::write(root.join("README.md"), "updated workspace documentation\n").expect("update");
        let updated = indexer.refresh().expect("updated refresh");
        assert_eq!(updated.updated, 1);
        assert_eq!(updated.added, 0);

        fs::remove_file(root.join("src/lib.rs")).expect("remove");
        let removed = indexer.refresh().expect("removed refresh");
        assert_eq!(removed.removed, 1);
        assert!(
            store
                .search_workspace(workspace, "load config", 10)
                .expect("search after removal")
                .is_empty()
        );
    }

    #[test]
    fn skips_binary_and_oversized_files() {
        let directory = tempdir().expect("tempdir");
        let root = directory.path();
        fs::write(root.join("empty.bin"), b"\0\0\0").expect("binary");
        fs::write(root.join("large.md"), "x".repeat(2048)).expect("large");
        let store = Store::open_in_memory().expect("store");
        let workspace = store
            .create_workspace("index", root.to_str().expect("path"))
            .expect("workspace");
        let report = WorkspaceIndexer::new(&store, workspace, root)
            .with_max_file_bytes(1024)
            .refresh()
            .expect("refresh");
        assert_eq!(report.summary.files, 0);
    }
}
