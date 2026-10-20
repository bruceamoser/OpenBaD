use serde::{Deserialize, Serialize};
use serde_json::Value;

/// Where a prompt section sits relative to the provider's cache.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CacheClass {
    /// Stable for a build + profile. No volatile data allowed.
    Static,
    /// Changes only on an explicit workspace refresh.
    Workspace,
    /// Append-only conversation history.
    Session,
    /// Regenerated every turn; must be the tail.
    Volatile,
}

/// One rendered section of the prompt.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PromptSection {
    pub key: String,
    pub order: u32,
    pub class: CacheClass,
    pub version: u64,
    pub content: String,
}

impl PromptSection {
    #[must_use]
    pub fn new(
        key: impl Into<String>,
        order: u32,
        class: CacheClass,
        version: u64,
        content: impl Into<String>,
    ) -> Self {
        Self {
            key: key.into(),
            order,
            class,
            version,
            content: content.into(),
        }
    }
}

/// A deterministic set of prompt sections.
#[derive(Debug, Default)]
pub struct SectionRegistry {
    sections: Vec<PromptSection>,
}

impl SectionRegistry {
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    pub fn insert(&mut self, section: PromptSection) -> Result<(), PromptError> {
        if self
            .sections
            .iter()
            .any(|existing| existing.key == section.key)
        {
            return Err(PromptError::DuplicateSection(section.key));
        }
        self.sections.push(section);
        Ok(())
    }

    /// Sections in cache-safe order: order, then key as a stable tie-break.
    #[must_use]
    pub fn render_all(&self) -> Vec<PromptSection> {
        let mut sections = self.sections.clone();
        sections.sort_by(|left, right| {
            (left.order, left.key.as_str()).cmp(&(right.order, right.key.as_str()))
        });
        sections
    }

    /// Render the canonical prompt text and reject volatile static sections.
    pub fn render_canonical(&self) -> Result<String, PromptError> {
        let sections = self.render_all();
        for section in &sections {
            self.assert_stable(section)?;
        }
        Ok(sections
            .iter()
            .map(|section| section.content.as_str())
            .collect::<Vec<_>>()
            .join("\n\n"))
    }

    /// Reject volatile content in the static prefix.
    pub fn assert_stable(&self, section: &PromptSection) -> Result<(), PromptError> {
        if section.class == CacheClass::Static
            && section.key != "tool_schemas"
            && looks_volatile(&section.content)
        {
            return Err(PromptError::VolatileStableSection(section.key.clone()));
        }
        Ok(())
    }
}

/// A deliberately conservative check for content that must never enter the
/// stable prefix.
#[must_use]
pub fn looks_volatile(content: &str) -> bool {
    let lower = content.to_ascii_lowercase();
    lower.contains("current time:")
        || lower.contains("current date:")
        || lower.contains("<runtime-context>")
        || contains_iso_date(content)
}

fn contains_iso_date(content: &str) -> bool {
    let bytes = content.as_bytes();
    bytes.windows(10).any(|window| {
        window[4] == b'-'
            && window[7] == b'-'
            && window[..4].iter().all(u8::is_ascii_digit)
            && window[5..7].iter().all(u8::is_ascii_digit)
            && window[8..10].iter().all(u8::is_ascii_digit)
    })
}

/// Serialize a JSON value with object keys sorted at every level. This is the
/// canonical form used for tool schemas and request hashes.
#[must_use]
pub fn canonical_json(value: &Value) -> String {
    let mut output = String::new();
    write_canonical(value, &mut output);
    output
}

fn write_canonical(value: &Value, output: &mut String) {
    match value {
        Value::Object(map) => {
            let mut keys: Vec<_> = map.keys().collect();
            keys.sort_unstable();
            output.push('{');
            for (index, key) in keys.into_iter().enumerate() {
                if index > 0 {
                    output.push(',');
                }
                output.push_str(&serde_json::to_string(key).expect("string serialization"));
                output.push(':');
                write_canonical(&map[key], output);
            }
            output.push('}');
        }
        Value::Array(values) => {
            output.push('[');
            for (index, value) in values.iter().enumerate() {
                if index > 0 {
                    output.push(',');
                }
                write_canonical(value, output);
            }
            output.push(']');
        }
        _ => output.push_str(&serde_json::to_string(value).expect("json serialization")),
    }
}

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum PromptError {
    #[error("duplicate prompt section: {0}")]
    DuplicateSection(String),
    #[error("volatile content found in stable prompt section: {0}")]
    VolatileStableSection(String),
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sections_render_in_stable_order() {
        let mut registry = SectionRegistry::new();
        registry
            .insert(PromptSection::new(
                "history",
                100,
                CacheClass::Session,
                1,
                "history",
            ))
            .expect("history");
        registry
            .insert(PromptSection::new(
                "identity",
                10,
                CacheClass::Static,
                1,
                "identity",
            ))
            .expect("identity");
        registry
            .insert(PromptSection::new(
                "workspace",
                70,
                CacheClass::Workspace,
                1,
                "workspace",
            ))
            .expect("workspace");

        let keys: Vec<_> = registry
            .render_all()
            .into_iter()
            .map(|section| section.key)
            .collect();
        assert_eq!(keys, ["identity", "workspace", "history"]);
    }

    #[test]
    fn volatile_content_is_rejected_in_static_sections() {
        let registry = SectionRegistry::new();
        let section = PromptSection::new(
            "identity",
            10,
            CacheClass::Static,
            1,
            "Current time: 2026-10-19T20:00:00Z",
        );
        assert!(registry.assert_stable(&section).is_err());
    }

    #[test]
    fn workspace_sections_may_contain_dates() {
        let registry = SectionRegistry::new();
        let section = PromptSection::new(
            "workspace",
            70,
            CacheClass::Workspace,
            1,
            "The project started in 2027-01-15.",
        );
        assert!(registry.assert_stable(&section).is_ok());
    }

    #[test]
    fn canonical_rendering_is_stable_and_rejects_volatile_static_text() {
        let mut registry = SectionRegistry::new();
        registry
            .insert(PromptSection::new(
                "identity",
                10,
                CacheClass::Static,
                1,
                "identity",
            ))
            .expect("identity");
        registry
            .insert(PromptSection::new(
                "runtime",
                90,
                CacheClass::Volatile,
                1,
                "runtime",
            ))
            .expect("runtime");
        assert_eq!(
            registry.render_canonical().expect("render"),
            "identity\n\nruntime"
        );

        let mut volatile = SectionRegistry::new();
        volatile
            .insert(PromptSection::new(
                "identity",
                10,
                CacheClass::Static,
                1,
                "generated 2027-01-15",
            ))
            .expect("identity");
        assert!(volatile.render_canonical().is_err());
    }

    #[test]
    fn canonical_json_sorts_object_keys_recursively() {
        let left = serde_json::json!({"b": 1, "a": {"z": true, "y": [2, 1]}});
        let right = serde_json::json!({"a": {"y": [2, 1], "z": true}, "b": 1});
        assert_eq!(canonical_json(&left), canonical_json(&right));
        assert_eq!(canonical_json(&left), r#"{"a":{"y":[2,1],"z":true},"b":1}"#);
    }
}
