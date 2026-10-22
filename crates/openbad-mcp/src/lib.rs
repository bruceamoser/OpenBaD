//! MCP server configuration, JSON-RPC clients, and lifecycle primitives.

use std::collections::{BTreeMap, HashMap};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::mpsc::{self, Receiver, RecvTimeoutError};
use std::sync::{Arc, Mutex, RwLock};
use std::thread::{self, JoinHandle};
use std::time::Duration;

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

const DEFAULT_REQUEST_TIMEOUT: Duration = Duration::from_secs(60);
const MAX_REQUEST_TIMEOUT_SECONDS: u64 = 600;

#[derive(Debug, thiserror::Error)]
pub enum McpError {
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
    #[error("protocol error: {0}")]
    Protocol(String),
    #[error("server error: {0}")]
    Server(String),
    #[error("invalid MCP configuration: {0}")]
    InvalidConfig(String),
    #[error("MCP server already exists: {0}")]
    DuplicateServer(String),
    #[error("tool is not allowed on server {server}: {tool}")]
    ToolNotAllowed { server: String, tool: String },
    #[error("unknown MCP server: {0}")]
    UnknownServer(String),
    #[error("MCP call timed out")]
    Timeout,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum McpTransport {
    Stdio,
    Http,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct McpServerConfig {
    pub name: String,
    pub transport: McpTransport,
    #[serde(default)]
    pub command: Option<String>,
    #[serde(default)]
    pub args: Vec<String>,
    #[serde(default)]
    pub env: BTreeMap<String, String>,
    #[serde(default)]
    pub cwd: Option<String>,
    #[serde(default)]
    pub url: Option<String>,
    #[serde(default)]
    pub allowed_tools: Vec<String>,
    #[serde(default = "enabled_by_default")]
    pub enabled: bool,
    #[serde(default)]
    pub timeout_seconds: Option<u64>,
}

const fn enabled_by_default() -> bool {
    true
}

impl McpServerConfig {
    #[must_use]
    pub fn stdio(name: impl Into<String>, command: impl Into<String>) -> Self {
        Self {
            name: name.into(),
            transport: McpTransport::Stdio,
            command: Some(command.into()),
            args: Vec::new(),
            env: BTreeMap::new(),
            cwd: None,
            url: None,
            allowed_tools: Vec::new(),
            enabled: true,
            timeout_seconds: None,
        }
    }

    #[must_use]
    pub fn http(name: impl Into<String>, url: impl Into<String>) -> Self {
        Self {
            name: name.into(),
            transport: McpTransport::Http,
            command: None,
            args: Vec::new(),
            env: BTreeMap::new(),
            cwd: None,
            url: Some(url.into()),
            allowed_tools: Vec::new(),
            enabled: true,
            timeout_seconds: None,
        }
    }

    #[must_use]
    pub fn timeout(&self) -> Duration {
        Duration::from_secs(
            self.timeout_seconds
                .unwrap_or(DEFAULT_REQUEST_TIMEOUT.as_secs())
                .clamp(1, MAX_REQUEST_TIMEOUT_SECONDS),
        )
    }

    pub fn validate(&self) -> Result<(), McpError> {
        let name = self.name.trim();
        if name.is_empty() {
            return Err(McpError::InvalidConfig(
                "server name must not be empty".to_owned(),
            ));
        }
        if !name
            .chars()
            .all(|character| character.is_ascii_alphanumeric() || "._-".contains(character))
        {
            return Err(McpError::InvalidConfig(format!(
                "server name '{name}' may only contain letters, digits, '.', '_', and '-'"
            )));
        }
        match self.transport {
            McpTransport::Stdio => {
                if self
                    .command
                    .as_deref()
                    .map(str::trim)
                    .filter(|command| !command.is_empty())
                    .is_none()
                {
                    return Err(McpError::InvalidConfig(format!(
                        "stdio server '{name}' requires a command"
                    )));
                }
            }
            McpTransport::Http => {
                let url = self.url.as_deref().unwrap_or_default().trim();
                if !(url.starts_with("http://") || url.starts_with("https://")) {
                    return Err(McpError::InvalidConfig(format!(
                        "http server '{name}' requires an http(s) URL"
                    )));
                }
            }
        }
        if let Some(timeout) = self.timeout_seconds {
            if timeout == 0 || timeout > MAX_REQUEST_TIMEOUT_SECONDS {
                return Err(McpError::InvalidConfig(format!(
                    "timeout_seconds must be between 1 and {MAX_REQUEST_TIMEOUT_SECONDS}"
                )));
            }
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct McpConfig {
    #[serde(default)]
    pub servers: Vec<McpServerConfig>,
}

impl McpConfig {
    pub fn load(root: &Path) -> Result<Self, McpError> {
        let path = config_path(root);
        if !path.is_file() {
            return Ok(Self::default());
        }
        let content = std::fs::read_to_string(&path)?;
        let config: Self = serde_yaml::from_str(&content)
            .map_err(|error| McpError::Protocol(format!("invalid .openbad/mcp.yaml: {error}")))?;
        for server in &config.servers {
            server.validate()?;
        }
        if let Some(duplicate) = first_duplicate(&config.servers) {
            return Err(McpError::DuplicateServer(duplicate));
        }
        Ok(config)
    }

    pub fn save(&self, root: &Path) -> Result<(), McpError> {
        let path = config_path(root);
        if let Some(directory) = path.parent() {
            std::fs::create_dir_all(directory)?;
        }
        let content = serde_yaml::to_string(self)
            .map_err(|error| McpError::Protocol(format!("could not encode MCP config: {error}")))?;
        let temporary = path.with_extension("yaml.tmp");
        std::fs::write(&temporary, content)?;
        if std::fs::rename(&temporary, &path).is_err() {
            // Windows refuses to rename over an existing file.
            if path.exists() {
                std::fs::remove_file(&path)?;
            }
            std::fs::rename(&temporary, &path)?;
        }
        Ok(())
    }

    pub fn server(&self, name: &str) -> Option<&McpServerConfig> {
        self.servers.iter().find(|server| server.name == name)
    }

    pub fn server_mut(&mut self, name: &str) -> Option<&mut McpServerConfig> {
        self.servers.iter_mut().find(|server| server.name == name)
    }
}

/// Absolute path to the workspace-scoped MCP configuration file.
#[must_use]
pub fn config_path(root: &Path) -> PathBuf {
    root.join(".openbad").join("mcp.yaml")
}

fn first_duplicate(servers: &[McpServerConfig]) -> Option<String> {
    let mut seen = std::collections::HashSet::new();
    servers
        .iter()
        .find(|server| !seen.insert(server.name.as_str()))
        .map(|server| server.name.clone())
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct McpToolInfo {
    pub server: String,
    pub name: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub input_schema: Value,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct ConfigStamp {
    length: u64,
    hash: u64,
}

fn config_stamp(root: &Path) -> Option<ConfigStamp> {
    let content = std::fs::read(config_path(root)).ok()?;
    let mut hash = 0xcbf2_9ce4_8422_2325_u64;
    for byte in &content {
        hash ^= u64::from(*byte);
        hash = hash.wrapping_mul(0x0000_0100_0000_01b3);
    }
    Some(ConfigStamp {
        length: content.len() as u64,
        hash,
    })
}

/// Loads and manages workspace-scoped MCP servers.
///
/// Management methods persist to `.openbad/mcp.yaml` and apply in memory, so a
/// server added by the model in one tool call is callable in the next.
pub struct McpManager {
    root: PathBuf,
    config: RwLock<McpConfig>,
    clients: Mutex<HashMap<String, Arc<Mutex<McpClient>>>>,
    stamp: Mutex<Option<ConfigStamp>>,
}

impl McpManager {
    pub fn new(root: &Path) -> Result<Self, McpError> {
        Ok(Self {
            root: root.to_path_buf(),
            config: RwLock::new(McpConfig::load(root)?),
            clients: Mutex::new(HashMap::new()),
            stamp: Mutex::new(config_stamp(root)),
        })
    }

    #[must_use]
    pub fn root(&self) -> &Path {
        &self.root
    }

    /// Reloads the config when the file changed on disk, for example after
    /// another manager or the settings UI updated it.
    pub fn reload_if_changed(&self) -> Result<(), McpError> {
        let current = config_stamp(&self.root);
        {
            let stamp = self
                .stamp
                .lock()
                .map_err(|_| McpError::Protocol("MCP config stamp lock poisoned".to_owned()))?;
            if *stamp == current {
                return Ok(());
            }
        }
        self.reload()
    }

    pub fn reload(&self) -> Result<(), McpError> {
        let loaded = McpConfig::load(&self.root)?;
        let changed = {
            let mut config = self
                .config
                .write()
                .map_err(|_| McpError::Protocol("MCP config lock poisoned".to_owned()))?;
            let changed = config
                .servers
                .iter()
                .filter(|server| loaded.server(&server.name) != Some(*server))
                .map(|server| server.name.clone())
                .collect::<Vec<_>>();
            *config = loaded;
            changed
        };
        for name in changed {
            self.stop_client(&name);
        }
        self.record_stamp();
        Ok(())
    }

    pub fn servers(&self) -> Result<Vec<McpServerConfig>, McpError> {
        self.reload_if_changed()?;
        let config = self
            .config
            .read()
            .map_err(|_| McpError::Protocol("MCP config lock poisoned".to_owned()))?;
        Ok(config.servers.clone())
    }

    pub fn add_server(&self, server: McpServerConfig) -> Result<McpServerConfig, McpError> {
        server.validate()?;
        self.reload_if_changed()?;
        let mut config = self
            .config
            .write()
            .map_err(|_| McpError::Protocol("MCP config lock poisoned".to_owned()))?;
        if config.server(&server.name).is_some() {
            return Err(McpError::DuplicateServer(server.name));
        }
        let mut updated = config.clone();
        updated.servers.push(server.clone());
        self.persist(&updated, &mut config)?;
        Ok(server)
    }

    pub fn update_server(
        &self,
        name: &str,
        server: McpServerConfig,
    ) -> Result<McpServerConfig, McpError> {
        if server.name != name {
            return Err(McpError::InvalidConfig(
                "renaming an MCP server is not supported; remove and add it instead".to_owned(),
            ));
        }
        server.validate()?;
        self.reload_if_changed()?;
        let mut config = self
            .config
            .write()
            .map_err(|_| McpError::Protocol("MCP config lock poisoned".to_owned()))?;
        let mut updated = config.clone();
        let existing = updated
            .server_mut(name)
            .ok_or_else(|| McpError::UnknownServer(name.to_owned()))?;
        *existing = server.clone();
        self.persist(&updated, &mut config)?;
        drop(config);
        self.stop_client(name);
        Ok(server)
    }

    pub fn remove_server(&self, name: &str) -> Result<(), McpError> {
        self.reload_if_changed()?;
        let mut config = self
            .config
            .write()
            .map_err(|_| McpError::Protocol("MCP config lock poisoned".to_owned()))?;
        let mut updated = config.clone();
        let before = updated.servers.len();
        updated.servers.retain(|server| server.name != name);
        if updated.servers.len() == before {
            return Err(McpError::UnknownServer(name.to_owned()));
        }
        self.persist(&updated, &mut config)?;
        drop(config);
        self.stop_client(name);
        Ok(())
    }

    pub fn set_server_enabled(
        &self,
        name: &str,
        enabled: bool,
    ) -> Result<McpServerConfig, McpError> {
        self.reload_if_changed()?;
        let mut config = self
            .config
            .write()
            .map_err(|_| McpError::Protocol("MCP config lock poisoned".to_owned()))?;
        let mut updated = config.clone();
        let server = updated
            .server_mut(name)
            .ok_or_else(|| McpError::UnknownServer(name.to_owned()))?;
        server.enabled = enabled;
        let server = server.clone();
        self.persist(&updated, &mut config)?;
        drop(config);
        if !enabled {
            self.stop_client(name);
        }
        Ok(server)
    }

    /// Lists every tool exposed by one configured server.
    pub fn probe(&self, name: &str) -> Result<Vec<McpToolInfo>, McpError> {
        self.reload_if_changed()?;
        let server = {
            let config = self
                .config
                .read()
                .map_err(|_| McpError::Protocol("MCP config lock poisoned".to_owned()))?;
            config
                .server(name)
                .cloned()
                .ok_or_else(|| McpError::UnknownServer(name.to_owned()))?
        };
        self.list_server_tools(&server)
    }

    pub fn catalog(&self) -> Result<Vec<McpToolInfo>, McpError> {
        self.reload_if_changed()?;
        let servers = {
            let config = self
                .config
                .read()
                .map_err(|_| McpError::Protocol("MCP config lock poisoned".to_owned()))?;
            config
                .servers
                .iter()
                .filter(|server| server.enabled)
                .cloned()
                .collect::<Vec<_>>()
        };
        let mut tools = Vec::new();
        for server in servers {
            if let Ok(mut found) = self.list_server_tools(&server) {
                tools.append(&mut found);
            }
        }
        Ok(tools)
    }

    pub fn call(
        &self,
        server_name: &str,
        tool: &str,
        arguments: Value,
    ) -> Result<String, McpError> {
        self.reload_if_changed()?;
        let server = {
            let config = self
                .config
                .read()
                .map_err(|_| McpError::Protocol("MCP config lock poisoned".to_owned()))?;
            config
                .server(server_name)
                .cloned()
                .ok_or_else(|| McpError::UnknownServer(server_name.to_owned()))?
        };
        if !server.enabled {
            return Err(McpError::Server(format!(
                "MCP server '{}' is disabled",
                server.name
            )));
        }
        if !server.allowed_tools.is_empty()
            && !server.allowed_tools.iter().any(|allowed| allowed == tool)
        {
            return Err(McpError::ToolNotAllowed {
                server: server_name.to_owned(),
                tool: tool.to_owned(),
            });
        }
        let result = match server.transport {
            McpTransport::Stdio => {
                let client = self.client_for(&server)?;
                let mut client = client
                    .lock()
                    .map_err(|_| McpError::Protocol("MCP client lock poisoned".to_owned()))?;
                client.request("tools/call", json!({"name": tool, "arguments": arguments}))?
            }
            McpTransport::Http => self.http_request(
                &server,
                "tools/call",
                json!({"name": tool, "arguments": arguments}),
            )?,
        };
        Ok(render_mcp_result(&result))
    }

    fn list_server_tools(&self, server: &McpServerConfig) -> Result<Vec<McpToolInfo>, McpError> {
        let result = match server.transport {
            McpTransport::Stdio => {
                let client = self.client_for(server)?;
                let mut client = client
                    .lock()
                    .map_err(|_| McpError::Protocol("MCP client lock poisoned".to_owned()))?;
                client.request("tools/list", json!({}))?
            }
            McpTransport::Http => self.http_request(server, "tools/list", json!({}))?,
        };
        let mut tools = Vec::new();
        for tool in result
            .get("tools")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
        {
            let name = tool.get("name").and_then(Value::as_str).unwrap_or_default();
            if !server.allowed_tools.is_empty()
                && !server.allowed_tools.iter().any(|allowed| allowed == name)
            {
                continue;
            }
            tools.push(McpToolInfo {
                server: server.name.clone(),
                name: name.to_owned(),
                description: tool
                    .get("description")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_owned(),
                input_schema: tool
                    .get("inputSchema")
                    .cloned()
                    .unwrap_or_else(|| json!({})),
            });
        }
        Ok(tools)
    }

    fn persist(&self, updated: &McpConfig, config: &mut McpConfig) -> Result<(), McpError> {
        updated.save(&self.root)?;
        *config = updated.clone();
        self.record_stamp();
        Ok(())
    }

    fn record_stamp(&self) {
        if let Ok(mut stamp) = self.stamp.lock() {
            *stamp = config_stamp(&self.root);
        }
    }

    fn stop_client(&self, name: &str) {
        let client = self
            .clients
            .lock()
            .ok()
            .and_then(|mut clients| clients.remove(name));
        if let Some(client) = client {
            if let Ok(mut client) = client.lock() {
                client.stop();
            }
        }
    }

    fn client_for(&self, server: &McpServerConfig) -> Result<Arc<Mutex<McpClient>>, McpError> {
        let mut clients = self
            .clients
            .lock()
            .map_err(|_| McpError::Protocol("MCP client map lock poisoned".to_owned()))?;
        if let Some(client) = clients.get(&server.name) {
            return Ok(Arc::clone(client));
        }
        let client = Arc::new(Mutex::new(McpClient::start_stdio(server)?));
        clients.insert(server.name.clone(), Arc::clone(&client));
        Ok(client)
    }

    fn http_request(
        &self,
        server: &McpServerConfig,
        method: &str,
        params: Value,
    ) -> Result<Value, McpError> {
        let url = server
            .url
            .as_deref()
            .ok_or_else(|| McpError::Protocol("HTTP MCP server has no URL".to_owned()))?;
        let client = reqwest::blocking::Client::builder()
            .timeout(server.timeout())
            .build()
            .map_err(|error| McpError::Protocol(error.to_string()))?;
        let response = client
            .post(url)
            .json(&json!({
                "jsonrpc": "2.0",
                "id": 1,
                "method": method,
                "params": params,
            }))
            .send()
            .map_err(|error| McpError::Server(error.to_string()))?;
        let value: Value = response
            .json()
            .map_err(|error| McpError::Protocol(error.to_string()))?;
        if let Some(error) = value.get("error") {
            return Err(McpError::Server(error.to_string()));
        }
        Ok(value.get("result").cloned().unwrap_or(Value::Null))
    }
}

impl Drop for McpManager {
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

fn render_mcp_result(result: &Value) -> String {
    if let Some(content) = result.get("content").and_then(Value::as_array) {
        let text = content
            .iter()
            .filter_map(|item| {
                item.get("text").and_then(Value::as_str).or_else(|| {
                    item.get("resource")
                        .and_then(|resource| resource.get("text"))
                        .and_then(Value::as_str)
                })
            })
            .collect::<Vec<_>>()
            .join("\n");
        if !text.is_empty() {
            return text;
        }
    }
    if let Some(text) = result.get("text").and_then(Value::as_str) {
        return text.to_owned();
    }
    result.to_string()
}

struct McpClient {
    child: Child,
    stdin: ChildStdin,
    messages: Receiver<Result<Value, String>>,
    reader: Option<JoinHandle<()>>,
    next_id: i64,
    timeout: Duration,
}

impl McpClient {
    fn start_stdio(server: &McpServerConfig) -> Result<Self, McpError> {
        let command = server
            .command
            .as_deref()
            .ok_or_else(|| McpError::Protocol("stdio MCP server has no command".to_owned()))?;
        let mut process = Command::new(command);
        process
            .args(&server.args)
            .envs(resolve_env(&server.env)?)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        if let Some(cwd) = server.cwd.as_deref().filter(|cwd| !cwd.is_empty()) {
            process.current_dir(cwd);
        }
        let mut child = process.spawn().map_err(|error| {
            McpError::Server(format!("could not start MCP server '{command}': {error}"))
        })?;
        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| McpError::Protocol("MCP stdin unavailable".to_owned()))?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| McpError::Protocol("MCP stdout unavailable".to_owned()))?;
        let (sender, messages) = mpsc::channel();
        let reader = thread::Builder::new()
            .name(format!("mcp-reader-{}", server.name))
            .spawn(move || read_loop(stdout, sender))
            .map_err(McpError::Io)?;
        let mut client = Self {
            child,
            stdin,
            messages,
            reader: Some(reader),
            next_id: 1,
            timeout: server.timeout(),
        };
        client.request(
            "initialize",
            json!({
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "OpenBaD", "version": env!("CARGO_PKG_VERSION")}
            }),
        )?;
        client.notify("notifications/initialized", json!({}))?;
        Ok(client)
    }

    fn request(&mut self, method: &str, params: Value) -> Result<Value, McpError> {
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
                .recv_timeout(self.timeout)
                .map_err(|error| match error {
                    RecvTimeoutError::Timeout => McpError::Timeout,
                    RecvTimeoutError::Disconnected => {
                        McpError::Server("MCP server closed its output".to_owned())
                    }
                })?
                .map_err(McpError::Server)?;
            if message.get("method").is_some() {
                if let Some(request_id) = message.get("id") {
                    self.send(json!({
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": null,
                    }))?;
                }
                continue;
            }
            if message.get("id").and_then(Value::as_i64) != Some(id) {
                continue;
            }
            if let Some(error) = message.get("error") {
                return Err(McpError::Server(error.to_string()));
            }
            return Ok(message.get("result").cloned().unwrap_or(Value::Null));
        }
    }

    fn notify(&mut self, method: &str, params: Value) -> Result<(), McpError> {
        self.send(json!({"jsonrpc": "2.0", "method": method, "params": params}))
    }

    fn send(&mut self, value: Value) -> Result<(), McpError> {
        serde_json::to_writer(&mut self.stdin, &value)
            .map_err(|error| McpError::Protocol(error.to_string()))?;
        self.stdin.write_all(b"\n")?;
        self.stdin.flush()?;
        Ok(())
    }

    fn stop(&mut self) {
        let _ = self.notify("shutdown", json!({}));
        let _ = self.child.kill();
        let _ = self.child.wait();
        if let Some(reader) = self.reader.take() {
            let _ = reader.join();
        }
    }
}

impl Drop for McpClient {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

/// Expands `${NAME}` references from the host environment. Literal values pass
/// through unchanged, so the settings UI can still store a local token.
fn resolve_env(env: &BTreeMap<String, String>) -> Result<BTreeMap<String, String>, McpError> {
    env.iter()
        .map(|(key, value)| Ok((key.clone(), expand_env(value)?)))
        .collect()
}

fn expand_env(value: &str) -> Result<String, McpError> {
    let mut expanded = String::with_capacity(value.len());
    let mut rest = value;
    while let Some(start) = rest.find("${") {
        expanded.push_str(&rest[..start]);
        let after = &rest[start + 2..];
        let Some(end) = after.find('}') else {
            return Err(McpError::InvalidConfig(format!(
                "unterminated environment reference in '{value}'"
            )));
        };
        let name = &after[..end];
        let resolved = std::env::var(name).map_err(|_| {
            McpError::InvalidConfig(format!(
                "environment variable '{name}' is referenced by the MCP config but is not set"
            ))
        })?;
        expanded.push_str(&resolved);
        rest = &after[end + 1..];
    }
    expanded.push_str(rest);
    Ok(expanded)
}

fn read_loop(stdout: ChildStdout, sender: mpsc::Sender<Result<Value, String>>) {
    let reader = BufReader::new(stdout);
    for line in reader.lines() {
        match line {
            Ok(line) if line.trim().is_empty() => continue,
            Ok(line) => match serde_json::from_str(&line) {
                Ok(value) => {
                    if sender.send(Ok(value)).is_err() {
                        return;
                    }
                }
                Err(error) => {
                    let _ = sender.send(Err(format!("invalid MCP JSON: {error}")));
                    return;
                }
            },
            Err(error) => {
                let _ = sender.send(Err(error.to_string()));
                return;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::tempdir;

    fn write_mock_script(root: &Path) -> String {
        let script = root.join("mock_mcp.py");
        fs::write(
            &script,
            r#"
import json, sys, os
for line in sys.stdin:
    message = json.loads(line)
    method = message.get('method')
    if method == 'initialize':
        print(json.dumps({'jsonrpc':'2.0','id':message['id'],'result':{'protocolVersion':'2024-11-05','capabilities':{}}}), flush=True)
    elif method == 'tools/list':
        print(json.dumps({'jsonrpc':'2.0','id':message['id'],'result':{'tools':[{'name':'echo','description':'Echo ' + os.environ.get('MOCK_LABEL', ''),'inputSchema':{'type':'object'}},{'name':'hidden','description':'Hidden','inputSchema':{'type':'object'}}]}}), flush=True)
    elif method == 'tools/call':
        print(json.dumps({'jsonrpc':'2.0','id':message['id'],'result':{'content':[{'type':'text','text':'called ' + message['params']['name'] + ' ' + os.environ.get('MOCK_LABEL', '')}]}}), flush=True)
"#,
        )
        .expect("script");
        script.to_string_lossy().into_owned()
    }

    fn mock_server(script: &str) -> McpServerConfig {
        let mut server = McpServerConfig::stdio("mock", "python3");
        server.args = vec![script.to_owned()];
        server.env.insert("MOCK_LABEL".to_owned(), "one".to_owned());
        server.allowed_tools = vec!["echo".to_owned()];
        server
    }

    #[test]
    fn stdio_servers_require_a_command() {
        let server = McpServerConfig::stdio("files", "mcp-files");
        assert_eq!(server.transport, McpTransport::Stdio);
        assert!(server.command.is_some());
        assert!(server.url.is_none());
        assert!(server.enabled);
        server.validate().expect("valid");
    }

    #[test]
    fn validation_rejects_bad_names_and_missing_endpoints() {
        let mut server = McpServerConfig::stdio("bad name", "cmd");
        assert!(matches!(server.validate(), Err(McpError::InvalidConfig(_))));
        server.name = "ok".to_owned();
        server.command = None;
        assert!(matches!(server.validate(), Err(McpError::InvalidConfig(_))));
        assert!(matches!(
            McpServerConfig::http("remote", "ftp://example.com").validate(),
            Err(McpError::InvalidConfig(_))
        ));
        McpServerConfig::http("remote", "https://example.com/rpc")
            .validate()
            .expect("valid http");
    }

    #[test]
    fn environment_values_expand_from_the_host() {
        assert_eq!(expand_env("plain").expect("plain"), "plain");
        assert!(expand_env("${OPENBAD_MISSING_ENV_FOR_TEST}").is_err());
        assert!(expand_env("${unterminated").is_err());
    }

    #[test]
    fn config_round_trips_and_preserves_defaults() {
        let root = tempdir().expect("tempdir");
        let config = McpConfig {
            servers: vec![
                McpServerConfig::stdio("docs", "npx"),
                McpServerConfig::http("remote", "https://example.com/rpc"),
            ],
        };
        config.save(root.path()).expect("save");
        let text = fs::read_to_string(config_path(root.path())).expect("read");
        assert!(text.contains("docs"));
        let loaded = McpConfig::load(root.path()).expect("load");
        assert_eq!(loaded, config);
    }

    #[test]
    fn manager_adds_updates_removes_and_persists_servers() {
        let root = tempdir().expect("tempdir");
        fs::create_dir_all(root.path().join(".openbad")).expect("dir");
        let manager = McpManager::new(root.path()).expect("manager");
        assert!(manager.servers().expect("servers").is_empty());

        let mut server = McpServerConfig::stdio("docs", "npx");
        server.args = vec!["-y".to_owned(), "@example/mcp-docs".to_owned()];
        manager.add_server(server.clone()).expect("add");
        assert!(matches!(
            manager.add_server(server.clone()),
            Err(McpError::DuplicateServer(_))
        ));

        let mut updated = server.clone();
        updated.allowed_tools = vec!["search".to_owned()];
        updated.timeout_seconds = Some(30);
        manager
            .update_server("docs", updated.clone())
            .expect("update");
        assert_eq!(manager.servers().expect("servers"), vec![updated.clone()]);

        manager.set_server_enabled("docs", false).expect("disable");
        assert!(!manager.servers().expect("servers")[0].enabled);

        let reloaded = McpConfig::load(root.path()).expect("load");
        assert_eq!(reloaded.servers, manager.servers().expect("servers"));
        assert_eq!(reloaded.servers[0].timeout(), Duration::from_secs(30));

        manager.remove_server("docs").expect("remove");
        assert!(manager.servers().expect("servers").is_empty());
        assert!(matches!(
            manager.remove_server("docs"),
            Err(McpError::UnknownServer(_))
        ));
        assert!(
            McpConfig::load(root.path())
                .expect("load")
                .servers
                .is_empty()
        );
    }

    #[test]
    fn manager_reloads_changes_written_by_another_instance() {
        let root = tempdir().expect("tempdir");
        fs::create_dir_all(root.path().join(".openbad")).expect("dir");
        let first = McpManager::new(root.path()).expect("first");
        let second = McpManager::new(root.path()).expect("second");
        assert!(second.servers().expect("servers").is_empty());

        first
            .add_server(McpServerConfig::stdio("docs", "npx"))
            .expect("add");
        let servers = second.servers().expect("reload");
        assert_eq!(servers.len(), 1);
        assert_eq!(servers[0].name, "docs");
    }

    #[test]
    fn disabled_servers_are_skipped_by_the_catalog() {
        let root = tempdir().expect("tempdir");
        fs::create_dir_all(root.path().join(".openbad")).expect("dir");
        let script = write_mock_script(root.path());
        let manager = McpManager::new(root.path()).expect("manager");
        manager.add_server(mock_server(&script)).expect("add");
        assert_eq!(manager.catalog().expect("catalog").len(), 1);

        manager.set_server_enabled("mock", false).expect("disable");
        assert!(manager.catalog().expect("catalog").is_empty());
        assert!(manager.call("mock", "echo", json!({})).is_err());
        assert_eq!(manager.probe("mock").expect("probe").len(), 1);
    }

    #[test]
    fn stdio_client_lists_and_calls_tools() {
        let root = tempdir().expect("tempdir");
        fs::create_dir_all(root.path().join(".openbad")).expect("dir");
        let script = write_mock_script(root.path());
        let manager = McpManager::new(root.path()).expect("manager");
        manager.add_server(mock_server(&script)).expect("add");

        let catalog = manager.catalog().expect("catalog");
        assert_eq!(catalog.len(), 1);
        assert_eq!(catalog[0].name, "echo");
        assert_eq!(catalog[0].description, "Echo one");
        assert_eq!(
            manager
                .call("mock", "echo", json!({"value": "hi"}))
                .expect("call"),
            "called echo one"
        );
        assert!(manager.call("mock", "hidden", json!({})).is_err());
        assert!(matches!(
            manager.call("missing", "echo", json!({})),
            Err(McpError::UnknownServer(_))
        ));
    }
}
