//! Persistent PTY-backed terminal sessions.

use std::collections::{HashMap, VecDeque};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use portable_pty::{Child, ChildKiller, CommandBuilder, MasterPty, PtySize, native_pty_system};
use thiserror::Error;
use uuid::Uuid;

const MAX_BUFFER_BYTES: usize = 256 * 1024;
const MAX_READ_BYTES: usize = 64 * 1024;

#[derive(Debug, Error)]
pub enum TerminalError {
    #[error("terminal {0} not found")]
    NotFound(String),
    #[error("terminal lock poisoned")]
    Poisoned,
    #[error("pty: {0}")]
    Pty(String),
    #[error("io: {0}")]
    Io(#[from] std::io::Error),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TerminalOutput {
    pub content: String,
    pub running: bool,
    pub truncated: bool,
}

struct Session {
    _master: Mutex<Box<dyn MasterPty + Send>>,
    child: Mutex<Box<dyn Child + Send>>,
    killer: Box<dyn ChildKiller + Send + Sync>,
    writer: Mutex<Box<dyn Write + Send>>,
    buffer: Arc<Mutex<VecDeque<u8>>>,
    dropped: Arc<AtomicBool>,
    process_group: Option<i32>,
}

impl Session {
    fn read_buffer(&self) -> Result<TerminalOutput, TerminalError> {
        let mut buffer = self.buffer.lock().map_err(|_| TerminalError::Poisoned)?;
        let take = buffer.len().min(MAX_READ_BYTES);
        let bytes: Vec<u8> = buffer.drain(..take).collect();
        let truncated = self.dropped.swap(false, Ordering::SeqCst);
        let running = self
            .child
            .lock()
            .map_err(|_| TerminalError::Poisoned)?
            .try_wait()
            .map_err(TerminalError::Io)?
            .is_none();
        Ok(TerminalOutput {
            content: String::from_utf8_lossy(&bytes).into_owned(),
            running,
            truncated,
        })
    }

    fn close(&self) {
        #[cfg(unix)]
        if let Some(group) = self.process_group {
            // The PTY child is a session leader; signal the whole group so
            // shell descendants do not survive the terminal.
            unsafe {
                libc::kill(-group, libc::SIGKILL);
            }
        }
        let _ = self.killer_kill();
        let _ = self
            .child
            .lock()
            .map(|mut child| child.kill().is_ok())
            .unwrap_or(false);
    }

    fn killer_kill(&self) -> std::io::Result<()> {
        let mut killer = self.killer.clone_killer();
        killer.kill()
    }
}

impl Drop for Session {
    fn drop(&mut self) {
        self.close();
    }
}

#[derive(Default)]
pub struct TerminalManager {
    sessions: Mutex<HashMap<String, Arc<Session>>>,
}

impl TerminalManager {
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    pub fn create(&self, cwd: &Path, shell: Option<&str>) -> Result<String, TerminalError> {
        let shell = shell
            .map(str::to_owned)
            .or_else(|| std::env::var("SHELL").ok())
            .unwrap_or_else(|| {
                if cfg!(windows) {
                    "cmd.exe".to_owned()
                } else {
                    "/bin/bash".to_owned()
                }
            });
        let pair = native_pty_system()
            .openpty(PtySize {
                rows: 24,
                cols: 120,
                pixel_width: 0,
                pixel_height: 0,
            })
            .map_err(|error| TerminalError::Pty(error.to_string()))?;
        let mut command = CommandBuilder::new(&shell);
        command.cwd(cwd.as_os_str());
        command.env("TERM", "xterm-256color");
        let child = pair
            .slave
            .spawn_command(command)
            .map_err(|error| TerminalError::Pty(error.to_string()))?;
        drop(pair.slave);
        let killer = child.clone_killer();
        #[cfg(unix)]
        let process_group = pair.master.process_group_leader();
        #[cfg(not(unix))]
        let process_group = None;
        let mut reader = pair
            .master
            .try_clone_reader()
            .map_err(|error| TerminalError::Pty(error.to_string()))?;
        let writer = pair
            .master
            .take_writer()
            .map_err(|error| TerminalError::Pty(error.to_string()))?;
        let buffer = Arc::new(Mutex::new(VecDeque::new()));
        let dropped = Arc::new(AtomicBool::new(false));
        let thread_buffer = Arc::clone(&buffer);
        let thread_dropped = Arc::clone(&dropped);
        thread::spawn(move || {
            let mut chunk = [0u8; 4096];
            loop {
                match reader.read(&mut chunk) {
                    Ok(0) | Err(_) => break,
                    Ok(count) => {
                        if let Ok(mut buffer) = thread_buffer.lock() {
                            buffer.extend(&chunk[..count]);
                            if buffer.len() > MAX_BUFFER_BYTES {
                                let excess = buffer.len() - MAX_BUFFER_BYTES;
                                buffer.drain(..excess);
                                thread_dropped.store(true, Ordering::SeqCst);
                            }
                        } else {
                            break;
                        }
                    }
                }
            }
        });
        let id = Uuid::now_v7().to_string();
        let session = Arc::new(Session {
            _master: Mutex::new(pair.master),
            child: Mutex::new(child),
            killer,
            writer: Mutex::new(writer),
            buffer,
            dropped,
            process_group,
        });
        self.sessions
            .lock()
            .map_err(|_| TerminalError::Poisoned)?
            .insert(id.clone(), session);
        Ok(id)
    }

