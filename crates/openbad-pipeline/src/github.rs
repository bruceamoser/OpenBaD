//! One GitHub identity for every pipeline action.
//!
//! `gh auth switch` changes the shared active account, which is global mutable
//! state: a pipeline that relied on it would push and comment as whatever account
//! the person happened to be using for their other work, and the two workflows
//! would flip each other back and forth. So the pipeline names an account and
//! supplies its credential to each process it starts instead:
//!
//! * the token is read from the account store with `gh auth token --user`, with
//!   the ambient `GH_TOKEN` and friends removed first, so the lookup cannot
//!   accidentally return whatever is active;
//! * every command gets `GH_TOKEN` in its own environment and an isolated,
//!   token-free `GH_CONFIG_DIR`, so nothing can fall back to the shared config;
//! * `GITHUB_TOKEN`, which takes precedence in some tools, is removed rather than
//!   left to disagree.
//!
//! The token never leaves this module in text: it is not serialized, it is
//! redacted from tool output, and errors name the account rather than the secret.

use std::ffi::OsStr;
use std::path::{Path, PathBuf};
use std::process::Command;

use crate::error::SourceError;

/// Environment variables that would override, or conflict with, the pinned
/// credential. Cleared before the lookup and before every command.
pub const OVERRIDING_VARIABLES: [&str; 4] =
    ["GH_TOKEN", "GITHUB_TOKEN", "GH_CONFIG_DIR", "GH_HOST"];

/// A GitHub account the pipeline acts as.
///
/// Deliberately not `Serialize`: the token is a credential, and nothing that
/// leaves the process should be able to include it by accident.
#[derive(Clone, PartialEq, Eq)]
pub struct GitHubIdentity {
    user: String,
    token: String,
    config_dir: PathBuf,
}

impl std::fmt::Debug for GitHubIdentity {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        // The account is not secret; the token is.
        f.debug_struct("GitHubIdentity")
            .field("user", &self.user)
            .field("token", &"[redacted]")
            .field("config_dir", &self.config_dir)
            .finish()
    }
}

/// The command that reads one account's saved credential.
///
/// The ambient overrides are removed from it, not merely ignored: `gh auth token`
/// echoes `GH_TOKEN` when it is set, so leaving it would make "the credential for
/// this account" mean "whatever is active".
fn lookup_command(gh: &Path, user: &str) -> Command {
    let mut lookup = Command::new(gh);
    for variable in OVERRIDING_VARIABLES {
        lookup.env_remove(variable);
    }
    lookup.env("GH_PROMPT_DISABLED", "1").args([
        "auth",
        "token",
        "--hostname",
        "github.com",
        "--user",
        user,
    ]);
    lookup
}

impl GitHubIdentity {
    /// Reads `user`'s saved credential and binds it to an isolated config
    /// directory, so every later command is that account and nothing else.
    pub fn resolve(gh: &Path, user: &str, config_dir: PathBuf) -> Result<Self, SourceError> {
        let user = user.trim();
        if user.is_empty() {
            return Err(SourceError::Config(
                "github.user is empty; name the account the pipeline should act as".to_owned(),
            ));
        }

        let output = lookup_command(gh, user)
            .output()
            .map_err(|error| SourceError::Api(format!("cannot run gh auth token: {error}")))?;
        if !output.status.success() {
            let said = String::from_utf8_lossy(&output.stderr);
            return Err(SourceError::Config(format!(
                "no saved GitHub credential for {user}: {}. Run `gh auth login` as that account once; the pipeline only reads the stored credential and never switches the active account.",
                said.trim()
            )));
        }
        let token = String::from_utf8_lossy(&output.stdout).trim().to_owned();
        if token.is_empty() {
            return Err(SourceError::Config(format!(
                "the saved credential for {user} is empty"
            )));
        }

        std::fs::create_dir_all(&config_dir).map_err(|error| {
            SourceError::Config(format!("cannot create {}: {error}", config_dir.display()))
        })?;
        Ok(Self {
            user: user.to_owned(),
            token,
            config_dir,
        })
    }

    /// The account the pipeline acts as.
    #[must_use]
    pub fn user(&self) -> &str {
        &self.user
    }

    /// The credential, for redacting it out of anything a person will read.
    #[must_use]
    pub fn token(&self) -> &str {
        &self.token
    }

