//! Knowledge library model: collections, documents, and chunks.

use serde::{Deserialize, Serialize};
use uuid::Uuid;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct KnowledgeCollection {
    pub id: Uuid,
    pub name: String,
}

impl KnowledgeCollection {
    #[must_use]
    pub fn new(name: impl Into<String>) -> Self {
        Self {
            id: Uuid::now_v7(),
            name: name.into(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct KnowledgeDocument {
    pub id: Uuid,
    pub collection_id: Uuid,
    pub title: String,
    pub content_hash: String,
    pub version: u32,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn collections_get_time_sortable_ids() {
        let collection = KnowledgeCollection::new("Architecture");
        assert_eq!(collection.name, "Architecture");
    }
}
