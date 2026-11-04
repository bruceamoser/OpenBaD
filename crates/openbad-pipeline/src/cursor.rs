//! Read positions: what a source has already seen.
//!
//! A restart must not replay events, and must not drop them either. Both
//! properties come from one structure: a watermark timestamp plus the ids of the
//! triggers already emitted near that watermark.
//!
//! The watermark is deliberately kept a little behind the newest event seen — a
//! safety overlap — because provider clocks and our own ordering are not the same
//! thing. The overlap re-reads a few events, and the id set suppresses them. That
//! trade is the safe direction: a re-read costs one comparison, a gap costs work
//! that silently never happens.

use serde::{Deserialize, Serialize};

use crate::trigger::TriggerId;

/// How far behind the newest event the watermark sits.
pub const WATERMARK_OVERLAP_MS: i64 = 60_000;

/// How many emitted trigger ids a cursor remembers.
///
/// Bounded because the set exists only to absorb the overlap above; anything
/// older than the watermark cannot come back, so eviction is safe.
pub const MAX_REMEMBERED_TRIGGERS: usize = 2_048;

/// One source's persisted position.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct SourceCursor {
    /// Newest provider timestamp seen, in milliseconds since the epoch.
    #[serde(default)]
    pub watermark_ms: i64,
    /// Triggers already emitted, newest last.
    #[serde(default)]
    pub emitted: Vec<TriggerId>,
}

impl SourceCursor {
    /// The `since` value to ask the provider for: the watermark minus the overlap.
    #[must_use]
    pub fn since_ms(&self) -> i64 {
        (self.watermark_ms - WATERMARK_OVERLAP_MS).max(0)
    }

    /// Whether this trigger was already emitted.
    #[must_use]
    pub fn seen(&self, id: &str) -> bool {
        self.emitted.iter().any(|emitted| emitted == id)
    }

    /// Records a trigger as emitted and advances the watermark. Returns whether
    /// it was new.
    pub fn accept(&mut self, id: &str, at_ms: i64) -> bool {
        if self.seen(id) {
            return false;
        }
        self.emitted.push(id.to_owned());
        if self.emitted.len() > MAX_REMEMBERED_TRIGGERS {
            let excess = self.emitted.len() - MAX_REMEMBERED_TRIGGERS;
            self.emitted.drain(0..excess);
        }
        self.watermark_ms = self.watermark_ms.max(at_ms);
        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_same_trigger_is_only_emitted_once() {
        let mut cursor = SourceCursor::default();
        assert!(cursor.accept("main:issue_opened:o/r#1:1", 1_000));
        assert!(
            !cursor.accept("main:issue_opened:o/r#1:1", 1_000),
            "a re-read of the overlap is suppressed"
        );
        assert_eq!(cursor.emitted.len(), 1);
    }

    #[test]
    fn the_watermark_moves_forward_and_never_back() {
        let mut cursor = SourceCursor::default();
        cursor.accept("a", 5_000);
        cursor.accept("b", 3_000);
        assert_eq!(
            cursor.watermark_ms, 5_000,
            "an older event does not rewind it"
        );
    }

    #[test]
    fn the_since_value_overlaps_by_a_minute_and_never_goes_negative() {
        let mut cursor = SourceCursor::default();
        assert_eq!(cursor.since_ms(), 0, "an empty cursor asks from the start");
        cursor.accept("a", 10 * 60_000);
        assert_eq!(cursor.since_ms(), 9 * 60_000);
    }

    #[test]
    fn the_remembered_set_stays_bounded() {
        let mut cursor = SourceCursor::default();
        for index in 0..(MAX_REMEMBERED_TRIGGERS + 10) {
            cursor.accept(&format!("t{index}"), index as i64);
        }
        assert_eq!(cursor.emitted.len(), MAX_REMEMBERED_TRIGGERS);
        assert!(!cursor.seen("t0"), "the oldest fell out");
        assert!(cursor.seen(&format!("t{}", MAX_REMEMBERED_TRIGGERS + 9)));
    }

    #[test]
    fn a_cursor_survives_a_round_trip_through_json() {
        let mut cursor = SourceCursor::default();
        cursor.accept("main:issue_comment:o/r#2:99", 1_700_000_000_000);
        let text = serde_json::to_string(&cursor).expect("serialize");
        let back: SourceCursor = serde_json::from_str(&text).expect("deserialize");
        assert_eq!(back, cursor);
        assert!(back.seen("main:issue_comment:o/r#2:99"));
    }
}