    /// The variables every child process needs to act as this account.
    ///
    /// Returned as a list rather than applied, so the caller can use it for a
    /// `Command` it is building or hand it to an agent's session.
    #[must_use]
    pub fn variables(&self) -> Vec<(String, String)> {
        vec![
            ("GH_TOKEN".to_owned(), self.token.clone()),
            (
                "GH_CONFIG_DIR".to_owned(),
                self.config_dir.display().to_string(),
            ),
            ("GH_HOST".to_owned(), "github.com".to_owned()),
        ]
    }

    /// Applies the identity to a command about to run.
    pub fn apply(&self, command: &mut Command) {
        command.env_remove("GITHUB_TOKEN");
        for (name, value) in self.variables() {
            command.env(name, value);
        }
    }
}

/// Where an isolated config directory lives for one account.
///
/// Under the app's own data directory, never the person's `~/.config/gh`: the
/// point is that `gh` cannot read the shared configuration and therefore cannot
/// answer as the wrong account.
#[must_use]
pub fn config_dir(data_dir: &Path, user: &str) -> PathBuf {
    let safe: String = user
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '-' || c == '_' {
                c
            } else {
                '_'
            }
        })
        .collect();
    data_dir.join("gh").join(safe)
}

/// Resolves the configured identity, if the project named one.
///
/// `None` keeps the previous behaviour — whatever `gh` is authenticated as — so a
/// project that has not named an account is unaffected.
pub fn configured(
    gh: Option<&Path>,
    user: Option<&str>,
    data_dir: &Path,
) -> Result<Option<GitHubIdentity>, SourceError> {
    let Some(user) = user.map(str::trim).filter(|user| !user.is_empty()) else {
        return Ok(None);
    };
    let gh = gh.ok_or_else(|| {
        SourceError::Api(format!(
            "cannot find gh, so the credential for {user} cannot be read"
        ))
    })?;
    GitHubIdentity::resolve(gh, user, config_dir(data_dir, user)).map(Some)
}

/// Identities already resolved in this process, by account.
///
/// A resolution shells out to `gh` and reads the account store, so it is done
/// once and shared: the watcher, the setup panel's guard check, and an agent
/// asking the `pipeline` tool all act as the same account without each paying for
/// it or, worse, each forming its own opinion.
fn cache() -> &'static std::sync::Mutex<std::collections::HashMap<String, GitHubIdentity>> {
    static CACHE: std::sync::OnceLock<
        std::sync::Mutex<std::collections::HashMap<String, GitHubIdentity>>,
    > = std::sync::OnceLock::new();
    CACHE.get_or_init(|| std::sync::Mutex::new(std::collections::HashMap::new()))
}

/// Resolves the identity, reusing one already resolved for this account.
pub fn cached(
    gh: Option<&Path>,
    user: Option<&str>,
    data_dir: &Path,
) -> Result<Option<GitHubIdentity>, SourceError> {
    let Some(user) = user.map(str::trim).filter(|user| !user.is_empty()) else {
        return Ok(None);
    };
    if let Ok(cache) = cache().lock() {
        if let Some(known) = cache.get(user) {
            return Ok(Some(known.clone()));
        }
    }
    let identity = configured(gh, Some(user), data_dir)?;
    if let Some(identity) = &identity {
        if let Ok(mut cache) = cache().lock() {
            cache.insert(user.to_owned(), identity.clone());
        }
    }
    Ok(identity)
}

/// Where to keep an isolated config when the caller has no data directory of its
/// own. Stable across processes so a token-free directory is reused, not leaked
/// per call.
#[must_use]
pub fn default_config_root() -> PathBuf {
    std::env::temp_dir().join("openbad-gh")
}

/// Applies an identity to a command when there is one.
pub fn apply(identity: Option<&GitHubIdentity>, command: &mut Command) {
    if let Some(identity) = identity {
        identity.apply(command);
    }
}

/// A value with the token removed, for text a person will read.
#[must_use]
pub fn redact(text: &str, identity: Option<&GitHubIdentity>) -> String {
    match identity {
        Some(identity) => text.replace(identity.token(), "[redacted]"),
        None => text.to_owned(),
    }
}

