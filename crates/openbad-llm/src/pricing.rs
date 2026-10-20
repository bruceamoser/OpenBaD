use chrono::{DateTime, Datelike, NaiveDate, Timelike, Utc, Weekday};
use serde::{Deserialize, Serialize};

use crate::provider::Usage;

/// Per-million-token prices in USD.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct Price {
    pub cache_hit: f64,
    pub cache_miss: f64,
    pub output: f64,
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct ModelPricing {
    pub off_peak: Price,
    pub peak: Price,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Serialize, Deserialize)]
pub struct CostBreakdown {
    pub cache_hit_usd: f64,
    pub cache_miss_usd: f64,
    pub output_usd: f64,
}

impl CostBreakdown {
    #[must_use]
    pub fn total_usd(&self) -> f64 {
        self.cache_hit_usd + self.cache_miss_usd + self.output_usd
    }
}

impl ModelPricing {
    #[must_use]
    pub fn price(&self, peak: bool) -> Price {
        if peak { self.peak } else { self.off_peak }
    }

    #[must_use]
    pub fn cost(&self, usage: &Usage, peak: bool) -> CostBreakdown {
        let price = self.price(peak);
        let per_million = 1_000_000.0;
        let miss_tokens = usage.input_tokens + usage.cache_write_tokens;

        CostBreakdown {
            cache_hit_usd: usage.cache_read_tokens as f64 / per_million * price.cache_hit,
            cache_miss_usd: miss_tokens as f64 / per_million * price.cache_miss,
            output_usd: usage.output_tokens as f64 / per_million * price.output,
        }
    }
}

pub const DEEPSEEK_FLASH: ModelPricing = ModelPricing {
    off_peak: Price {
        cache_hit: 0.003,
        cache_miss: 0.15,
        output: 0.60,
    },
    peak: Price {
        cache_hit: 0.006,
        cache_miss: 0.30,
        output: 1.20,
    },
};

pub const DEEPSEEK_V4_PRO: ModelPricing = ModelPricing {
    off_peak: Price {
        cache_hit: 0.022,
        cache_miss: 0.66,
        output: 1.98,
    },
    peak: Price {
        cache_hit: 0.044,
        cache_miss: 1.32,
        output: 3.96,
    },
};

#[must_use]
pub fn deepseek_pricing(model: &str) -> Option<ModelPricing> {
    match model {
        crate::deepseek::FLASH_MODEL => Some(DEEPSEEK_FLASH),
        crate::deepseek::PRO_MODEL => Some(DEEPSEEK_V4_PRO),
        _ => None,
    }
}

/// DeepSeek peak windows are 01:00–04:00 and 06:00–10:00 UTC on weekdays,
/// excluding configured holidays. Everything else is off-peak.
#[derive(Debug, Clone, Default)]
pub struct PeakCalendar {
    holidays: std::collections::BTreeSet<NaiveDate>,
}

impl PeakCalendar {
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    #[must_use]
    pub fn with_holidays(holidays: impl IntoIterator<Item = NaiveDate>) -> Self {
        Self {
            holidays: holidays.into_iter().collect(),
        }
    }

    pub fn add_holiday(&mut self, holiday: NaiveDate) {
        self.holidays.insert(holiday);
    }

    #[must_use]
    pub fn is_peak(&self, at: DateTime<Utc>) -> bool {
        if matches!(at.weekday(), Weekday::Sat | Weekday::Sun)
            || self.holidays.contains(&at.date_naive())
        {
            return false;
        }

        let hour = at.hour();
        (1..4).contains(&hour) || (6..10).contains(&hour)
    }

    #[must_use]
    pub fn is_off_peak(&self, at: DateTime<Utc>) -> bool {
        !self.is_peak(at)
    }

    #[must_use]
    pub fn is_peak_now(&self) -> bool {
        self.is_peak(Utc::now())
    }
}

/// Accumulated usage and cost for a session, turn, or route.
#[derive(Debug, Clone, Copy, Default, PartialEq, Serialize, Deserialize)]
pub struct UsageTotals {
    pub input_tokens: u64,
    pub cache_read_tokens: u64,
    pub cache_write_tokens: u64,
    pub output_tokens: u64,
    pub reasoning_tokens: u64,
    pub cost_usd: f64,
}

