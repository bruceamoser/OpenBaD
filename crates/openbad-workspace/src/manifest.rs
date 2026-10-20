//! Project manifest and scope-map support.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use globset::Glob;
use serde::{Deserialize, Serialize};

#[derive(Debug, thiserror::Error)]
pub enum ManifestError {
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
    #[error("invalid project manifest: {0}")]
    Invalid(String),
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct ScopeMap {
    #[serde(default)]
    pub authoritative: Vec<String>,
    #[serde(default)]
    pub historical: Vec<String>,
    #[serde(default)]
    pub generated: Vec<String>,
    #[serde(default)]
    pub vendored: Vec<String>,
    #[serde(default)]
    pub scratch: Vec<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ScopeClass {
    Authoritative,
    Historical,
    Generated,
    Vendored,
    Scratch,
    Unclassified,
}

impl ScopeClass {
    #[must_use]
    pub fn label(self) -> &'static str {
        match self {
            Self::Authoritative => "authoritative",
            Self::Historical => "historical",
            Self::Generated => "generated",
            Self::Vendored => "vendored",
            Self::Scratch => "scratch",
            Self::Unclassified => "unclassified",
        }
    }

    #[must_use]
    pub fn needs_care(self) -> bool {
        matches!(self, Self::Historical | Self::Generated)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum CommandSpec {
    Shell(String),
    Detailed(CommandDefinition),
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CommandDefinition {
    pub command: String,
    #[serde(default)]
    pub description: Option<String>,
    #[serde(default)]
    pub cwd: Option<String>,
    #[serde(default)]
    pub timeout_seconds: Option<u64>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ValidatorDefinition {
    pub pattern: String,
    pub command: String,
    #[serde(default)]
    pub description: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Invariant {
    pub id: String,
    pub statement: String,
    #[serde(default)]
    pub check: Option<String>,
    #[serde(default)]
    pub severity: Option<String>,
    #[serde(default)]
    pub rationale: Option<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProjectManifest {
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default)]
    pub description: Option<String>,
    #[serde(default)]
    pub scope: ScopeMap,
    #[serde(default)]
    pub commands: BTreeMap<String, CommandSpec>,
    #[serde(default)]
    pub recipes: BTreeMap<String, CommandSpec>,
    #[serde(default)]
    pub validators: Vec<ValidatorDefinition>,
    #[serde(default)]
    pub invariants: Vec<Invariant>,
}

impl ProjectManifest {
    #[must_use]
    pub fn classify(&self, path: &str) -> ScopeClass {
        let path = path.trim_start_matches("./").replace('\\', "/");
        for (class, patterns) in [
            (ScopeClass::Authoritative, &self.scope.authoritative),
            (ScopeClass::Historical, &self.scope.historical),
            (ScopeClass::Generated, &self.scope.generated),
            (ScopeClass::Vendored, &self.scope.vendored),
            (ScopeClass::Scratch, &self.scope.scratch),
        ] {
            if patterns.iter().any(|pattern| glob_matches(pattern, &path)) {
                return class;
            }
        }
        ScopeClass::Unclassified
    }

    #[must_use]
    pub fn command(&self, name: &str) -> Option<CommandDefinition> {
        self.commands.get(name).map(|spec| match spec {
            CommandSpec::Shell(command) => CommandDefinition {
                command: command.clone(),
                description: None,
                cwd: None,
                timeout_seconds: None,
            },
            CommandSpec::Detailed(definition) => definition.clone(),
        })
    }

    #[must_use]
    pub fn recipe(&self, name: &str) -> Option<CommandDefinition> {
        self.recipes.get(name).map(|spec| match spec {
            CommandSpec::Shell(command) => CommandDefinition {
                command: command.clone(),
                description: None,
                cwd: None,
                timeout_seconds: None,
            },
            CommandSpec::Detailed(definition) => definition.clone(),
        })
    }

    #[must_use]
    pub fn validators_for(&self, path: &str) -> Vec<&ValidatorDefinition> {
        let path = path.trim_start_matches("./").replace('\\', "/");
        self.validators
            .iter()
            .filter(|validator| glob_matches(&validator.pattern, &path))
            .collect()
    }

    #[must_use]
    pub fn summary(&self) -> String {
        let mut summary = String::new();
        if let Some(name) = &self.name {
            summary.push_str(&format!("Project: {name}\n"));
        }
        if let Some(description) = &self.description {
            summary.push_str(&format!("{description}\n"));
        }
        if !self.commands.is_empty() {
            summary.push_str("Named tasks:\n");
            for (name, spec) in &self.commands {
                let (command, description) = match spec {
                    CommandSpec::Shell(command) => (command.clone(), None),
                    CommandSpec::Detailed(definition) => {
                        (definition.command.clone(), definition.description.clone())
                    }
                };
                summary.push_str(&format!(
                    "- {name}: `{command}`{}\n",
                    description
                        .map(|description| format!(" — {description}"))
                        .unwrap_or_default()
                ));
            }
        }
        if !self.recipes.is_empty() {
            summary.push_str("Recipes:\n");
            for (name, spec) in &self.recipes {
                let (command, description) = match spec {
                    CommandSpec::Shell(command) => (command.clone(), None),
                    CommandSpec::Detailed(definition) => {
                        (definition.command.clone(), definition.description.clone())
                    }
                };
                summary.push_str(&format!(
                    "- {name}: `{command}`{}\n",
                    description
                        .map(|description| format!(" — {description}"))
                        .unwrap_or_default()
                ));
            }
        }
        if !self.validators.is_empty() {
            summary.push_str("Validators:\n");
            for validator in &self.validators {
                summary.push_str(&format!(
                    "- {} runs `{}`\n",
                    validator.pattern, validator.command
                ));
            }
        }
        if !self.invariants.is_empty() {
            summary.push_str("Invariants:\n");
            for invariant in &self.invariants {
                summary.push_str(&format!("- {}: {}\n", invariant.id, invariant.statement));
            }
        }
        let scope = [
            ("authoritative", &self.scope.authoritative),
            ("historical", &self.scope.historical),
            ("generated", &self.scope.generated),
            ("vendored", &self.scope.vendored),
            ("scratch", &self.scope.scratch),
        ];
        if scope.iter().any(|(_, patterns)| !patterns.is_empty()) {
            summary.push_str("Scope map:\n");
            for (label, patterns) in scope {
                if !patterns.is_empty() {
                    summary.push_str(&format!("- {label}: {}\n", patterns.join(", ")));
                }
            }
        }
        summary
    }
}

#[must_use]
pub fn manifest_path(root: &Path) -> PathBuf {
    root.join(".openbad").join("project.yaml")
}

pub fn load_project_manifest(root: &Path) -> Result<Option<ProjectManifest>, ManifestError> {
    let path = manifest_path(root);
    if !path.is_file() {
        return Ok(None);
    }
    let content = std::fs::read_to_string(&path)?;
    let manifest: ProjectManifest = serde_yaml::from_str(&content)
        .map_err(|error| ManifestError::Invalid(error.to_string()))?;
    Ok(Some(manifest))
}

fn glob_matches(pattern: &str, path: &str) -> bool {
    Glob::new(pattern)
        .ok()
        .map(|glob| glob.compile_matcher().is_match(path))
        .unwrap_or(false)
}

#[cfg(test)]
mod tests {
    use std::fs;

    use tempfile::tempdir;

    use super::*;

    #[test]
    fn loads_manifest_and_classifies_paths() {
        let directory = tempdir().expect("tempdir");
        fs::create_dir_all(directory.path().join(".openbad")).expect("openbad");
        fs::write(
            directory.path().join(".openbad/project.yaml"),
            r#"
name: Demo
commands:
  verify: npm run verify
  test:
    command: npm test
    cwd: ./
recipes:
  verify:
    command: npm run verify
    description: Typecheck and test
  lint: cargo clippy
validators:
  - pattern: "src/**/*.yaml"
    command: npm run validate:yaml
scope:
  authoritative: ["src/**", "docs/**"]
  historical: ["archive/**"]
  generated: ["dist/**"]
invariants:
  - id: INV-1
    statement: Every export is documented.
"#,
        )
        .expect("manifest");
        let manifest = load_project_manifest(directory.path())
            .expect("load")
            .expect("present");
        assert_eq!(manifest.name.as_deref(), Some("Demo"));
        assert_eq!(manifest.classify("src/main.rs"), ScopeClass::Authoritative);
        assert_eq!(manifest.classify("archive/old.md"), ScopeClass::Historical);
        assert!(manifest.classify("archive/old.md").needs_care());
        assert_eq!(manifest.classify("notes.md"), ScopeClass::Unclassified);
        assert_eq!(
            manifest.command("verify").expect("verify").command,
            "npm run verify"
        );
        assert!(manifest.command("test").expect("test").cwd.as_deref() == Some("./"));
        assert_eq!(
            manifest.recipe("verify").expect("recipe").command,
            "npm run verify"
        );
        assert_eq!(manifest.validators_for("src/packs/a.yaml").len(), 1);
        assert!(manifest.validators_for("docs/readme.md").is_empty());
        assert!(manifest.summary().contains("Named tasks"));
        assert!(manifest.summary().contains("Recipes"));
        assert!(manifest.summary().contains("Validators"));
    }

    #[test]
    fn absent_manifest_is_none() {
        let directory = tempdir().expect("tempdir");
        assert!(
            load_project_manifest(directory.path())
                .expect("load")
                .is_none()
        );
    }
}