/// Whether `name` is an environment variable the identity owns.
#[must_use]
pub fn owns(variable: &OsStr) -> bool {
    OVERRIDING_VARIABLES
        .iter()
        .any(|owned| variable == OsStr::new(owned))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A `gh` that answers `auth token --user <name>` from a file, so the tests
    /// exercise the real command line without a network or a credential store.
    fn fake_gh(dir: &Path, known: &str, token: &str) -> PathBuf {
        let path = dir.join("gh");
        std::fs::write(
            &path,
            format!(
                "#!/bin/sh\n\
                 if [ \"$GH_TOKEN\" != \"\" ] || [ \"$GITHUB_TOKEN\" != \"\" ]; then\n\
                   echo 'ambient token was not cleared' >&2; exit 9\n\
                 fi\n\
                 for arg in \"$@\"; do\n\
                   if [ \"$arg\" = \"--user\" ]; then next=1; continue; fi\n\
                   if [ \"${{next:-}}\" = \"1\" ]; then\n\
                     if [ \"$arg\" = \"{known}\" ]; then echo '{token}'; exit 0; fi\n\
                     echo 'not logged in' >&2; exit 1\n\
                   fi\n\
                 done\n\
                 echo 'no --user' >&2; exit 2\n"
            ),
        )
        .expect("fake gh");
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt as _;
            std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o755)).expect("mode");
        }
        path
    }

    #[test]
    fn the_lookup_clears_every_ambient_override_first() {
        let command = lookup_command(Path::new("/usr/bin/gh"), "bruceamoser");
        let mut removed: Vec<&str> = command
            .get_envs()
            .filter(|(_, value)| value.is_none())
            .filter_map(|(name, _)| name.to_str())
            .collect();
        removed.sort_unstable();
        assert_eq!(
            removed,
            vec!["GH_CONFIG_DIR", "GH_HOST", "GH_TOKEN", "GITHUB_TOKEN"],
            "an ambient token would otherwise be read back as this account's"
        );
        let args: Vec<&std::ffi::OsStr> = command.get_args().collect();
        assert_eq!(
            args,
            vec![
                "auth",
                "token",
                "--hostname",
                "github.com",
                "--user",
                "bruceamoser"
            ]
        );
    }

    #[test]
    fn an_identity_supplies_its_own_credential_and_an_isolated_config() {
        let directory = tempfile::tempdir().expect("dir");
        let gh = fake_gh(directory.path(), "bruceamoser", "secret-token");

        let identity = GitHubIdentity::resolve(&gh, "bruceamoser", directory.path().join("cfg"))
            .expect("resolve");

        assert_eq!(identity.user(), "bruceamoser");
        assert_eq!(
            identity.variables(),
            vec![
                ("GH_TOKEN".to_owned(), "secret-token".to_owned()),
                (
                    "GH_CONFIG_DIR".to_owned(),
                    directory.path().join("cfg").display().to_string()
                ),
                ("GH_HOST".to_owned(), "github.com".to_owned()),
            ]
        );
        assert!(
            !format!("{identity:?}").contains("secret-token"),
            "the token must not be printable"
        );
    }

    #[test]
    fn an_account_with_no_saved_credential_says_so_instead_of_switching() {
        let directory = tempfile::tempdir().expect("dir");
        let gh = fake_gh(directory.path(), "someone-else", "secret-token");
        let error = GitHubIdentity::resolve(&gh, "bruceamoser", directory.path().join("cfg"))
            .expect_err("no credential");
        assert!(
            error
                .to_string()
                .contains("no saved GitHub credential for bruceamoser"),
            "{error}"
        );
        assert!(!error.to_string().contains("secret-token"));
    }

    #[test]
    fn a_project_that_names_no_account_uses_the_ambient_one() {
        let directory = tempfile::tempdir().expect("dir");
        assert_eq!(
            configured(None, None, directory.path()).expect("none"),
            None
        );
        assert_eq!(
            configured(None, Some("   "), directory.path()).expect("blank"),
            None
        );
    }

    #[test]
    fn the_token_is_redacted_out_of_what_a_person_reads() {
        let identity = GitHubIdentity {
            user: "bruceamoser".to_owned(),
            token: "secret-token".to_owned(),
            config_dir: PathBuf::from("/tmp/gh"),
        };
        assert_eq!(
            redact("push failed with secret-token", Some(&identity)),
            "push failed with [redacted]"
        );
        assert_eq!(redact("nothing to hide", None), "nothing to hide");
    }

    #[test]
    fn a_config_directory_is_derived_from_the_account_and_cannot_escape() {
        let base = Path::new("/data");
        assert_eq!(
            config_dir(base, "bruceamoser"),
            PathBuf::from("/data/gh/bruceamoser")
        );
        assert_eq!(config_dir(base, "../etc"), PathBuf::from("/data/gh/___etc"));
    }
}