impl UsageTotals {
    pub fn record(&mut self, usage: &Usage, cost: &CostBreakdown) {
        self.input_tokens += usage.input_tokens;
        self.cache_read_tokens += usage.cache_read_tokens;
        self.cache_write_tokens += usage.cache_write_tokens;
        self.output_tokens += usage.output_tokens;
        self.reasoning_tokens += usage.reasoning_tokens.unwrap_or(0);
        self.cost_usd += cost.total_usd();
    }

    #[must_use]
    pub fn billed_input(&self) -> u64 {
        self.input_tokens + self.cache_read_tokens + self.cache_write_tokens
    }

    #[must_use]
    pub fn cache_hit_share(&self) -> f64 {
        let billed = self.billed_input();
        if billed == 0 {
            0.0
        } else {
            self.cache_read_tokens as f64 / billed as f64
        }
    }
}

#[cfg(test)]
mod tests {
    use chrono::TimeZone;

    use super::*;

    fn usage(hit: u64, miss: u64, output: u64) -> Usage {
        Usage {
            input_tokens: miss,
            cache_read_tokens: hit,
            cache_write_tokens: 0,
            output_tokens: output,
            reasoning_tokens: None,
        }
    }

    #[test]
    fn flash_cost_uses_disjoint_hit_and_miss_prices() {
        let cost = DEEPSEEK_FLASH.cost(&usage(1_000_000, 1_000_000, 1_000_000), false);
        assert!((cost.cache_hit_usd - 0.003).abs() < 1e-12);
        assert!((cost.cache_miss_usd - 0.15).abs() < 1e-12);
        assert!((cost.output_usd - 0.60).abs() < 1e-12);
        assert!((cost.total_usd() - 0.753).abs() < 1e-12);
    }

    #[test]
    fn peak_prices_are_double_off_peak() {
        let cost = DEEPSEEK_V4_PRO.cost(&usage(1_000_000, 1_000_000, 1_000_000), true);
        assert!((cost.cache_hit_usd - 0.044).abs() < 1e-12);
        assert!((cost.cache_miss_usd - 1.32).abs() < 1e-12);
        assert!((cost.output_usd - 3.96).abs() < 1e-12);
        assert!((cost.total_usd() - 5.324).abs() < 1e-12);
    }

    #[test]
    fn peak_windows_are_exclusive_at_the_edges() {
        let calendar = PeakCalendar::new();
        let monday = |hour, minute| {
            Utc.with_ymd_and_hms(2026, 10, 19, hour, minute, 0)
                .single()
                .expect("time")
        };
        assert!(calendar.is_off_peak(monday(0, 59)));
        assert!(calendar.is_peak(monday(1, 0)));
        assert!(calendar.is_peak(monday(3, 59)));
        assert!(calendar.is_off_peak(monday(4, 0)));
        assert!(calendar.is_off_peak(monday(5, 59)));
        assert!(calendar.is_peak(monday(6, 0)));
        assert!(calendar.is_peak(monday(9, 59)));
        assert!(calendar.is_off_peak(monday(10, 0)));
    }

    #[test]
    fn weekends_and_holidays_are_off_peak() {
        let saturday = Utc
            .with_ymd_and_hms(2026, 10, 17, 2, 0, 0)
            .single()
            .expect("time");
        let holiday = NaiveDate::from_ymd_opt(2026, 10, 19).expect("date");
        let calendar = PeakCalendar::with_holidays([holiday]);
        let monday = Utc
            .with_ymd_and_hms(2026, 10, 19, 2, 0, 0)
            .single()
            .expect("time");

        assert!(calendar.is_off_peak(saturday));
        assert!(calendar.is_off_peak(monday));
    }

    #[test]
    fn totals_track_hit_share_and_cost() {
        let mut totals = UsageTotals::default();
        totals.record(
            &usage(900, 100, 50),
            &DEEPSEEK_FLASH.cost(&usage(900, 100, 50), false),
        );
        assert_eq!(totals.billed_input(), 1_000);
        assert!((totals.cache_hit_share() - 0.9).abs() < 1e-12);
        assert!(totals.cost_usd > 0.0);
    }

    #[test]
    fn unknown_models_have_no_default_pricing() {
        assert!(deepseek_pricing("not-a-deepseek-model").is_none());
        assert!(deepseek_pricing(crate::deepseek::FLASH_MODEL).is_some());
    }
}
