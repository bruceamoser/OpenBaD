//! Small CLI for the project vault.
//!
//! Values are read from stdin and written to stdout, never from argv, so a
//! secret does not appear in the process list. The CLI is for the human user;
//! it is not exposed to the model as a tool.

use std::env;
use std::io::{self, Read, Write};
use std::path::PathBuf;
use std::process::ExitCode;

use openbad_vault::{MAX_SECRET_BYTES, Vault, list_metadata};

const USAGE: &str = "\
usage: openbad-vault [--workspace <path>] <command>\n\n\
commands:\n\
  put <name>    read a secret from stdin and store it\n\
  get <name>    write the secret to stdout (no trailing newline)\n\
  list          print secret metadata (never values)\n\
  rm <name>     remove a secret\n\n\
The workspace defaults to OPENBAD_WORKSPACE or the current directory.\n\
The key comes from OPENBAD_VAULT_KEY (base64, 32 bytes) or\n\
~/.config/openbaddesktop/vault.key, generated with mode 0600 on first use.";

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("openbad-vault: {error}");
            ExitCode::FAILURE
        }
    }
}

fn run() -> Result<(), String> {
    let mut arguments = env::args().skip(1).collect::<Vec<_>>();
    if arguments
        .iter()
        .any(|argument| argument == "-h" || argument == "--help")
    {
        println!("{USAGE}");
        return Ok(());
    }

    let mut workspace = env::var_os("OPENBAD_WORKSPACE")
        .map(PathBuf::from)
        .unwrap_or(env::current_dir().map_err(|error| error.to_string())?);

    let mut index = 0;
    while index < arguments.len() {
        if arguments[index] == "--workspace" || arguments[index] == "-C" {
            if index + 1 >= arguments.len() {
                return Err("--workspace requires a directory".to_owned());
            }
            workspace = PathBuf::from(arguments.remove(index + 1));
            arguments.remove(index);
        } else if let Some(value) = arguments[index].strip_prefix("--workspace=") {
            if value.is_empty() {
                return Err("--workspace requires a directory".to_owned());
            }
            workspace = PathBuf::from(value);
            arguments.remove(index);
        } else {
            index += 1;
        }
    }

    let Some(command) = arguments.first().cloned() else {
        println!("{USAGE}");
        return Err("missing command".to_owned());
    };

    match command.as_str() {
        "put" => {
            let name = name_argument(&arguments, "put")?;
            let mut value = zeroize::Zeroizing::new(Vec::new());
            io::stdin()
                .take((MAX_SECRET_BYTES + 1) as u64)
                .read_to_end(&mut value)
                .map_err(|error| error.to_string())?;
            if value.len() > MAX_SECRET_BYTES {
                return Err(format!(
                    "secret is too large ({} bytes; limit {MAX_SECRET_BYTES})",
                    value.len()
                ));
            }
            let vault = Vault::open(&workspace).map_err(|error| error.to_string())?;
            let entry = vault
                .put(name, &value)
                .map_err(|error| error.to_string())?;
            println!("stored {} ({})", entry.name, entry.id);
        }
        "get" => {
            let name = name_argument(&arguments, "get")?;
            let vault = Vault::open(&workspace).map_err(|error| error.to_string())?;
            let value = vault.get(name).map_err(|error| error.to_string())?;
            let mut stdout = io::stdout();
            stdout
                .write_all(value.expose_secret())
                .and_then(|()| stdout.flush())
                .map_err(|error| error.to_string())?;
        }
        "list" => {
            if arguments.len() != 1 {
                return Err("usage: openbad-vault list".to_owned());
            }
            let entries = list_metadata(&workspace).map_err(|error| error.to_string())?;
            println!("NAME\tCREATED\tUPDATED\tALGORITHM\tID");
            for entry in entries {
                println!(
                    "{}\t{}\t{}\t{}\t{}",
                    entry.name, entry.created, entry.updated, entry.algorithm, entry.id
                );
            }
        }
        "rm" | "remove" => {
            let command = command.as_str();
            let name = name_argument(&arguments, command)?;
            let vault = Vault::open(&workspace).map_err(|error| error.to_string())?;
            match vault.remove(name).map_err(|error| error.to_string())? {
                Some(entry) => println!("removed {} ({})", entry.name, entry.id),
                None => return Err(format!("secret not found: {name}")),
            }
        }
        other => return Err(format!("unknown command: {other}\n\n{USAGE}")),
    }
    Ok(())
}

fn name_argument<'a>(arguments: &'a [String], command: &str) -> Result<&'a str, String> {
    if arguments.len() != 2 {
        return Err(format!("usage: openbad-vault {command} <name>"));
    }
    Ok(arguments[1].as_str())
}
