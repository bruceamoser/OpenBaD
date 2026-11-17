//! Local HTTP services that the web tools depend on.
//!
//! Both services are provisioned by the repository-root `docker-compose.yml`
//! and bind to loopback on their default ports. Each endpoint is overridable by
//! environment variable, and every failure names the service, the endpoint, and
//! the fix instead of surfacing a raw transport error.

use std::net::{TcpStream, ToSocketAddrs};
use std::time::Duration;

/// How long a TCP probe waits before calling an endpoint unreachable.
///
/// A local service that is not running refuses in microseconds; the timeout
/// only bounds a host that silently drops the SYN.
const PROBE_TIMEOUT: Duration = Duration::from_millis(800);

/// One local HTTP service behind a tool.
#[derive(Debug)]
pub struct WebService {
    /// Display name used in errors.
    pub name: &'static str,
    /// Environment variable that overrides the base URL.
    pub env_var: &'static str,
    /// Loopback URL used when the environment variable is unset.
    pub default_url: &'static str,
    /// Service name in `docker-compose.yml`.
    pub compose_service: &'static str,
}

/// Local metasearch behind `web_search`.
pub const SEARXNG: WebService = WebService {
    name: "SearXNG",
    env_var: "OPENBAD_SEARXNG_URL",
    default_url: "http://127.0.0.1:8080",
    compose_service: "searxng",
};

/// Readable-markdown extraction behind `web_fetch`.
pub const JINA_READER: WebService = WebService {
    name: "Jina Reader",
    env_var: "OPENBAD_JINA_READER_URL",
    default_url: "http://127.0.0.1:3000",
    compose_service: "jina-reader",
};

impl WebService {
    /// The configured base URL without a trailing slash.
    ///
    /// An unset variable falls back to the loopback default. `None` means the
    /// variable is set to an empty value or one of the documented off switches
    /// (`off`, `none`, `disabled`, `direct`), which matters because the two
    /// web tools treat a switched-off service differently.
    pub fn base_url(&self) -> Option<String> {
        let Ok(value) = std::env::var(self.env_var) else {
            return Some(self.default_url.to_owned());
        };
        let value = value.trim().trim_end_matches('/');
        if value.is_empty()
            || matches!(
                value.to_ascii_lowercase().as_str(),
                "off" | "none" | "disabled" | "direct"
            )
        {
            None
        } else {
            Some(value.to_owned())
        }
    }

    /// An actionable error for a service that is not answering.
    ///
    /// A bare transport error names neither the service nor the remedy, so the
    /// message carries the endpoint, the environment variable, and the compose
    /// command that provisions it.
    pub fn unreachable(&self, url: &str, cause: &str) -> String {
        format!(
            "{} is not reachable at {url} ({cause}). Set {} to a different base URL, or start the local web services with `docker compose up -d {}` from the repository root (see docs/web-services.md).",
            self.name, self.env_var, self.compose_service
        )
    }
}

/// The Jina Reader base URL, or `None` when the reader is switched off.
pub fn jina_reader_base() -> Option<String> {
    JINA_READER.base_url()
}

/// Build a reader request URL from a base URL and the target URL.
pub fn jina_reader_url(base: &str, target: &str) -> String {
    if base.contains("{url}") {
        base.replace("{url}", target)
    } else {
        format!("{base}/{target}")
    }
}

