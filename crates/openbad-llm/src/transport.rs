use std::pin::Pin;

use async_trait::async_trait;
use bytes::Bytes;
use futures_core::Stream;
use reqwest::header::{HeaderMap, HeaderName, HeaderValue};

use crate::provider::ProviderError;

pub type ByteStream = Pin<Box<dyn Stream<Item = Result<Bytes, ProviderError>> + Send>>;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HttpRequest {
    pub url: String,
    pub headers: Vec<(String, String)>,
    pub body: Vec<u8>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HttpResponse {
    pub status: u16,
    pub body: Vec<u8>,
}

#[async_trait]
pub trait HttpTransport: Send + Sync {
    async fn post(&self, request: HttpRequest) -> Result<HttpResponse, ProviderError>;
    async fn post_stream(&self, request: HttpRequest) -> Result<ByteStream, ProviderError>;
}

#[derive(Debug, Clone, Default)]
pub struct ReqwestTransport {
    client: reqwest::Client,
}

impl ReqwestTransport {
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    fn build_request(
        &self,
        request: HttpRequest,
    ) -> Result<reqwest::RequestBuilder, ProviderError> {
        let mut headers = HeaderMap::new();
        for (name, value) in request.headers {
            let name = HeaderName::from_bytes(name.as_bytes())
                .map_err(|error| ProviderError::Request(error.to_string()))?;
            let value = HeaderValue::from_str(&value)
                .map_err(|error| ProviderError::Request(error.to_string()))?;
            headers.insert(name, value);
        }
        Ok(self
            .client
            .post(&request.url)
            .headers(headers)
            .body(request.body))
    }
}

#[async_trait]
impl HttpTransport for ReqwestTransport {
    async fn post(&self, request: HttpRequest) -> Result<HttpResponse, ProviderError> {
        let response = self
            .build_request(request)?
            .send()
            .await
            .map_err(|error| ProviderError::Request(error.to_string()))?;
        let status = response.status().as_u16();
        let body = response
            .bytes()
            .await
            .map_err(|error| ProviderError::Request(error.to_string()))?
            .to_vec();
        Ok(HttpResponse { status, body })
    }

    async fn post_stream(&self, request: HttpRequest) -> Result<ByteStream, ProviderError> {
        let response = self
            .build_request(request)?
            .send()
            .await
            .map_err(|error| ProviderError::Request(error.to_string()))?;
        let status = response.status();
        if !status.is_success() {
            let body = response
                .text()
                .await
                .map_err(|error| ProviderError::Request(error.to_string()))?;
            return Err(ProviderError::Request(format!("HTTP {status}: {body}")));
        }
        let stream = futures_util::StreamExt::map(response.bytes_stream(), |chunk| {
            chunk.map_err(|error| ProviderError::Request(error.to_string()))
        });
        Ok(Box::pin(stream))
    }
}

#[cfg(test)]
mod tests {
    use std::io::{Read, Write};
    use std::net::TcpListener;

    use super::*;

    #[tokio::test]
    async fn reqwest_transport_posts_the_frozen_body() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("listener");
        let address = listener.local_addr().expect("address");
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("accept");
            let mut buffer = [0_u8; 4096];
            let read = stream.read(&mut buffer).expect("read");
            let request = String::from_utf8_lossy(&buffer[..read]).into_owned();
            stream
                .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nhi")
                .expect("write");
            request
        });

        let transport = ReqwestTransport::new();
        let response = transport
            .post(HttpRequest {
                url: format!("http://{address}/chat/completions"),
                headers: vec![("Content-Type".to_owned(), "application/json".to_owned())],
                body: br#"{"hello":"world"}"#.to_vec(),
            })
            .await
            .expect("response");

        assert_eq!(response.status, 200);
        assert_eq!(response.body, b"hi");
        let request = server.join().expect("server");
        assert!(request.starts_with("POST /chat/completions"));
        assert!(request.contains(r#"{"hello":"world"}"#));
    }
}
