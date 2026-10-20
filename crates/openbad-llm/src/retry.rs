use std::time::Duration;

/// Retry decisions for provider calls. Retries always reuse the same
/// `FrozenRequest` bytes; the policy only decides when to try again.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RetryPolicy {
    pub max_attempts: u32,
    pub base_delay_ms: u64,
    pub max_delay_ms: u64,
}

impl Default for RetryPolicy {
    fn default() -> Self {
        Self {
            max_attempts: 5,
            base_delay_ms: 500,
            max_delay_ms: 30_000,
        }
    }
}

impl RetryPolicy {
    #[must_use]
    pub fn new(max_attempts: u32, base_delay_ms: u64, max_delay_ms: u64) -> Self {
        Self {
            max_attempts,
            base_delay_ms,
            max_delay_ms,
        }
    }

    /// Return the delay before the next attempt, or `None` when the error is
    /// terminal or the attempt budget is exhausted.
    #[must_use]
    pub fn next_delay(
        &self,
        attempts_made: u32,
        status: Option<u16>,
        retry_after: Option<Duration>,
    ) -> Option<Duration> {
        if attempts_made + 1 >= self.max_attempts || !is_retryable(status) {
            return None;
        }
        if let Some(retry_after) = retry_after {
            return Some(retry_after.min(Duration::from_millis(self.max_delay_ms)));
        }

        let factor = 1_u64.checked_shl(attempts_made).unwrap_or(u64::MAX);
        let delay_ms = self
            .base_delay_ms
            .saturating_mul(factor)
            .min(self.max_delay_ms);
        Some(Duration::from_millis(delay_ms))
    }
}

#[must_use]
pub fn is_retryable(status: Option<u16>) -> bool {
    match status {
        None => true,
        Some(408 | 429) => true,
        Some(status) => (500..600).contains(&status),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rate_limits_and_server_errors_retry_with_backoff() {
        let policy = RetryPolicy::default();
        assert_eq!(
            policy.next_delay(0, Some(429), None),
            Some(Duration::from_millis(500))
        );
        assert_eq!(
            policy.next_delay(1, Some(500), None),
            Some(Duration::from_millis(1_000))
        );
        assert_eq!(
            policy.next_delay(2, None, None),
            Some(Duration::from_millis(2_000))
        );
    }

    #[test]
    fn client_errors_are_terminal() {
        let policy = RetryPolicy::default();
        for status in [400, 401, 403, 404, 422] {
            assert_eq!(policy.next_delay(0, Some(status), None), None);
        }
    }

    #[test]
    fn retry_after_wins_and_is_capped() {
        let policy = RetryPolicy::default();
        assert_eq!(
            policy.next_delay(0, Some(429), Some(Duration::from_secs(7))),
            Some(Duration::from_secs(7))
        );
        assert_eq!(
            policy.next_delay(0, Some(429), Some(Duration::from_secs(120))),
            Some(Duration::from_millis(30_000))
        );
    }

    #[test]
    fn attempt_budget_is_enforced() {
        let policy = RetryPolicy::default();
        assert_eq!(policy.next_delay(4, Some(500), None), None);
        assert!(policy.next_delay(3, Some(500), None).is_some());
    }
}
