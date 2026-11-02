//! A bounded log file beside the database.
//!
//! The session log in `openbad.db` explains a finished turn, but it cannot
//! explain a failure that happens before a session exists: a panic in startup,
//! a store that will not open, a crash in the window. This module records those.

use std::fs::{self, OpenOptions};
use std::io::Write;
use std::panic;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

/// Rotate once the log passes this size, keeping one previous file.
const MAX_BYTES: u64 = 4 * 1024 * 1024;

/// The directory holding the database and the log.
///
/// Honors `OPENBAD_DB_PATH` so an isolated automation run also gets an isolated
/// log next to its database, instead of writing into the user's data directory.
pub fn data_dir() -> Option<PathBuf> {
    if let Some(path) = std::env::var_os("OPENBAD_DB_PATH") {
        let path = PathBuf::from(path);
        return path.parent().map(Path::to_path_buf);
    }
    directories::ProjectDirs::from("com", "OpenBaD", "OpenBaD Desktop")
        .map(|directories| directories.data_dir().to_path_buf())
}

pub fn log_path() -> Option<PathBuf> {
    data_dir().map(|directory| directory.join("openbad.log"))
}

/// Append one line. Never panics and never fails the caller: logging that can
/// take down the application is worse than no logging.
pub fn record(level: &str, message: &str) {
    let Some(path) = log_path() else {
        return;
    };
    if let Some(parent) = path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    openbad_workspace::rotate_file(&path, MAX_BYTES);
    let Ok(mut file) = OpenOptions::new().create(true).append(true).open(&path) else {
        return;
    };
    let _ = writeln!(file, "{} {level} {message}", timestamp());
}

/// Records startup and any panic, then returns the log path for the caller to
/// mention. Called first in `run`, before anything that can panic.
pub fn init() -> Option<PathBuf> {
    let path = log_path();
    record(
        "INFO",
        &format!(
            "start openbad-desktop {} pid {} on {}",
            env!("CARGO_PKG_VERSION"),
            std::process::id(),
            std::env::consts::OS
        ),
    );
    let hook = panic::take_hook();
    panic::set_hook(Box::new(move |info| {
        let location = info
            .location()
            .map(|location| format!("{}:{}", location.file(), location.line()))
            .unwrap_or_else(|| "unknown location".to_owned());
        let payload = info.payload();
        let message = payload
            .downcast_ref::<&str>()
            .map(|text| (*text).to_owned())
            .or_else(|| payload.downcast_ref::<String>().cloned())
            .unwrap_or_else(|| "non-string panic payload".to_owned());
        record("PANIC", &format!("{message} at {location}"));
        hook(info);
    }));
    path
}

// Windows reads a timestamp only through the file's own metadata, so the log
// carries a UTC stamp computed by hand rather than taking a date dependency.
fn timestamp() -> String {
    let millis = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|elapsed| elapsed.as_millis() as u64)
        .unwrap_or(0);
    let seconds = millis / 1000;
    let days = (seconds / 86_400) as i64;
    let remaining = seconds % 86_400;
    let (year, month, day) = civil_from_days(days);
    format!(
        "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}.{:03}Z",
        remaining / 3600,
        (remaining % 3600) / 60,
        remaining % 60,
        millis % 1000
    )
}

/// Howard Hinnant's days-from-civil, inverted. Valid for the whole range we
/// care about, and exact for leap years.
fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let day_of_era = z - era * 146_097;
    let year_of_era =
        (day_of_era - day_of_era / 1460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let year = year_of_era + era * 400;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let shifted_month = (5 * day_of_year + 2) / 153;
    let day = (day_of_year - (153 * shifted_month + 2) / 5 + 1) as u32;
    let month = if shifted_month < 10 {
        shifted_month + 3
    } else {
        shifted_month - 9
    } as u32;
    (year + i64::from(month <= 2), month, day)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_timestamp_is_a_real_utc_instant() {
        assert_eq!(civil_from_days(0), (1970, 1, 1));
        assert_eq!(civil_from_days(19_723), (2024, 1, 1));
        // 2024 is a leap year, so day 59 lands on 29 February.
        assert_eq!(civil_from_days(19_782), (2024, 2, 29));
        let stamp = timestamp();
        assert_eq!(stamp.len(), 24, "{stamp}");
        assert!(stamp.ends_with('Z'), "{stamp}");
    }

    // One test owns OPENBAD_DB_PATH: two tests setting it in parallel would race.
    #[test]
    fn init_records_startup_and_a_real_panic_beside_the_database() {
        let directory = tempfile::tempdir().expect("directory");
        let database = directory.path().join("openbad.db");
        // SAFETY: no other test in this binary reads the variable, and this test
        // is the only one that sets it.
        unsafe { std::env::set_var("OPENBAD_DB_PATH", &database) };
        let path = init().expect("log path");
        record("INFO", "diagnostics test line");

        let panicked = panic::catch_unwind(|| panic!("the hook records this"));
        assert!(panicked.is_err(), "the panic unwound");

        let written = fs::read_to_string(&path).expect("log file");
        assert!(written.contains("start openbad-desktop"), "{written}");
        assert!(written.contains("diagnostics test line"), "{written}");
        assert!(
            written.contains("PANIC the hook records this at"),
            "{written}"
        );
        unsafe { std::env::remove_var("OPENBAD_DB_PATH") };
    }
}
