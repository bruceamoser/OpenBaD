//! Workspace root, manifest, and path-containment primitives.

pub mod canon;
pub mod context;
pub mod manifest;
pub mod policy;
pub mod questions;

use std::path::{Component, Path, PathBuf};

pub use canon::{
    BannedPhrase, CanonEntry, CanonError, CanonLedger, Glossary, GlossaryEntry, adr_directory,
    canon_path, current_truth, glossary_path, lint_text, load_canon, load_glossary, save_canon,
    save_glossary, validate_canon_status,
};
pub use context::{
    ContextError, ContextPolicy, ContextRule, DeadEnd, context_path, dead_end_summary,
    dead_ends_path, load_context_policy, load_dead_ends, save_context_policy, save_dead_ends,
};
pub use manifest::{
    CommandDefinition, CommandSpec, Invariant, ManifestError, ProjectManifest, ScopeClass,
    ScopeMap, ValidatorDefinition, load_project_manifest, manifest_path,
};
pub use policy::{PolicyError, ProjectPolicy, load_project_policy, policy_path};
pub use questions::{
    Question, QuestionError, answer_question, load_questions, question_summary, questions_path,
    save_questions,
};

fn normalize(path: &Path) -> PathBuf {
    let mut normalized = PathBuf::new();
    for component in path.components() {
        match component {
            Component::ParentDir => {
                normalized.pop();
            }
            Component::CurDir => {}
            other => normalized.push(other.as_os_str()),
        }
    }
    normalized
}

#[derive(Debug, thiserror::Error)]
pub enum WorkspaceError {
    #[error("path is outside the workspace: {0}")]
    OutsideRoot(PathBuf),
    #[error("path does not exist: {0}")]
    NotFound(PathBuf),
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
}

#[derive(Debug, Clone)]
pub struct WorkspaceService {
    root: PathBuf,
}

impl WorkspaceService {
    #[must_use]
    pub fn new(root: impl Into<PathBuf>) -> Self {
        Self { root: root.into() }
    }

    #[must_use]
    pub fn root(&self) -> &Path {
        &self.root
    }

    /// Lexical containment check. `resolve` adds canonicalization and symlink
    /// protection for actual filesystem access.
    #[must_use]
    pub fn contains(&self, path: &Path) -> bool {
        let root = normalize(&self.root);
        let candidate = if path.is_absolute() {
            normalize(path)
        } else {
            normalize(&self.root.join(path))
        };
        candidate.starts_with(&root)
    }

    /// Resolve a workspace-relative or absolute path and reject escapes.
    pub fn resolve(&self, path: &Path) -> Result<PathBuf, WorkspaceError> {
        let root = self.root.canonicalize().map_err(|error| {
            if error.kind() == std::io::ErrorKind::NotFound {
                WorkspaceError::NotFound(self.root.clone())
            } else {
                WorkspaceError::Io(error)
            }
        })?;

        let candidate = if path.is_absolute() {
            path.to_path_buf()
        } else {
            self.root.join(path)
        };
        let lexical = normalize(&candidate);
        if !lexical.starts_with(&root) && !lexical.starts_with(normalize(&self.root)) {
            return Err(WorkspaceError::OutsideRoot(path.to_path_buf()));
        }

        if candidate.exists() {
            let canonical = candidate.canonicalize()?;
            if canonical.starts_with(&root) {
                Ok(canonical)
            } else {
                Err(WorkspaceError::OutsideRoot(path.to_path_buf()))
            }
        } else {
            let mut ancestor = candidate.parent();
            while let Some(parent) = ancestor {
                if parent.exists() {
                    let canonical = parent.canonicalize()?;
                    if !canonical.starts_with(&root) {
                        return Err(WorkspaceError::OutsideRoot(path.to_path_buf()));
                    }
                    let suffix = candidate
                        .strip_prefix(parent)
                        .map_err(|_| WorkspaceError::OutsideRoot(path.to_path_buf()))?;
                    return Ok(canonical.join(suffix));
                }
                ancestor = parent.parent();
            }
            Err(WorkspaceError::NotFound(candidate))
        }
    }
}

#[cfg(test)]
mod tests {
    use std::fs;

    use tempfile::tempdir;

    use super::*;

    #[test]
    fn containment_rejects_parent_traversal() {
        let workspace = WorkspaceService::new("/tmp/project");
        assert!(workspace.contains(Path::new("src/main.rs")));
        assert!(!workspace.contains(Path::new("../secrets")));
    }

    #[test]
    fn resolve_accepts_inside_paths_and_new_files() {
        let directory = tempdir().expect("tempdir");
        fs::create_dir_all(directory.path().join("src")).expect("src");
        let workspace = WorkspaceService::new(directory.path());

        let existing = workspace
            .resolve(Path::new("src/main.rs"))
            .expect("existing");
        assert!(existing.starts_with(directory.path().canonicalize().expect("root")));

        let new_file = workspace.resolve(Path::new("src/new.rs")).expect("new");
        assert!(new_file.starts_with(directory.path().canonicalize().expect("root")));

        let nested = workspace
            .resolve(Path::new("src/generated/new.rs"))
            .expect("nested new");
        assert!(nested.starts_with(directory.path().canonicalize().expect("root")));
    }

    #[test]
    fn resolve_rejects_parent_and_symlink_escapes() {
        let directory = tempdir().expect("tempdir");
        let outside = tempdir().expect("outside");
        fs::write(outside.path().join("secret.txt"), "secret").expect("secret");
        let workspace = WorkspaceService::new(directory.path());

        assert!(workspace.resolve(Path::new("../outside.txt")).is_err());

        #[cfg(unix)]
        {
            std::os::unix::fs::symlink(outside.path(), directory.path().join("link"))
                .expect("symlink");
            assert!(workspace.resolve(Path::new("link/secret.txt")).is_err());
        }
    }
}
