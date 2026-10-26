use std::io::Read;
use std::process::{Command, Output, Stdio};
use std::time::{Duration, Instant};

/// Runs a command to completion with a hard deadline.
///
/// Both pipes are drained on their own threads while waiting: a child that
/// fills the OS pipe buffer blocks in `write(2)` and would never exit, which
/// turns a merely slow command into a permanent hang. `label` is used for
/// error messages and should read like `git status`.
pub fn run_bounded(
    command: &mut Command,
    timeout: Duration,
    label: &str,
) -> Result<Output, String> {
    let mut child = command
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|error| format!("could not run {label}: {error}"))?;
    let stdout = child.stdout.take().map(drain);
    let stderr = child.stderr.take().map(drain);
    let started = Instant::now();
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status,
            Ok(None) if started.elapsed() < timeout => {
                std::thread::sleep(Duration::from_millis(20));
            }
            Ok(None) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(format!("{label} timed out after {}s", timeout.as_secs()));
            }
            Err(error) => return Err(format!("{label} failed: {error}")),
        }
    };
    Ok(Output {
        status,
        stdout: collect(stdout),
        stderr: collect(stderr),
    })
}

fn drain<R: Read + Send + 'static>(mut reader: R) -> std::thread::JoinHandle<Vec<u8>> {
    std::thread::spawn(move || {
        let mut buffer = Vec::new();
        let _ = reader.read_to_end(&mut buffer);
        buffer
    })
}

fn collect(handle: Option<std::thread::JoinHandle<Vec<u8>>>) -> Vec<u8> {
    handle
        .and_then(|handle| handle.join().ok())
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn times_out_a_hung_command() {
        let mut command = Command::new("sleep");
        command.arg("30");
        let started = Instant::now();
        let error = run_bounded(&mut command, Duration::from_millis(150), "sleep")
            .expect_err("a hung command must time out");
        assert!(error.contains("timed out"), "{error}");
        assert!(
            started.elapsed() < Duration::from_secs(10),
            "the timeout must kill the child promptly"
        );
    }

    /// Regression: output larger than the OS pipe buffer used to deadlock
    /// because the pipe was never drained while polling.
    #[test]
    fn drains_output_larger_than_the_pipe_buffer() {
        let mut command = Command::new("sh");
        command
            .arg("-c")
            .arg("head -c 300000 /dev/zero | tr '\\0' 'a'");
        let output = run_bounded(&mut command, Duration::from_secs(20), "producer")
            .expect("producer output");
        assert!(output.status.success());
        assert_eq!(output.stdout.len(), 300_000);
    }
}
