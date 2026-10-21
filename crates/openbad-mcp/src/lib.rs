//! MCP server configuration, JSON-RPC clients, and lifecycle primitives.

use std::collections::HashMap;
use std::io::{BufRead, BufReader, Write};
use std::path::Path;
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::mpsc::{self, Receiver, RecvTimeoutError};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::Duration;

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

const REQUEST_TIMEOUT: Duration = Duration::from_secs(60);

#[derive(Debug, thiserror::Error)]
pub enum McpError {
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
    #[error("protocol error: {0}")]
    Protocol(String),
    #[error("server error: {0}")]
    Server(String),
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
    pub url: Option<String>,
    #[serde(default)]
    pub allowed_tools: Vec<String>,
}

impl McpServerConfig {
    #[must_use]
    pub fn stdio(name: impl Into<String>, command: impl Into<String>) -> Self {
        Self {
            name: name.into(),
            transport: McpTransport::Stdio,
            command: Some(command.into()),
            args: Vec::new(),
            url: None,
            allowed_tools: Vec::new(),
        }
    }

    #[must_use]
    pub fn http(name: impl Into<String>, url: impl Into<String>) -> Self {
        Self {
            name: name.into(),
            transport: McpTransport::Http,
            command: None,
            args: Vec::new(),
            url: Some(url.into()),
            allowed_tools: Vec::new(),
        }
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct McpConfig {
    #[serde(default)]
    pub servers: Vec<McpServerConfig>,
}

impl McpConfig {
    pub fn load(root: &Path) -> Result<Self, McpError> {
        let path = root.join(".openbad").join("mcp.yaml");
        if !path.is_file() {
            return Ok(Self::default());
        }
        let content = std::fs::read_to_string(path)?;
        serde_yaml::from_str(&content)
            .map_err(|error| McpError::Protocol(format!("invalid .openbad/mcp.yaml: {error}")))
    }
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

pub struct McpManager {
    config: McpConfig,
    clients: Mutex<HashMap<String, Arc<Mutex<McpClient>>>>,
    http: reqwest::blocking::Client,
}

impl McpManager {
    pub fn new(root: &Path) -> Result<Self, McpError> {
        Ok(Self {
            config: McpConfig::load(root)?,
            clients: Mutex::new(HashMap::new()),
            http: reqwest::blocking::Client::builder()
                .timeout(REQUEST_TIMEOUT)
                .build()
                .map_err(|error| McpError::Protocol(error.to_string()))?,
        })
    }

    #[must_use]
    pub fn servers(&self) -> &[McpServerConfig] {
        &self.config.servers
    }

    pub fn catalog(&self) -> Result<Vec<McpToolInfo>, McpError> {
        let mut tools = Vec::new();
        for server in &self.config.servers {
            let client = self.client_for(server)?;
            let mut client = client
                .lock()
                .map_err(|_| McpError::Protocol("MCP client lock poisoned".to_owned()))?;
            let result = client.request("tools/list", json!({}))?;
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
        }
        Ok(tools)
    }

    pub fn call(
        &self,
        server_name: &str,
        tool: &str,
        arguments: Value,
    ) -> Result<String, McpError> {
        let server = self
            .config
            .servers
            .iter()
            .find(|server| server.name == server_name)
            .ok_or_else(|| McpError::UnknownServer(server_name.to_owned()))?;
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
                let client = self.client_for(server)?;
                let mut client = client
                    .lock()
                    .map_err(|_| McpError::Protocol("MCP client lock poisoned".to_owned()))?;
                client.request("tools/call", json!({"name": tool, "arguments": arguments}))?
            }
            McpTransport::Http => self.http_request(
                server,
                "tools/call",
                json!({"name": tool, "arguments": arguments}),
            )?,
        };
        Ok(render_mcp_result(&result))
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
        let response = self
            .http
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
}

impl McpClient {
    fn start_stdio(server: &McpServerConfig) -> Result<Self, McpError> {
        let command = server
            .command
            .as_deref()
            .ok_or_else(|| McpError::Protocol("stdio MCP server has no command".to_owned()))?;
        let mut child = Command::new(command)
            .args(&server.args)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|error| {
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
                .recv_timeout(REQUEST_TIMEOUT)
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

    #[test]
    fn stdio_servers_require_a_command() {
        let server = McpServerConfig::stdio("files", "mcp-files");
        assert_eq!(server.transport, McpTransport::Stdio);
        assert!(server.command.is_some());
        assert!(server.url.is_none());
    }

    #[test]
    fn stdio_client_lists_and_calls_tools() {
        let root = tempdir().expect("tempdir");
        let script = root.path().join("mock_mcp.py");
        fs::write(
            &script,
            r#"
import json, sys
for line in sys.stdin:
    message = json.loads(line)
    method = message.get('method')
    if method == 'initialize':
        print(json.dumps({'jsonrpc':'2.0','id':message['id'],'result':{'protocolVersion':'2024-11-05','capabilities':{}}}), flush=True)
    elif method == 'tools/list':
        print(json.dumps({'jsonrpc':'2.0','id':message['id'],'result':{'tools':[{'name':'echo','description':'Echo','inputSchema':{'type':'object'}}]}}), flush=True)
    elif method == 'tools/call':
        print(json.dumps({'jsonrpc':'2.0','id':message['id'],'result':{'content':[{'type':'text','text':'called ' + message['params']['name']}]}}), flush=True)
"#,
        )
        .expect("script");
        let mut server = McpServerConfig::stdio("mock", "python3");
        server.args = vec![script.to_string_lossy().into_owned()];
        server.allowed_tools = vec!["echo".to_owned()];
        let manager = McpManager {
            config: McpConfig {
                servers: vec![server],
            },
            clients: Mutex::new(HashMap::new()),
            http: reqwest::blocking::Client::new(),
        };
        let catalog = manager.catalog().expect("catalog");
        assert_eq!(catalog.len(), 1);
        assert_eq!(
            manager
                .call("mock", "echo", json!({"value": "hi"}))
                .expect("call"),
            "called echo"
        );
        assert!(manager.call("mock", "other", json!({})).is_err());
    }
}