/// Probe a URL's TCP endpoint within a short budget.
///
/// The detail is a human-readable reason, so a stopped local service fails in
/// milliseconds with a known-bad reason instead of waiting for the HTTP
/// timeout. A listening process that never answers still reads as reachable;
/// only the HTTP client can see that failure.
pub fn probe_tcp(url: &str) -> (bool, String) {
    let Ok(parsed) = reqwest::Url::parse(url) else {
        return (false, "invalid URL".to_owned());
    };
    let Some(host) = parsed.host_str() else {
        return (false, "URL has no host".to_owned());
    };
    let host = host.trim_start_matches('[').trim_end_matches(']');
    let port = parsed.port_or_known_default().unwrap_or(80);
    let Ok(addresses) = (host, port).to_socket_addrs() else {
        return (false, format!("could not resolve {host}:{port}"));
    };
    // A hostname such as `localhost` can resolve to both loopback families,
    // and the service may listen on only one of them, so try them all.
    let mut last_error = None;
    for address in addresses {
        match TcpStream::connect_timeout(&address, PROBE_TIMEOUT) {
            Ok(_) => return (true, format!("TCP {host}:{port} is reachable")),
            Err(error) => last_error = Some(error),
        }
    }
    match last_error {
        Some(error) => (false, format!("TCP {host}:{port}: {error}")),
        None => (false, format!("could not resolve {host}:{port}")),
    }
}

/// Whether a URL points at this machine.
///
/// Only loopback endpoints are probed before an HTTP request: a remote endpoint
/// may be reached through an egress proxy that a raw TCP connect cannot follow,
/// and a false "unreachable" would be worse than the wait it avoids.
pub fn is_local(url: &reqwest::Url) -> bool {
    let Some(host) = url.host_str() else {
        return false;
    };
    let host = host.trim_start_matches('[').trim_end_matches(']');
    host.eq_ignore_ascii_case("localhost")
        || host
            .parse::<std::net::IpAddr>()
            .is_ok_and(|address| address.is_loopback())
}

/// Describe a transport failure without repeating reqwest's request dump.
pub fn transport_cause(error: &reqwest::Error, timeout: Duration) -> String {
    if error.is_timeout() {
        return format!(
            "timed out after {} s; the endpoint did not answer before the request deadline",
            timeout.as_secs()
        );
    }
    let root = root_cause(error);
    if error.is_connect() && !root.is_empty() {
        return format!("connection failed: {root}");
    }
    if root.is_empty() {
        "request failed".to_owned()
    } else {
        root
    }
}

fn root_cause(error: &reqwest::Error) -> String {
    let mut source: &dyn std::error::Error = error;
    while let Some(next) = source.source() {
        source = next;
    }
    source.to_string()
}

#[cfg(test)]
mod tests {
    use std::net::TcpListener;

    use super::*;

    #[test]
    fn a_listening_loopback_port_is_reachable() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("listener");
        let address = listener.local_addr().expect("addr");
        let (reachable, detail) = probe_tcp(&format!("http://{address}"));
        assert!(reachable, "{detail}");
    }

    #[test]
    fn a_closed_loopback_port_is_unreachable_and_names_the_endpoint() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("listener");
        let address = listener.local_addr().expect("addr");
        drop(listener);
        let (reachable, detail) = probe_tcp(&format!("http://{address}"));
        assert!(!reachable, "{detail}");
        assert!(detail.contains(&address.to_string()), "{detail}");
    }

    #[test]
    fn the_unreachable_message_names_the_service_url_env_var_and_fix() {
        let message = SEARXNG.unreachable("http://127.0.0.1:8080", "connection failed");
        assert!(message.contains("SearXNG"), "{message}");
        assert!(message.contains("http://127.0.0.1:8080"), "{message}");
        assert!(message.contains("OPENBAD_SEARXNG_URL"), "{message}");
        assert!(
            message.contains("docker compose up -d searxng"),
            "{message}"
        );
        assert!(!message.contains("error sending request"), "{message}");
    }

    #[test]
    fn only_loopback_hosts_are_local() {
        for url in [
            "http://127.0.0.1:8080",
            "http://localhost:8080",
            "http://[::1]:8080",
        ] {
            assert!(is_local(&reqwest::Url::parse(url).expect("url")), "{url}");
        }
        for url in ["http://192.168.1.5:8080", "https://example.com"] {
            assert!(!is_local(&reqwest::Url::parse(url).expect("url")), "{url}");
        }
    }
}
