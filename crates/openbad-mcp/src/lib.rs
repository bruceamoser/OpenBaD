//! MCP server configuration and lifecycle primitives.

use serde::{Deserialize, Serialize};

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
    pub command: Option<String>,
    pub url: Option<String>,
    pub allowed_tools: Vec<String>,
}

impl McpServerConfig {
    #[must_use]
    pub fn stdio(name: impl Into<String>, command: impl Into<String>) -> Self {
        Self {
            name: name.into(),
            transport: McpTransport::Stdio,
            command: Some(command.into()),
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
            url: Some(url.into()),
            allowed_tools: Vec::new(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stdio_servers_require_a_command() {
        let server = McpServerConfig::stdio("files", "mcp-files");
        assert_eq!(server.transport, McpTransport::Stdio);
        assert!(server.command.is_some());
        assert!(server.url.is_none());
    }
}