    pub fn send(&self, id: &str, data: &str) -> Result<(), TerminalError> {
        let session = self.session(id)?;
        let mut writer = session.writer.lock().map_err(|_| TerminalError::Poisoned)?;
        writer.write_all(data.as_bytes())?;
        if !data.ends_with('\r') {
            writer.write_all(b"\r")?;
        }
        writer.flush()?;
        Ok(())
    }

    pub fn read(&self, id: &str) -> Result<TerminalOutput, TerminalError> {
        self.session(id)?.read_buffer()
    }

    pub fn close(&self, id: &str) -> Result<(), TerminalError> {
        let session = self
            .sessions
            .lock()
            .map_err(|_| TerminalError::Poisoned)?
            .remove(id)
            .ok_or_else(|| TerminalError::NotFound(id.to_owned()))?;
        session.close();
        Ok(())
    }

    pub fn close_all(&self) {
        if let Ok(mut sessions) = self.sessions.lock() {
            for (_, session) in sessions.drain() {
                session.close();
            }
        }
    }

    fn session(&self, id: &str) -> Result<Arc<Session>, TerminalError> {
        self.sessions
            .lock()
            .map_err(|_| TerminalError::Poisoned)?
            .get(id)
            .map(Arc::clone)
            .ok_or_else(|| TerminalError::NotFound(id.to_owned()))
    }
}

impl Drop for TerminalManager {
    fn drop(&mut self) {
        self.close_all();
    }
}

/// Wait briefly for `needle` to appear in a terminal's output.
pub fn read_until(
    manager: &TerminalManager,
    id: &str,
    needle: &str,
    timeout: Duration,
) -> Result<TerminalOutput, TerminalError> {
    let deadline = std::time::Instant::now() + timeout;
    let mut collected = String::new();
    loop {
        let output = manager.read(id)?;
        collected.push_str(&output.content);
        if collected.contains(needle) {
            return Ok(TerminalOutput {
                content: collected,
                running: output.running,
                truncated: output.truncated,
            });
        }
        if std::time::Instant::now() >= deadline {
            return Ok(TerminalOutput {
                content: collected,
                running: output.running,
                truncated: output.truncated,
            });
        }
        thread::sleep(Duration::from_millis(25));
    }
}

/// Default shell used by tests and hosts that do not configure one.
#[must_use]
pub fn default_shell() -> PathBuf {
    PathBuf::from(std::env::var("SHELL").unwrap_or_else(|_| "/bin/bash".to_owned()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shell_round_trip_and_close() {
        let directory = tempfile::tempdir().expect("tempdir");
        let manager = TerminalManager::new();
        let id = manager
            .create(directory.path(), Some("/bin/sh"))
            .expect("create");
        manager.send(&id, "printf 'openbad-ready'").expect("send");
        let output =
            read_until(&manager, &id, "openbad-ready", Duration::from_secs(5)).expect("read");
        assert!(output.content.contains("openbad-ready"));
        manager.close(&id).expect("close");
        assert!(manager.read(&id).is_err());
    }

    #[test]
    fn unknown_terminal_is_reported() {
        let manager = TerminalManager::new();
        assert!(matches!(
            manager.read("missing"),
            Err(TerminalError::NotFound(_))
        ));
    }
}
