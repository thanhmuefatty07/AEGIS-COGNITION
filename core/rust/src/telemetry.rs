use std::io::{Read, Write};
use std::net::TcpListener;

const REQUEST_BUFFER_BYTES: usize = 1024;

pub fn physical_metrics_http_response() -> String {
    prometheus_http_response(&crate::physical::physical_metrics_prometheus())
}

pub fn prometheus_http_response(body: &str) -> String {
    format!(
        concat!(
            "HTTP/1.1 200 OK\r\n",
            "Content-Type: text/plain; version=0.0.4; charset=utf-8\r\n",
            "Content-Length: {}\r\n",
            "Connection: close\r\n",
            "\r\n",
            "{}"
        ),
        body.len(),
        body
    )
}

pub fn serve_physical_metrics_once(listener: &TcpListener) -> std::io::Result<()> {
    let (mut stream, _) = listener.accept()?;
    let mut request = [0u8; REQUEST_BUFFER_BYTES];
    let bytes_read = stream.read(&mut request)?;
    let request_text = std::str::from_utf8(&request[..bytes_read]).unwrap_or("");

    let response =
        if request_text.starts_with("GET /metrics ") || request_text.starts_with("GET /metrics?") {
            physical_metrics_http_response()
        } else {
            not_found_response()
        };

    stream.write_all(response.as_bytes())?;
    stream.flush()
}

fn not_found_response() -> String {
    let body = "not found\n";
    format!(
        concat!(
            "HTTP/1.1 404 Not Found\r\n",
            "Content-Type: text/plain; charset=utf-8\r\n",
            "Content-Length: {}\r\n",
            "Connection: close\r\n",
            "\r\n",
            "{}"
        ),
        body.len(),
        body
    )
}
