use openbad_core::ProviderId;
use rusqlite::{Connection, params};
use serde_json::Value;
use uuid::Uuid;

use crate::error::StoreError;
use crate::now_millis;
use crate::types::{AuditRecord, AuditReport};

fn canonical_record(
    id: Uuid,
    ts: i64,
    actor: &str,
    action: &str,
    target: Option<&str>,
    decision: Option<&str>,
    policy: Option<&str>,
    metadata: &Value,
) -> Value {
    serde_json::json!({
        "id": id.to_string(),
        "ts": ts,
        "actor": actor,
        "action": action,
        "target": target,
        "decision": decision,
        "policy": policy,
        "metadata": metadata,
    })
}

fn hash_record(previous_hash: &[u8], bytes: &[u8]) -> Vec<u8> {
    let mut input = Vec::with_capacity(previous_hash.len() + bytes.len());
    input.extend_from_slice(previous_hash);
    input.extend_from_slice(bytes);
    blake3::hash(&input).as_bytes().to_vec()
}

/// Append one tamper-evident audit record.
pub fn append(
    connection: &mut Connection,
    actor: &str,
    action: &str,
    target: Option<&str>,
    decision: Option<&str>,
    policy: Option<&str>,
    metadata: Value,
) -> Result<AuditRecord, StoreError> {
    let prev_hash: Vec<u8> = match connection.query_row(
        "SELECT hash FROM audit_log ORDER BY rowid DESC LIMIT 1",
        [],
        |row| row.get(0),
    ) {
        Ok(hash) => hash,
        Err(rusqlite::Error::QueryReturnedNoRows) => vec![0_u8; 32],
        Err(error) => return Err(error.into()),
    };

    let id = Uuid::now_v7();
    let ts = now_millis();
    let record = canonical_record(id, ts, actor, action, target, decision, policy, &metadata);
    let bytes = serde_json::to_vec(&record)?;
    let hash = hash_record(&prev_hash, &bytes);

    connection.execute(
        "INSERT INTO audit_log
         (id, ts, actor, action, target, decision, policy, metadata_json, prev_hash, hash)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
        params![
            id.to_string(),
            ts,
            actor,
            action,
            target,
            decision,
            policy,
            serde_json::to_string(&metadata)?,
            prev_hash,
            hash,
        ],
    )?;

    Ok(AuditRecord {
        id,
        ts,
        actor: actor.to_owned(),
        action: action.to_owned(),
        target: target.map(str::to_owned),
        decision: decision.map(str::to_owned),
        policy: policy.map(str::to_owned),
        metadata,
        prev_hash,
        hash,
    })
}

/// Verify the full audit chain.
pub fn verify(connection: &Connection) -> Result<AuditReport, StoreError> {
    let mut statement = connection.prepare(
        "SELECT id, ts, actor, action, target, decision, policy, metadata_json,
                prev_hash, hash
         FROM audit_log ORDER BY rowid ASC",
    )?;
    let rows = statement.query_map([], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, i64>(1)?,
            row.get::<_, String>(2)?,
            row.get::<_, String>(3)?,
            row.get::<_, Option<String>>(4)?,
            row.get::<_, Option<String>>(5)?,
            row.get::<_, Option<String>>(6)?,
            row.get::<_, String>(7)?,
            row.get::<_, Vec<u8>>(8)?,
            row.get::<_, Vec<u8>>(9)?,
        ))
    })?;

    let mut expected_previous = vec![0_u8; 32];
    let mut records = 0_u64;

    for row in rows {
        let (id, ts, actor, action, target, decision, policy, metadata_json, prev_hash, hash) =
            row?;
        records += 1;
        let metadata: Value = serde_json::from_str(&metadata_json)?;
        let record = canonical_record(
            Uuid::parse_str(&id).map_err(|error| StoreError::InvalidData(error.to_string()))?,
            ts,
            &actor,
            &action,
            target.as_deref(),
            decision.as_deref(),
            policy.as_deref(),
            &metadata,
        );
        let bytes = serde_json::to_vec(&record)?;
        let expected_hash = hash_record(&expected_previous, &bytes);

        if prev_hash != expected_previous || hash != expected_hash {
            return Ok(AuditReport {
                valid: false,
                records,
                first_invalid: Some(id),
            });
        }
        expected_previous = hash;
    }

    Ok(AuditReport {
        valid: true,
        records,
        first_invalid: None,
    })
}

/// A convenience helper for provider audit actions.
pub fn provider_target(provider_id: ProviderId) -> String {
    format!("provider:{provider_id}")
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn connection() -> Connection {
        let mut connection = Connection::open_in_memory().expect("connection");
        crate::migrations::apply(&mut connection).expect("migrations");
        connection
    }

    #[test]
    fn audit_chain_verifies() {
        let mut connection = connection();
        append(
            &mut connection,
            "user",
            "workspace.trust",
            Some("workspace:demo"),
            Some("allow"),
            Some("trusted"),
            json!({"path": "/tmp/demo"}),
        )
        .expect("first");
        append(
            &mut connection,
            "agent",
            "tool.execute",
            Some("tool:run_tests"),
            Some("allow_once"),
            Some("execute"),
            json!({"duration_ms": 1200}),
        )
        .expect("second");

        let report = verify(&connection).expect("verify");
        assert!(report.valid);
        assert_eq!(report.records, 2);
    }

    #[test]
    fn tampering_is_detected() {
        let mut connection = connection();
        let record = append(
            &mut connection,
            "user",
            "workspace.trust",
            Some("workspace:demo"),
            Some("allow"),
            Some("trusted"),
            json!({"path": "/tmp/demo"}),
        )
        .expect("append");
        connection
            .execute(
                "UPDATE audit_log SET metadata_json = ?1 WHERE id = ?2",
                params![r#"{"path":"/tmp/other"}"#, record.id.to_string()],
            )
            .expect("tamper");

        let report = verify(&connection).expect("verify");
        assert!(!report.valid);
        assert_eq!(
            report.first_invalid.as_deref(),
            Some(record.id.to_string().as_str())
        );
    }
}
