#[cfg(feature = "keyring")]
use std::env;
#[cfg(feature = "keyring")]
use std::io;

#[cfg(feature = "keyring")]
use openbad_governance::secrets::{KeychainSecretStore, Secret, SecretStore};
#[cfg(feature = "keyring")]
use uuid::Uuid;

#[cfg(feature = "keyring")]
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = env::args().skip(1).collect();
    let check = args.iter().any(|arg| arg == "--check");
    let delete = args.iter().any(|arg| arg == "--delete");
    let keep = args.iter().any(|arg| arg == "--keep");
    let reference = args
        .iter()
        .find(|arg| !arg.starts_with("--"))
        .cloned()
        .unwrap_or_else(|| format!("openbad/validation/{}", Uuid::now_v7()));

    let store = KeychainSecretStore::new("OpenBaD Desktop");

    if check {
        match store.get(&reference)? {
            Some(secret) => println!("present: {} bytes", secret.expose().len()),
            None => println!("absent"),
        }
        return Ok(());
    }

    if delete {
        store.delete(&reference)?;
        println!("deleted: {reference}");
        return Ok(());
    }

    let value = env::var("OPENBAD_KEYCHAIN_TEST_SECRET")
        .unwrap_or_else(|_| format!("openbad-test-{}", Uuid::now_v7()));
    store.set(&reference, &Secret::new(value.clone()))?;

    let loaded = store
        .get(&reference)?
        .ok_or_else(|| io::Error::other("keychain did not return the stored secret"))?;
    if loaded.expose() != value {
        return Err(io::Error::other("keychain returned a different secret").into());
    }

    if keep {
        println!("stored: {reference}");
        println!("run with --check {reference} to verify persistence");
    } else {
        store.delete(&reference)?;
        if store.get(&reference)?.is_some() {
            return Err(io::Error::other("keychain did not delete the stored secret").into());
        }
        println!("keychain round-trip OK");
        println!("reference: {reference}");
    }

    Ok(())
}

#[cfg(not(feature = "keyring"))]
fn main() {
    eprintln!("This example requires `--features keyring`.");
    std::process::exit(2);
}
