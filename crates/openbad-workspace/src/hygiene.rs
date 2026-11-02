//! Keeping the workspace's own bookkeeping from growing without bound.
//!
//! Command logs, spill files, and the audit trail are append-only by nature.
//! Nothing here decides what is interesting; it only stops any of them from
//! filling a disk over months of use.

use std::fs;
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

/// How much of a directory to keep.
///
/// Age is the rule that decides; the count and byte ceilings exist only so a
/// single busy window cannot fill a disk, and they keep the newest files.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Retention {
    /// Keep files younger than this. Older files are removed.
    pub max_age: Duration,
    /// Emergency ceiling on the number of files.
    pub max_files: usize,
    /// Emergency ceiling on the total bytes.
    pub max_bytes: u64,
}

impl Retention {
    /// Keep the last `days` of history, with no ceiling.
    #[must_use]
    pub const fn days(days: u64) -> Self {
        Self {
            max_age: Duration::from_secs(days * 86_400),
            max_files: usize::MAX,
            max_bytes: u64::MAX,
        }
    }

    /// Add ceilings, which only take effect if they hold less than the window.
    #[must_use]
    pub const fn with_ceilings(self, max_files: usize, max_bytes: u64) -> Self {
        Self {
            max_files,
            max_bytes,
            ..self
        }
    }
}

/// Deletes the oldest files until the directory holds at most `max_files` and
/// at most `max_bytes`, whichever binds first. Returns how many were removed.
///
/// Best effort by design: a directory that cannot be read, or a file that
/// cannot be deleted, is left alone. Failing to tidy up must never fail the
/// tool call that triggered it.
pub fn prune_directory(directory: &Path, retention: Retention) -> usize {
    let Ok(entries) = fs::read_dir(directory) else {
        return 0;
    };
    let mut files: Vec<(SystemTime, u64, PathBuf)> = entries
        .filter_map(Result::ok)
        .filter_map(|entry| {
            let metadata = entry.metadata().ok()?;
            // Directories and symlinks are never pruned: only plain files that
            // this bookkeeping wrote itself.
            metadata.is_file().then(|| {
                (
                    metadata.modified().unwrap_or(UNIX_EPOCH),
                    metadata.len(),
                    entry.path(),
                )
            })
        })
        .collect();
    // Newest first, so the loop below deletes from the end.
    files.sort_by(|a, b| b.0.cmp(&a.0).then_with(|| b.2.cmp(&a.2)));

    let now = SystemTime::now();
    let mut kept_bytes = 0_u64;
    let mut removed = 0_usize;
    for (index, (modified, bytes, path)) in files.iter().enumerate() {
        // A file stamped in the future is treated as fresh rather than deleted
        // because of a clock that disagrees with itself.
        let age = now.duration_since(*modified).unwrap_or_default();
        let within_age = age <= retention.max_age;
        let within_count = index < retention.max_files;
        let within_bytes = kept_bytes.saturating_add(*bytes) <= retention.max_bytes;
        if within_age && within_count && within_bytes {
            kept_bytes += *bytes;
            continue;
        }
        if fs::remove_file(path).is_ok() {
            removed += 1;
        }
    }
    removed
}

