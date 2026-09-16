#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde_json::Value;
use std::env;
use std::io::{BufReader, Read, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::Mutex;
use tauri::{Manager, State};

const MAX_FRAME_BYTES: usize = 1024 * 1024;
const READY_SCHEMA: &str = "aegis-desktop-ready-v1";
const RESPONSE_SCHEMA: &str = "aegis-desktop-response-v1";
const PROTOCOL_VERSION: u64 = 1;

#[cfg(target_os = "windows")]
const BUNDLED_SERVICE_NAME: &str = "aegis-desktop-service.exe";
#[cfg(not(target_os = "windows"))]
const BUNDLED_SERVICE_NAME: &str = "aegis-desktop-service";

struct SidecarClient {
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
}

impl SidecarClient {
    fn start() -> Result<Self, String> {
        let program = env::var_os("AEGIS_DESKTOP_SERVICE")
            .map(PathBuf::from)
            .or_else(|| {
                let bundled = env::current_exe()
                    .ok()?
                    .parent()?
                    .join("resources")
                    .join(BUNDLED_SERVICE_NAME);
                bundled.is_file().then_some(bundled)
            })
            .unwrap_or_else(|| PathBuf::from("aegis-desktop"));
        let mut command = Command::new(program);
        command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit());
        let mut child = command
            .spawn()
            .map_err(|error| format!("unable to start the bundled desktop service: {error}"))?;
        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| "desktop service stdin unavailable".to_string())?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| "desktop service stdout unavailable".to_string())?;
        let mut client = Self {
            child,
            stdin,
            stdout: BufReader::new(stdout),
        };
        let ready = client.read_json_line()?;
        if ready.get("schema").and_then(Value::as_str) != Some(READY_SCHEMA)
            || ready.get("protocol_version").and_then(Value::as_u64) != Some(PROTOCOL_VERSION)
        {
            return Err("desktop service handshake is invalid".to_string());
        }
        Ok(client)
    }

    fn read_json_line(&mut self) -> Result<Value, String> {
        let mut line = Vec::with_capacity(4096);
        loop {
            let mut byte = [0u8; 1];
            let bytes = self
                .stdout
                .read(&mut byte)
                .map_err(|error| format!("desktop service read failed: {error}"))?;
            if bytes == 0 {
                return Err("desktop service closed its output".to_string());
            }
            line.push(byte[0]);
            if line.len() > MAX_FRAME_BYTES + 1 {
                return Err("desktop service response exceeded the frame limit".to_string());
            }
            if byte[0] == b'\n' {
                break;
            }
        }
        while matches!(line.last(), Some(b'\r' | b'\n')) {
            line.pop();
        }
        serde_json::from_slice(&line)
            .map_err(|_| "desktop service response was invalid JSON".to_string())
    }

    fn request(&mut self, frame: &str) -> Result<String, String> {
        if frame.len() > MAX_FRAME_BYTES {
            return Err("desktop request exceeded the frame limit".to_string());
        }
        let request: Value = serde_json::from_str(frame)
            .map_err(|_| "desktop request was invalid JSON".to_string())?;
        if request.get("schema").and_then(Value::as_str) != Some("aegis-desktop-command-v1") {
            return Err("desktop request schema is unsupported".to_string());
        }
        if request.get("protocol_version").and_then(Value::as_u64) != Some(PROTOCOL_VERSION) {
            return Err("desktop request used an unsupported protocol version".to_string());
        }
        let request_id = request
            .get("request_id")
            .and_then(Value::as_str)
            .ok_or_else(|| "desktop request has no request id".to_string())?;
        if request_id.trim().is_empty() || request_id.len() > 128 {
            return Err("desktop request id is invalid".to_string());
        }
        self.stdin
            .write_all(frame.as_bytes())
            .map_err(|error| format!("desktop service write failed: {error}"))?;
        self.stdin
            .write_all(b"\n")
            .map_err(|error| format!("desktop service write failed: {error}"))?;
        self.stdin
            .flush()
            .map_err(|error| format!("desktop service flush failed: {error}"))?;
        let response = self.read_json_line()?;
        if response.get("schema").and_then(Value::as_str) != Some(RESPONSE_SCHEMA)
            || response.get("protocol_version").and_then(Value::as_u64) != Some(PROTOCOL_VERSION)
            || response.get("request_id").and_then(Value::as_str) != Some(request_id)
        {
            return Err("desktop service response did not match the request".to_string());
        }
        serde_json::to_string(&response)
            .map_err(|_| "desktop response could not be serialized".to_string())
    }
}

impl Drop for SidecarClient {
    fn drop(&mut self) {
        let shutdown = serde_json::json!({
            "schema": "aegis-desktop-command-v1",
            "protocol_version": PROTOCOL_VERSION,
            "request_id": "host-shutdown",
            "command": "service.shutdown",
            "payload": {}
        });
        let _ = self.stdin.write_all(shutdown.to_string().as_bytes());
        let _ = self.stdin.write_all(b"\n");
        let _ = self.stdin.flush();
        for _ in 0..10 {
            match self.child.try_wait() {
                Ok(Some(_)) => return,
                Ok(None) => std::thread::sleep(std::time::Duration::from_millis(10)),
                Err(_) => break,
            }
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

#[tauri::command]
fn desktop_request(
    state: State<'_, Mutex<SidecarClient>>,
    frame: String,
) -> Result<String, String> {
    state
        .lock()
        .map_err(|_| "desktop service state is poisoned".to_string())?
        .request(&frame)
}

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            let client = SidecarClient::start().map_err(std::io::Error::other)?;
            app.manage(Mutex::new(client));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![desktop_request])
        .run(tauri::generate_context!())
        .expect("error while running AEGIS desktop");
}

#[cfg(test)]
mod tests {
    use super::BUNDLED_SERVICE_NAME;

    #[test]
    fn bundled_service_name_matches_the_host_platform() {
        #[cfg(target_os = "windows")]
        assert_eq!(BUNDLED_SERVICE_NAME, "aegis-desktop-service.exe");
        #[cfg(not(target_os = "windows"))]
        assert_eq!(BUNDLED_SERVICE_NAME, "aegis-desktop-service");
    }
}
