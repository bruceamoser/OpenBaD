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

/// Stops GLib criticals from killing the window.
///
/// GTK reaches into GSettings while opening the display, and a desktop whose
/// schemas disagree — or a recent-files list pointing at a CIFS share that
/// returns no size — makes it raise a critical. GLib aborts on a *fatal* level
/// and skips every handler while doing so, so registering a handler is not
/// enough: the fatal mask has to be narrowed first.
///
/// `g_error` stays fatal on purpose. It is GLib's "this cannot continue"
/// contract, and the app should not pretend otherwise.
#[cfg(target_os = "linux")]
pub fn init_glib_logging() {
    let previous = glib::log_set_always_fatal(glib::LogLevels::LEVEL_ERROR);
    if previous.contains(glib::LogLevels::LEVEL_CRITICAL) {
        record(
            "WARN",
            &format!(
                "glib treated criticals as fatal ({previous:?}); narrowed to errors so a \
                 desktop-integration warning cannot kill the window"
            ),
        );
    }
    // The default handler is the one that runs for every domain and every
    // level, which is what routes "GLib-GIO" and friends here instead of to the
    // handler that aborts.
    glib::log_set_default_handler(|domain, level, message| {
        record(
            "GLIB",
            &format!("{} {level:?}: {message}", domain.unwrap_or("GLib")),
        );
    });
}

#[cfg(not(target_os = "linux"))]
pub fn init_glib_logging() {}

/// Handles `--version` and `--help` before anything touches the display.
///
/// Returns the exit code when the process should stop there. This used to reach
/// GTK, which aborted on a desktop-integration `g_error` — so the version probe
/// needed a working session bus, a display, and a compatible schema set.
pub fn early_exit_for(arguments: &[String]) -> Option<i32> {
    for argument in arguments {
        match argument.as_str() {
            "--version" | "-V" => {
                println!("openbad-desktop {}", env!("CARGO_PKG_VERSION"));
                return Some(0);
            }
            "--help" | "-h" => {
                println!(
                    "openbad-desktop {}\n\nUsage: openbad-desktop [--version] [--help]\n\nWith no flag, opens the desktop application.",
                    env!("CARGO_PKG_VERSION")
                );
                return Some(0);
            }
            _ => {}
        }
    }
    None
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

    // One test owns OPENBAD_DB_PATH: two setting it in parallel would race. It
    // also proves the crash behind the core dumps cannot recur — a critical that
    // GLib considered fatal used to abort the process, and would abort this test
    // binary if the narrowing were removed.
    #[test]
    fn startup_panics_and_fatal_criticals_all_reach_the_log() {
        let directory = tempfile::tempdir().expect("directory");
        let database = directory.path().join("openbad.db");
        // SAFETY: no other test in this binary reads the variable, and this test
        // is the only one that sets it.
        unsafe { std::env::set_var("OPENBAD_DB_PATH", &database) };
        let path = init().expect("log path");
        init_glib_logging();
        record("INFO", "diagnostics test line");

        let panicked = panic::catch_unwind(|| panic!("the hook records this"));
        assert!(panicked.is_err(), "the panic unwound");

        #[cfg(target_os = "linux")]
        glib::g_critical!("openbad-test", "a critical the window must survive");

        let written = fs::read_to_string(&path).expect("log file");
        assert!(written.contains("start openbad-desktop"), "{written}");
        assert!(written.contains("diagnostics test line"), "{written}");
        assert!(
            written.contains("PANIC the hook records this at"),
            "{written}"
        );
        #[cfg(target_os = "linux")]
        assert!(
            written.contains("a critical the window must survive"),
            "the critical was logged instead of aborting: {written}"
        );
        unsafe { std::env::remove_var("OPENBAD_DB_PATH") };
    }
}
