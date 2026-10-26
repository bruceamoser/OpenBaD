use std::net::{TcpStream, ToSocketAddrs};
use std::process::Command;
use std::time::Duration;

use serde::Serialize;

/// A version banner should be immediate; anything slower is a hung binary.
const BINARY_PROBE_TIMEOUT: Duration = Duration::from_secs(5);

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ServiceStatus {
    pub id: String,
    pub name: String,
    pub description: String,
    pub kind: String,
    pub url: Option<String>,
    pub env_var: Option<String>,
    pub status: String,
    pub detail: String,
    pub install_hint: String,
}

struct Definition {
    id: &'static str,
    name: &'static str,
    description: &'static str,
    kind: &'static str,
    env_var: Option<&'static str>,
    default_url: Option<&'static str>,
    binary: Option<&'static str>,
    install_hint: &'static str,
}

const DEFINITIONS: &[Definition] = &[
    Definition {
        id: "searxng",
        name: "SearXNG",
        description: "Local metasearch used by the web_search tool.",
        kind: "http",
        env_var: Some("OPENBAD_SEARXNG_URL"),
        default_url: Some("http://127.0.0.1:8080"),
        binary: None,
        install_hint: "Run a SearXNG instance locally on port 8080, or set OPENBAD_SEARXNG_URL to its base URL.",
    },
    Definition {
        id: "jina-reader",
        name: "Jina Reader",
        description: "Readable markdown extraction used by web_fetch.",
        kind: "http",
        env_var: Some("OPENBAD_JINA_READER_URL"),
        default_url: Some("http://127.0.0.1:3000"),
        binary: None,
        install_hint: "Run a local Jina Reader (or r.jina.ai-compatible endpoint) on port 3000, or set OPENBAD_JINA_READER_URL.",
    },
    Definition {
        id: "jupyter",
        name: "Jupyter",
        description: "Notebook server used for interactive analysis.",
        kind: "http",
        env_var: Some("OPENBAD_JUPYTER_URL"),
        default_url: Some("http://127.0.0.1:8888"),
        binary: None,
        install_hint: "Install JupyterLab (`pip install jupyterlab`) and start it on port 8888, or set OPENBAD_JUPYTER_URL.",
    },
    Definition {
        id: "tesseract",
        name: "Tesseract OCR",
        description: "Optical character recognition for pasted images.",
        kind: "binary",
        env_var: None,
        default_url: None,
        binary: Some("tesseract"),
        install_hint: "Install Tesseract (for example `sudo apt install tesseract-ocr`).",
    },
    Definition {
        id: "pdftotext",
        name: "pdftotext",
        description: "Optional PDF text extraction fallback.",
        kind: "binary",
        env_var: None,
        default_url: None,
        binary: Some("pdftotext"),
        install_hint: "Install poppler-utils (for example `sudo apt install poppler-utils`).",
    },
    Definition {
        id: "git",
        name: "Git",
        description: "Branch, diff, commit, pull, and push support.",
        kind: "binary",
        env_var: None,
        default_url: None,
        binary: Some("git"),
        install_hint: "Install Git (for example `sudo apt install git`).",
    },
];

fn configured_url(definition: &Definition) -> Option<String> {
    definition
        .env_var
        .and_then(|name| std::env::var(name).ok())
        .map(|value| value.trim().trim_end_matches('/').to_owned())
        .filter(|value| !value.is_empty())
        .or_else(|| definition.default_url.map(str::to_owned))
}

fn tcp_probe(url: &str) -> (bool, String) {
    let Ok(parsed) = reqwest::Url::parse(url) else {
        return (false, "invalid URL".to_owned());
    };
    let Some(host) = parsed.host_str() else {
        return (false, "URL has no host".to_owned());
    };
    let port = parsed.port_or_known_default().unwrap_or(80);
    let Ok(mut addresses) = (host, port).to_socket_addrs() else {
        return (false, format!("could not resolve {host}:{port}"));
    };
    let Some(address) = addresses.next() else {
        return (false, format!("could not resolve {host}:{port}"));
    };
    match TcpStream::connect_timeout(&address, Duration::from_millis(800)) {
        Ok(_) => (true, format!("TCP {host}:{port} is reachable")),
        Err(error) => (false, format!("TCP {host}:{port}: {error}")),
    }
}

fn binary_probe(binary: &str) -> (bool, String) {
    let mut command = Command::new(binary);
    command.arg("--version");
    let output = match crate::process::run_bounded(
        &mut command,
        BINARY_PROBE_TIMEOUT,
        &format!("{binary} --version"),
    ) {
        Ok(output) => output,
        Err(error) => return (false, error),
    };
    let text = if output.stdout.is_empty() {
        String::from_utf8_lossy(&output.stderr).trim().to_owned()
    } else {
        String::from_utf8_lossy(&output.stdout).trim().to_owned()
    };
    if text.is_empty() {
        (true, "installed".to_owned())
    } else {
        (true, text.lines().next().unwrap_or("installed").to_owned())
    }
}

fn collect_status() -> Vec<ServiceStatus> {
    DEFINITIONS
        .iter()
        .map(|definition| {
            let (running, detail) = if definition.kind == "binary" {
                binary_probe(definition.binary.unwrap_or_default())
            } else {
                configured_url(definition)
                    .map(|url| tcp_probe(&url))
                    .unwrap_or_else(|| (false, "no URL configured".to_owned()))
            };
            ServiceStatus {
                id: definition.id.to_owned(),
                name: definition.name.to_owned(),
                description: definition.description.to_owned(),
                kind: definition.kind.to_owned(),
                url: configured_url(definition),
                env_var: definition.env_var.map(str::to_owned),
                status: if running { "running" } else { "missing" }.to_owned(),
                detail,
                install_hint: definition.install_hint.to_owned(),
            }
        })
        .collect()
}

#[tauri::command]
pub async fn services_status() -> Vec<ServiceStatus> {
    tauri::async_runtime::spawn_blocking(collect_status)
        .await
        .unwrap_or_default()
}

#[tauri::command]
pub fn open_service_url(url: String) -> Result<(), String> {
    if !(url.starts_with("http://") || url.starts_with("https://")) {
        return Err("only http(s) URLs can be opened".to_owned());
    }
    let mut command = if cfg!(target_os = "macos") {
        let mut command = Command::new("open");
        command.arg(&url);
        command
    } else if cfg!(target_os = "windows") {
        let mut command = Command::new("cmd");
        command.args(["/C", "start", ""]).arg(&url);
        command
    } else {
        let mut command = Command::new("xdg-open");
        command.arg(&url);
        command
    };
    command
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn()
        .map(|_| ())
        .map_err(|error| format!("could not open URL: {error}"))
}