/// Renames `path` to `<name>.1` once it reaches `max_bytes`, replacing any
/// previous archive, so one generation of history is always kept. Returns
/// whether it rotated.
pub fn rotate_file(path: &Path, max_bytes: u64) -> bool {
    let Ok(metadata) = fs::metadata(path) else {
        return false;
    };
    if metadata.len() < max_bytes {
        return false;
    }
    let mut archive = path.as_os_str().to_owned();
    archive.push(".1");
    fs::rename(path, PathBuf::from(archive)).is_ok()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs::File;
    use std::io::Write;
    use std::thread::sleep;
    use std::time::Duration;

    /// Backdates a file so age-based retention can be exercised without waiting.
    fn age_file(path: &Path, age: Duration) {
        let file = fs::OpenOptions::new().write(true).open(path).expect("open");
        let modified = SystemTime::now().checked_sub(age).expect("backdate");
        file.set_times(fs::FileTimes::new().set_modified(modified))
            .expect("set times");
    }

    fn write_file(directory: &Path, name: &str, bytes: usize) {
        let mut file = File::create(directory.join(name)).expect("create");
        file.write_all(&vec![b'x'; bytes]).expect("write");
    }

    /// Stamps distinct modification times: mtime resolution is coarse enough on
    /// some filesystems that writes in the same millisecond would tie.
    fn touch_in_order(directory: &Path, names: &[&str]) {
        for name in names {
            write_file(directory, name, 10);
            sleep(Duration::from_millis(20));
        }
    }

    #[test]
    fn pruning_keeps_the_newest_files_and_removes_the_rest() {
        let directory = tempfile::tempdir().expect("directory");
        touch_in_order(directory.path(), &["a.log", "b.log", "c.log", "d.log"]);

        let removed = prune_directory(
            directory.path(),
            Retention::days(5).with_ceilings(2, u64::MAX),
        );

        assert_eq!(removed, 2);
        let mut kept: Vec<String> = fs::read_dir(directory.path())
            .expect("read")
            .filter_map(Result::ok)
            .map(|entry| entry.file_name().to_string_lossy().into_owned())
            .collect();
        kept.sort();
        assert_eq!(kept, ["c.log", "d.log"]);
    }

    #[test]
    fn logs_older_than_the_window_go_even_when_the_count_has_room() {
        let directory = tempfile::tempdir().expect("directory");
        write_file(directory.path(), "yesterday.log", 10);
        write_file(directory.path(), "last-week.log", 10);
        age_file(
            &directory.path().join("yesterday.log"),
            Duration::from_secs(86_400),
        );
        age_file(
            &directory.path().join("last-week.log"),
            Duration::from_secs(7 * 86_400),
        );

        // No ceiling at all: only age decides.
        let removed = prune_directory(directory.path(), Retention::days(5));

        assert_eq!(removed, 1);
        assert!(
            directory.path().join("yesterday.log").is_file(),
            "inside five days"
        );
        assert!(
            !directory.path().join("last-week.log").exists(),
            "outside five days"
        );
    }

    #[test]
    fn pruning_respects_the_byte_budget_before_the_file_count() {
        let directory = tempfile::tempdir().expect("directory");
        write_file(directory.path(), "old.log", 100);
        sleep(Duration::from_millis(20));
        write_file(directory.path(), "new.log", 100);

        // Two files fit the count but not the budget, so only the newest stays.
        let removed = prune_directory(directory.path(), Retention::days(5).with_ceilings(10, 150));

        assert_eq!(removed, 1);
        assert!(directory.path().join("new.log").is_file());
        assert!(!directory.path().join("old.log").exists());
    }

    #[test]
    fn pruning_leaves_other_content_alone() {
        let directory = tempfile::tempdir().expect("directory");
        fs::create_dir(directory.path().join("nested")).expect("nested");
        write_file(directory.path(), "keep.log", 10);
        write_file(directory.path(), "nested/inside.log", 10);

        prune_directory(directory.path(), Retention::days(0));

        assert!(directory.path().join("nested").is_dir());
        assert!(directory.path().join("nested/inside.log").is_file());
    }

    #[test]
    fn pruning_a_missing_directory_is_not_an_error() {
        let directory = tempfile::tempdir().expect("directory");
        let missing = directory.path().join("absent");
        assert_eq!(prune_directory(&missing, Retention::days(5)), 0);
    }

    #[test]
    fn rotation_keeps_one_archive_and_replaces_it() {
        let directory = tempfile::tempdir().expect("directory");
        let path = directory.path().join("audit.jsonl");

        assert!(!rotate_file(&path, 1024), "absent file does not rotate");
        write_file(directory.path(), "audit.jsonl", 100);
        assert!(!rotate_file(&path, 1024), "small file does not rotate");

        assert!(rotate_file(&path, 50), "oversized file rotates");
        assert!(directory.path().join("audit.jsonl.1").is_file());
        assert!(!path.exists(), "the live file starts empty again");

        write_file(directory.path(), "audit.jsonl", 100);
        write_file(directory.path(), "audit.jsonl.1", 7);
        assert!(rotate_file(&path, 50), "second rotation");
        let archive = fs::read(directory.path().join("audit.jsonl.1")).expect("archive");
        assert_eq!(archive.len(), 100, "the previous archive was replaced");
    }
}
