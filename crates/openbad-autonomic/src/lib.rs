//! Heartbeat, pulse, and background-work primitives.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, Default, PartialEq, Serialize, Deserialize)]
pub struct PulseSnapshot {
    pub cpu_pct: f32,
    pub memory_pct: f32,
    pub disk_free_bytes: u64,
    pub context_pressure: f32,
    pub cache_hit_share: f32,
    pub token_spend_usd: f64,
    pub error_rate: f32,
}

impl PulseSnapshot {
    /// A conservative health check used by the Pulse panel.
    #[must_use]
    pub fn is_healthy(&self) -> bool {
        self.cpu_pct < 90.0
            && self.memory_pct < 85.0
            && self.disk_free_bytes > 2_000_000_000
            && self.context_pressure < 0.80
            && self.error_rate < 0.05
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn healthy_snapshot_has_free_disk() {
        let pulse = PulseSnapshot {
            disk_free_bytes: 10_000_000_000,
            ..PulseSnapshot::default()
        };
        assert!(pulse.is_healthy());
    }

    #[test]
    fn high_context_pressure_is_unhealthy() {
        let pulse = PulseSnapshot {
            disk_free_bytes: 10_000_000_000,
            context_pressure: 0.95,
            ..PulseSnapshot::default()
        };
        assert!(!pulse.is_healthy());
    }
}
