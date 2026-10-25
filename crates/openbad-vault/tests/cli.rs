use std::io::Write;
use std::process::{Command, Stdio};

use base64::Engine;
use base64::engine::general_purpose::STANDARD;
use tempfile::tempdir;

fn binary() -> &'static str {
    env!("CARGO_BIN_EXE_openbad-vault")
}

fn run(
    args: &[&str],
    workspace: &std::path::Path,
    key: &str,
    stdin: Option<&[u8]>,
) -> std::process::Output {
    let mut command = Command::new(binary());
    command
        .arg("--workspace")
        .arg(workspace)
        .args(args)
        .env("OPENBAD_VAULT_KEY", key)
        .env_remove("OPENBAD_WORKSPACE")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = command.spawn().expect("spawn openbad-vault");
    if let Some(input) = stdin {
        child
            .stdin
            .as_mut()
            .expect("stdin")
            .write_all(input)
            .expect("write stdin");
    }
    drop(child.stdin.take());
    child.wait_with_output().expect("wait for openbad-vault")
}

#[test]
fn cli_put_get_list_rm_roundtrip_without_plaintext_metadata() {
    let directory = tempdir().expect("tempdir");
    let key = STANDARD.encode([11u8; 32]);
    let secret = b"cli-secret-value-798";

    let put = run(&["put", "db-password"], directory.path(), &key, Some(secret));
    assert!(
        put.status.success(),
        "put failed: {}",
        String::from_utf8_lossy(&put.stderr)
    );
    assert!(!put.stdout.windows(secret.len()).any(|window| window == secret));

    let get = run(&["get", "db-password"], directory.path(), &key, None);
    assert!(
        get.status.success(),
        "get failed: {}",
        String::from_utf8_lossy(&get.stderr)
    );
    assert_eq!(get.stdout, secret);

    let list = run(&["list"], directory.path(), &key, None);
    assert!(list.status.success());
    let list = String::from_utf8(list.stdout).expect("utf8 list");
    assert!(list.contains("db-password"));
    assert!(list.contains("XChaCha20-Poly1305"));
    assert!(!list.contains("cli-secret-value-798"));

    let remove = run(&["rm", "db-password"], directory.path(), &key, None);
    assert!(remove.status.success());
    let missing = run(&["get", "db-password"], directory.path(), &key, None);
    assert!(!missing.status.success());
    assert!(String::from_utf8_lossy(&missing.stderr).contains("not found"));
}

#[cfg(unix)]
#[test]
fn cli_generates_a_private_default_key_file_on_first_use() {
    use std::os::unix::fs::PermissionsExt;

    let workspace = tempdir().expect("workspace");
    let home = tempdir().expect("home");
    let secret = b"default-key-file-secret-798";

    let mut put = Command::new(binary());
    put.arg("--workspace")
        .arg(workspace.path())
        .args(["put", "key-file-secret"])
        .env_remove("OPENBAD_VAULT_KEY")
        .env_remove("OPENBAD_WORKSPACE")
        .env_remove("XDG_CONFIG_HOME")
        .env("HOME", home.path())
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = put.spawn().expect("spawn put");
    child
        .stdin
        .as_mut()
        .expect("stdin")
        .write_all(secret)
        .expect("write secret");
    drop(child.stdin.take());
    let output = child.wait_with_output().expect("put output");
    assert!(
        output.status.success(),
        "put failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );

    let key_path = home
        .path()
        .join(".config")
        .join("openbaddesktop")
        .join("vault.key");
    assert!(key_path.is_file(), "key file was not generated");
    let mode = std::fs::metadata(&key_path)
        .expect("key metadata")
        .permissions()
        .mode()
        & 0o777;
    assert_eq!(mode, 0o600);

    let mut get = Command::new(binary());
    get.arg("--workspace")
        .arg(workspace.path())
        .args(["get", "key-file-secret"])
        .env_remove("OPENBAD_VAULT_KEY")
        .env_remove("OPENBAD_WORKSPACE")
        .env_remove("XDG_CONFIG_HOME")
        .env("HOME", home.path())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let output = get.output().expect("get output");
    assert!(
        output.status.success(),
        "get failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert_eq!(output.stdout, secret);
}

#[test]
fn cli_help_does_not_need_a_key() {
    let directory = tempdir().expect("tempdir");
    let mut command = Command::new(binary());
    command
        .arg("--help")
        .env_remove("OPENBAD_VAULT_KEY")
        .env_remove("OPENBAD_WORKSPACE")
        .current_dir(directory.path())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let output = command.output().expect("help");
    assert!(output.status.success());
    assert!(String::from_utf8_lossy(&output.stdout).contains("openbad-vault"));
}
