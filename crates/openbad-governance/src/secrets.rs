use std::collections::HashMap;
use std::fmt;
use std::sync::Mutex;

use openbad_core::ProviderId;
use zeroize::Zeroize;

/// A secret value that redacts itself in logs and wipes its buffer on drop.
#[derive(Clone, PartialEq, Eq)]
pub struct Secret(String);

impl Secret {
    #[must_use]
    pub fn new(value: impl Into<String>) -> Self {
        Self(value.into())
    }

    #[must_use]
    pub fn expose(&self) -> &str {
        &self.0
    }

    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.0.is_empty()
    }
}

impl fmt::Debug for Secret {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("Secret(***)")
    }
}

impl Drop for Secret {
    fn drop(&mut self) {
        self.0.zeroize();
    }
}

#[derive(Debug, thiserror::Error)]
pub enum SecretError {
    #[error("secret store is unavailable: {0}")]
    Unavailable(String),
    #[error("secret backend error: {0}")]
    Backend(String),
    #[error("secret store lock poisoned")]
    LockPoisoned,
}

/// Storage for API keys and other credentials. Implementations must never
/// write secret material into the SQLite database.
pub trait SecretStore: Send + Sync {
    fn get(&self, reference: &str) -> Result<Option<Secret>, SecretError>;
    fn set(&self, reference: &str, secret: &Secret) -> Result<(), SecretError>;
    fn delete(&self, reference: &str) -> Result<(), SecretError>;
}

#[must_use]
pub fn provider_key_reference(provider_id: ProviderId) -> String {
    format!("provider/{provider_id}/api_key")
}

/// In-memory store for tests and for sessions that have not configured a
/// platform keychain yet. Values disappear when the process exits.
#[derive(Debug, Default)]
pub struct MemorySecretStore {
    values: Mutex<HashMap<String, Secret>>,
}

impl MemorySecretStore {
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }
}

impl SecretStore for MemorySecretStore {
    fn get(&self, reference: &str) -> Result<Option<Secret>, SecretError> {
        let values = self.values.lock().map_err(|_| SecretError::LockPoisoned)?;
        Ok(values.get(reference).cloned())
    }

    fn set(&self, reference: &str, secret: &Secret) -> Result<(), SecretError> {
        let mut values = self.values.lock().map_err(|_| SecretError::LockPoisoned)?;
        values.insert(reference.to_owned(), secret.clone());
        Ok(())
    }

    fn delete(&self, reference: &str) -> Result<(), SecretError> {
        let mut values = self.values.lock().map_err(|_| SecretError::LockPoisoned)?;
        values.remove(reference);
        Ok(())
    }
}

/// Platform keychain-backed storage. Enable the `keyring` feature to use it.
#[cfg(feature = "keyring")]
#[derive(Debug, Clone)]
pub struct KeychainSecretStore {
    service: String,
}

#[cfg(feature = "keyring")]
impl KeychainSecretStore {
    #[must_use]
    pub fn new(service: impl Into<String>) -> Self {
        Self {
            service: service.into(),
        }
    }

    fn entry(&self, reference: &str) -> Result<keyring::Entry, SecretError> {
        keyring::Entry::new(&self.service, reference).map_err(|error| {
            SecretError::Unavailable(format!("could not open keychain entry: {error}"))
        })
    }
}

#[cfg(feature = "keyring")]
impl SecretStore for KeychainSecretStore {
    fn get(&self, reference: &str) -> Result<Option<Secret>, SecretError> {
        match self.entry(reference)?.get_password() {
            Ok(value) => Ok(Some(Secret::new(value))),
            Err(keyring::Error::NoEntry) => Ok(None),
            Err(error) => Err(SecretError::Backend(error.to_string())),
        }
    }

    fn set(&self, reference: &str, secret: &Secret) -> Result<(), SecretError> {
        self.entry(reference)?
            .set_password(secret.expose())
            .map_err(|error| SecretError::Backend(error.to_string()))
    }

    fn delete(&self, reference: &str) -> Result<(), SecretError> {
        match self.entry(reference)?.delete_credential() {
            Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
            Err(error) => Err(SecretError::Backend(error.to_string())),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn memory_store_round_trips() {
        let store = MemorySecretStore::new();
        let reference = provider_key_reference(ProviderId::new());
        assert!(store.get(&reference).expect("get").is_none());

        store.set(&reference, &Secret::new("sk-test")).expect("set");
        assert_eq!(
            store
                .get(&reference)
                .expect("get")
                .map(|s| s.expose().to_owned()),
            Some("sk-test".to_owned())
        );

        store.delete(&reference).expect("delete");
        assert!(store.get(&reference).expect("get").is_none());
    }

    #[test]
    fn secrets_redact_in_debug_output() {
        let secret = Secret::new("sk-super-secret");
        let rendered = format!("{secret:?}");
        assert_eq!(rendered, "Secret(***)");
        assert!(!rendered.contains("sk-super-secret"));
    }
}
