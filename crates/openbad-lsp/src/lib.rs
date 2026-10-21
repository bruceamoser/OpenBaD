//! Minimal language-server client over stdio.

use std::collections::HashMap;
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::mpsc::{self, Receiver, RecvTimeoutError};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::Duration;

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use url::Url;

const REQUEST_TIMEOUT: Duration = Duration::from_secs(60);

#[derive(Debug, thiserror::Error)]
pub enum LspError {
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
    #[error("protocol error: {0}")]
    Protocol(String),
    #[error("server error: {0}")]
    Server(String),
    #[error("no language server is configured for {0}")]
    NoServer(String),
    #[error("language server timed out")]
    Timeout,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct LspServerConfig {
    pub language: String,
    pub command: String,
    #[serde(default)]
    pub args: Vec<String>,
    #[serde(default)]
    pub extensions: Vec<String>,
    #[serde(default)]
    pub initialization_options: Value,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct LspConfig {
    #[serde(default)]
    pub servers: Vec<LspServerConfig>,
}

impl LspConfig {
    pub fn load(root: &Path) -> Result<Self, LspError> {
        let path = root.join(".openbad").join("lsp.yaml");
        if !path.is_file() {
            return Ok(Self::default());
        }
        let content = std::fs::read_to_string(path)?;
        serde_yaml::from_str(&content)
            .map_err(|error| LspError::Protocol(format!("invalid .openbad/lsp.yaml: {error}")))
    }

    #[must_use]
    pub fn server_for(&self, path: &Path) -> Option<&LspServerConfig> {
        let extension = path.extension()?.to_str()?;
        self.servers.iter().find(|server| {
            server
                .extensions
                .iter()
                .any(|candidate| candidate.eq_ignore_ascii_case(extension))
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct LspLocation {
    pub path: PathBuf,
    pub line: u32,
    pub character: u32,
}

pub struct LspManager {
    root: PathBuf,
    config: LspConfig,
    clients: Mutex<HashMap<String, Arc<Mutex<LspClient>>>>,
}

impl LspManager {
    pub fn new(root: impl Into<PathBuf>) -> Result<Self, LspError> {
        let root = root.into();
        let config = LspConfig::load(&root)?;
        Ok(Self {
            root,
            config,
            clients: Mutex::new(HashMap::new()),
        })
    }

    pub fn definition(
        &self,
        path: &Path,
        line: u32,
        character: u32,
    ) -> Result<Vec<LspLocation>, LspError> {
        self.locations("textDocument/definition", path, line, character)
    }

    pub fn references(
        &self,
        path: &Path,
        line: u32,
        character: u32,
    ) -> Result<Vec<LspLocation>, LspError> {
        let mut locations = self.definition(path, line, character).unwrap_or_default();
        locations.extend(self.locations("textDocument/references", path, line, character)?);
        locations.sort_by(|left, right| {
            (left.path.clone(), left.line, left.character).cmp(&(
                right.path.clone(),
                right.line,
                right.character,
            ))
        });
        locations.dedup();
        Ok(locations)
    }

    pub fn implementation(
        &self,
        path: &Path,
        line: u32,
        character: u32,
    ) -> Result<Vec<LspLocation>, LspError> {
        self.locations("textDocument/implementation", path, line, character)
    }

    pub fn hover(&self, path: &Path, line: u32, character: u32) -> Result<String, LspError> {
        let path = self.absolute(path);
        let uri = path_to_uri(&path)?;
        let server = self.server_for(&path)?;
        let client = self.client_for(server)?;
        let mut client = client
            .lock()
            .map_err(|_| LspError::Protocol("language server lock poisoned".to_owned()))?;
        let result = client.request(
            "textDocument/hover",
            json!({
                "textDocument": {"uri": uri},
                "position": lsp_position(line, character),
            }),
        )?;
        Ok(hover_text(&result))
    }

    fn locations(
        &self,
        method: &str,
        path: &Path,
        line: u32,
        character: u32,
    ) -> Result<Vec<LspLocation>, LspError> {
        let path = self.absolute(path);
        let uri = path_to_uri(&path)?;
        let server = self.server_for(&path)?;
        let client = self.client_for(server)?;
        let mut client = client
            .lock()
            .map_err(|_| LspError::Protocol("language server lock poisoned".to_owned()))?;
        let result = client.request(
            method,
            json!({
                "textDocument": {"uri": uri},
                "position": lsp_position(line, character),
                "context": {"includeDeclaration": true},
            }),
        )?;
        Ok(parse_locations(&result))
    }

    fn absolute(&self, path: &Path) -> PathBuf {
        if path.is_absolute() {
            path.to_path_buf()
        } else {
            self.root.join(path)
        }
    }

    fn server_for(&self, path: &Path) -> Result<&LspServerConfig, LspError> {
        self.config.server_for(path).ok_or_else(|| {
            LspError::NoServer(
                path.file_name()
                    .and_then(|name| name.to_str())
                    .unwrap_or("file")
                    .to_owned(),
            )
        })
    }

    fn client_for(&self, server: &LspServerConfig) -> Result<Arc<Mutex<LspClient>>, LspError> {
        let mut clients = self
            .clients
            .lock()
            .map_err(|_| LspError::Protocol("language server map lock poisoned".to_owned()))?;
        if let Some(client) = clients.get(&server.language) {
            return Ok(Arc::clone(client));
        }
        let client = Arc::new(Mutex::new(LspClient::start(server, &self.root)?));
        clients.insert(server.language.clone(), Arc::clone(&client));
        Ok(client)
    }
}

impl Drop for LspManager {
    fn drop(&mut self) {
        if let Ok(clients) = self.clients.lock() {
            for client in clients.values() {
                if let Ok(mut client) = client.lock() {
                    client.stop();
                }
            }
        }
    }
}

fn lsp_position(line: u32, character: u32) -> Value {
    json!({
        "line": line.saturating_sub(1),
        "character": character.saturating_sub(1),
    })
}

fn path_to_uri(path: &Path) -> Result<String, LspError> {
    let path = path.canonicalize().unwrap_or_else(|_| path.to_path_buf());
    Url::from_file_path(&path)
        .map(|url| url.to_string())
        .map_err(|()| LspError::Protocol(format!("invalid file path: {}", path.display())))
}

fn uri_to_path(uri: &str) -> Option<PathBuf> {
    Url::parse(uri).ok()?.to_file_path().ok()
}

fn parse_locations(value: &Value) -> Vec<LspLocation> {
    let mut locations = Vec::new();
    match value {
        Value::Null => {}
        Value::Array(items) => {
            for item in items {
                collect_location(item, &mut locations);
            }
        }
        _ => collect_location(value, &mut locations),
    }
    locations.sort_by(|left, right| {
        (left.path.clone(), left.line, left.character).cmp(&(
            right.path.clone(),
            right.line,
            right.character,
        ))
    });
    locations.dedup();
    locations
}

fn collect_location(value: &Value, locations: &mut Vec<LspLocation>) {
    let uri = value
        .get("uri")
        .or_else(|| value.get("targetUri"))
        .and_then(Value::as_str);
    let range = value
        .get("range")
        .or_else(|| value.get("targetSelectionRange"))
        .or_else(|| value.get("targetRange"));
    let Some(path) = uri.and_then(uri_to_path) else {
        return;
    };
    let Some(start) = range.and_then(|range| range.get("start")) else {
        return;
    };
    locations.push(LspLocation {
        path,
        line: start.get("line").and_then(Value::as_u64).unwrap_or(0) as u32 + 1,
        character: start.get("character").and_then(Value::as_u64).unwrap_or(0) as u32 + 1,
    });
}

fn hover_text(value: &Value) -> String {
    let contents = value.get("contents").unwrap_or(value);
    match contents {
        Value::String(text) => text.clone(),
        Value::Array(items) => items
            .iter()
            .map(hover_text)
            .filter(|item| !item.is_empty())
            .collect::<Vec<_>>()
            .join("\n"),
        Value::Object(object) => object
            .get("value")
            .and_then(Value::as_str)
            .or_else(|| object.get("text").and_then(Value::as_str))
            .unwrap_or_default()
            .to_owned(),
        _ => String::new(),
    }
}

struct LspClient {
    child: Child,
    stdin: ChildStdin,
    messages: Receiver<Result<Value, String>>,
    reader: Option<JoinHandle<()>>,
    next_id: i64,
}

impl LspClient {
    fn start(server: &LspServerConfig, root: &Path) -> Result<Self, LspError> {
        let mut child = Command::new(&server.command)
            .args(&server.args)
            .current_dir(root)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|error| {
                LspError::Server(format!(
                    "could not start language server '{}': {error}",
                    server.command
                ))
            })?;
        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| LspError::Protocol("language server stdin unavailable".to_owned()))?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| LspError::Protocol("language server stdout unavailable".to_owned()))?;
        let (sender, messages) = mpsc::channel();
        let reader = thread::Builder::new()
            .name(format!("lsp-reader-{}", server.language))
            .spawn(move || read_loop(stdout, sender))
            .map_err(LspError::Io)?;
        let mut client = Self {
            child,
            stdin,
            messages,
            reader: Some(reader),
            next_id: 1,
        };
        let root_uri = path_to_uri(root)?;
        let mut capabilities = json!({
            "textDocument": {
                "definition": {"linkSupport": true},
                "references": {},
                "implementation": {"linkSupport": true},
                "hover": {"contentFormat": ["markdown", "plaintext"]}
            },
            "workspace": {"configuration": true, "workspaceFolders": true}
        });
        if server.initialization_options.is_object() {
            capabilities["initializationOptions"] = server.initialization_options.clone();
        }
        client.request(
            "initialize",
            json!({
                "processId": std::process::id(),
                "rootUri": root_uri,
                "capabilities": capabilities,
                "workspaceFolders": [{"uri": root_uri, "name": "workspace"}]
            }),
        )?;
        client.notify("initialized", json!({}))?;
        Ok(client)
    }

    fn request(&mut self, method: &str, params: Value) -> Result<Value, LspError> {
        let id = self.next_id;
        self.next_id += 1;
        self.send(json!({
            "jsonrpc": "2.0",
            "id": id,
            "method": method,
            "params": params,
        }))?;
        loop {
            let message = self
                .messages
                .recv_timeout(REQUEST_TIMEOUT)
                .map_err(|error| match error {
                    RecvTimeoutError::Timeout => LspError::Timeout,
                    RecvTimeoutError::Disconnected => {
                        LspError::Server("language server closed its output".to_owned())
                    }
                })?
                .map_err(LspError::Server)?;
            if message.get("method").is_some() {
                if let Some(request_id) = message.get("id") {
                    let result = if message.get("method").and_then(Value::as_str)
                        == Some("workspace/configuration")
                    {
                        json!([])
                    } else {
                        Value::Null
                    };
                    self.send(json!({
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": result,
                    }))?;
                }
                continue;
            }
            if message.get("id").and_then(Value::as_i64) != Some(id) {
                continue;
            }
            if let Some(error) = message.get("error") {
                return Err(LspError::Server(error.to_string()));
            }
            return Ok(message.get("result").cloned().unwrap_or(Value::Null));
        }
    }

    fn notify(&mut self, method: &str, params: Value) -> Result<(), LspError> {
        self.send(json!({"jsonrpc": "2.0", "method": method, "params": params}))
    }

    fn send(&mut self, value: Value) -> Result<(), LspError> {
        let body = serde_json::to_vec(&value)
            .map_err(|error| LspError::Protocol(format!("invalid JSON-RPC message: {error}")))?;
        write!(self.stdin, "Content-Length: {}\r\n\r\n", body.len())?;
        self.stdin.write_all(&body)?;
        self.stdin.flush()?;
        Ok(())
    }

    fn stop(&mut self) {
        let _ = self.notify("shutdown", Value::Null);
        let _ = self.notify("exit", Value::Null);
        let _ = self.child.kill();
        let _ = self.child.wait();
        if let Some(reader) = self.reader.take() {
            let _ = reader.join();
        }
    }
}

impl Drop for LspClient {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn read_loop(stdout: ChildStdout, sender: mpsc::Sender<Result<Value, String>>) {
    let mut stdout = BufReader::new(stdout);
    loop {
        match read_frame(&mut stdout) {
            Ok(Some(value)) => {
                if sender.send(Ok(value)).is_err() {
                    return;
                }
            }
            Ok(None) => return,
            Err(error) => {
                let _ = sender.send(Err(error));
                return;
            }
        }
    }
}

fn read_frame(reader: &mut BufReader<ChildStdout>) -> Result<Option<Value>, String> {
    let mut content_length = None;
    loop {
        let mut line = String::new();
        let read = reader
            .read_line(&mut line)
            .map_err(|error| error.to_string())?;
        if read == 0 {
            return Ok(None);
        }
        let trimmed = line.trim_end();
        if trimmed.is_empty() {
            break;
        }
        if let Some(value) = trimmed.strip_prefix("Content-Length:") {
            content_length = value.trim().parse::<usize>().ok();
        }
    }
    let length = content_length.ok_or_else(|| "missing Content-Length".to_owned())?;
    let mut body = vec![0; length];
    reader
        .read_exact(&mut body)
        .map_err(|error| error.to_string())?;
    serde_json::from_slice(&body)
        .map(Some)
        .map_err(|error| format!("invalid JSON-RPC body: {error}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn positions_convert_to_zero_based_lsp_coordinates() {
        assert_eq!(lsp_position(3, 5), json!({"line": 2, "character": 4}));
    }

    #[test]
    fn locations_parse_plain_and_link_shapes() {
        let value = json!([
            {"uri": "file:///tmp/a.rs", "range": {"start": {"line": 0, "character": 1}}},
            {"targetUri": "file:///tmp/b.rs", "targetSelectionRange": {"start": {"line": 4, "character": 2}}}
        ]);
        let locations = parse_locations(&value);
        assert_eq!(locations.len(), 2);
        assert_eq!(locations[0].line, 1);
        assert_eq!(locations[0].character, 2);
        assert_eq!(locations[1].path, PathBuf::from("/tmp/b.rs"));
        assert_eq!(locations[1].line, 5);
    }

    #[test]
    fn config_selects_servers_by_extension() {
        let config = LspConfig {
            servers: vec![LspServerConfig {
                language: "rust".to_owned(),
                command: "rust-analyzer".to_owned(),
                args: Vec::new(),
                extensions: vec!["rs".to_owned()],
                initialization_options: Value::Null,
            }],
        };
        assert_eq!(
            config
                .server_for(Path::new("src/main.rs"))
                .map(|server| server.language.as_str()),
            Some("rust")
        );
        assert!(config.server_for(Path::new("src/main.ts")).is_none());
    }
}

#[cfg(test)]
mod protocol_tests {
    use super::*;
    use std::fs;
    use tempfile::tempdir;

    #[test]
    fn manager_runs_a_stdio_server() {
        let root = tempdir().expect("tempdir");
        fs::create_dir_all(root.path().join(".openbad")).expect("openbad");
        fs::write(root.path().join("main.rs"), "fn main() {}\n").expect("source");
        let script = root.path().join("mock_lsp.py");
        fs::write(
            &script,
            r#"
import json, sys

def read():
    length = 0
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            break
        if line.lower().startswith(b'content-length:'):
            length = int(line.split(b':', 1)[1])
    return json.loads(sys.stdin.buffer.read(length))

def send(value):
    body = json.dumps(value).encode()
    sys.stdout.buffer.write(b'Content-Length: ' + str(len(body)).encode() + b'\r\n\r\n' + body)
    sys.stdout.buffer.flush()

while True:
    message = read()
    if message is None:
        break
    method = message.get('method')
    if method == 'initialize':
        send({'jsonrpc': '2.0', 'id': message['id'], 'result': {'capabilities': {}}})
    elif method == 'textDocument/definition':
        uri = message['params']['textDocument']['uri']
        send({'jsonrpc': '2.0', 'id': message['id'], 'result': [
            {'uri': uri, 'range': {'start': {'line': 0, 'character': 0}, 'end': {'line': 0, 'character': 1}}}
        ]})
    elif method == 'textDocument/references':
        uri = message['params']['textDocument']['uri']
        send({'jsonrpc': '2.0', 'id': message['id'], 'result': [
            {'uri': uri, 'range': {'start': {'line': 0, 'character': 0}, 'end': {'line': 0, 'character': 1}}},
            {'uri': uri, 'range': {'start': {'line': 1, 'character': 2}, 'end': {'line': 1, 'character': 3}}}
        ]})
    elif method == 'textDocument/hover':
        send({'jsonrpc': '2.0', 'id': message['id'], 'result': {'contents': {'kind': 'markdown', 'value': 'mock hover'}}})
    elif method == 'shutdown':
        send({'jsonrpc': '2.0', 'id': message['id'], 'result': None})
    elif method == 'exit':
        break
"#,
        )
        .expect("script");
        let config = LspConfig {
            servers: vec![LspServerConfig {
                language: "mock".to_owned(),
                command: "python3".to_owned(),
                args: vec![script.to_string_lossy().into_owned()],
                extensions: vec!["rs".to_owned()],
                initialization_options: Value::Null,
            }],
        };
        fs::write(
            root.path().join(".openbad/lsp.yaml"),
            serde_yaml::to_string(&config).expect("yaml"),
        )
        .expect("config");

        let manager = LspManager::new(root.path()).expect("manager");
        let definitions = manager
            .definition(Path::new("main.rs"), 1, 1)
            .expect("definitions");
        assert_eq!(definitions.len(), 1);
        assert_eq!(definitions[0].line, 1);
        assert_eq!(definitions[0].character, 1);
        let references = manager
            .references(Path::new("main.rs"), 1, 1)
            .expect("references");
        assert_eq!(references.len(), 2);
        assert_eq!(
            manager.hover(Path::new("main.rs"), 1, 1).expect("hover"),
            "mock hover"
        );
    }
}
