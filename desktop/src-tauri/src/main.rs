#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde_json::Value;
use std::collections::HashSet;
use std::env;
use std::fs;
use std::io::{BufReader, Read, Write};
use std::path::{Component, Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tauri::{Manager, State};

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;

const MAX_FRAME_BYTES: usize = 16 * 1024 * 1024;
// MCP shutdown may use five seconds to drain and five more to join its loop.
const SERVICE_SHUTDOWN_GRACE: Duration = Duration::from_secs(12);
const SERVICE_SHUTDOWN_POLL_INTERVAL: Duration = Duration::from_millis(25);
const READY_SCHEMA: &str = "aegis-desktop-ready-v1";
const RESPONSE_SCHEMA: &str = "aegis-desktop-response-v1";
const PROTOCOL_VERSION: u64 = 1;
static NEXT_SKILL_IMPORT_REQUEST_ID: AtomicU64 = AtomicU64::new(1);
static NEXT_EXTENSION_PACK_IMPORT_REQUEST_ID: AtomicU64 = AtomicU64::new(1);

#[derive(Default)]
struct WorkspaceAuthority {
    approved_roots: HashSet<PathBuf>,
}

impl WorkspaceAuthority {
    fn approve_picker_result(&mut self, path: PathBuf) -> Result<String, String> {
        let canonical = canonical_directory(&path)?;
        self.approved_roots.insert(canonical.clone());
        Ok(canonical.to_string_lossy().into_owned())
    }

    fn approve_matching_picker_result(
        &mut self,
        selected: PathBuf,
        requested: &Path,
    ) -> Result<String, String> {
        let selected = canonical_directory(&selected)?;
        let requested = canonical_directory(requested)?;
        if selected != requested {
            return Err("the selected folder did not match the requested project".to_string());
        }
        self.approved_roots.insert(requested.clone());
        Ok(requested.to_string_lossy().into_owned())
    }

    fn authorize_existing(&self, raw_path: &str) -> Result<(), String> {
        let canonical = canonical_directory(Path::new(raw_path))?;
        if self.is_within_approved_root(&canonical) {
            Ok(())
        } else {
            Err("choose the folder with Open project before using local access".to_string())
        }
    }

    fn authorize_destination(&self, raw_path: &str) -> Result<(), String> {
        let mut candidate = PathBuf::from(raw_path);
        if candidate
            .components()
            .any(|component| matches!(component, Component::ParentDir))
        {
            return Err(
                "clone destination must not contain parent-directory components".to_string(),
            );
        }
        while !candidate.exists() {
            if !candidate.pop() {
                return Err("clone destination is not inside an approved folder".to_string());
            }
        }
        let canonical = fs::canonicalize(&candidate)
            .map_err(|error| format!("clone destination could not be verified: {error}"))?;
        if self.is_within_approved_root(&canonical) {
            Ok(())
        } else {
            Err("choose the clone parent folder with Open project before cloning".to_string())
        }
    }

    fn is_within_approved_root(&self, candidate: &Path) -> bool {
        self.approved_roots
            .iter()
            .any(|root| candidate == root || candidate.starts_with(root))
    }
}

fn canonical_directory(path: &Path) -> Result<PathBuf, String> {
    let canonical = fs::canonicalize(path)
        .map_err(|error| format!("workspace folder could not be verified: {error}"))?;
    if !canonical.is_dir() {
        return Err("workspace access requires a directory".to_string());
    }
    Ok(canonical)
}

#[cfg(target_os = "windows")]
const BUNDLED_SERVICE_NAME: &str = "aegis-desktop-service.exe";
#[cfg(not(target_os = "windows"))]
const BUNDLED_SERVICE_NAME: &str = "aegis-desktop-service";

struct SidecarClient {
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
}

fn wait_for_service_exit(child: &mut Child, grace_period: Duration) -> bool {
    let deadline = Instant::now() + grace_period;
    loop {
        match child.try_wait() {
            Ok(Some(_)) => return true,
            Ok(None) => {
                let remaining = deadline.saturating_duration_since(Instant::now());
                if remaining.is_zero() {
                    return false;
                }
                std::thread::sleep(remaining.min(SERVICE_SHUTDOWN_POLL_INTERVAL));
            }
            Err(_) => return false,
        }
    }
}

impl SidecarClient {
    fn start() -> Result<Self, String> {
        let mut command = if let Some(program) = env::var_os("AEGIS_DESKTOP_SERVICE") {
            Command::new(PathBuf::from(program))
        } else if let Some(bundled) = bundled_service_path() {
            Command::new(bundled)
        } else {
            development_service_command()?
        };
        command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit());
        #[cfg(target_os = "windows")]
        command.creation_flags(0x08000000);
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

fn bundled_service_path() -> Option<PathBuf> {
    let bundled = env::current_exe()
        .ok()?
        .parent()?
        .join("resources")
        .join(BUNDLED_SERVICE_NAME);
    bundled.is_file().then_some(bundled)
}

#[cfg(debug_assertions)]
fn development_service_command() -> Result<Command, String> {
    let repository_root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..");
    let local_python = if cfg!(target_os = "windows") {
        repository_root
            .join(".venv")
            .join("Scripts")
            .join("python.exe")
    } else {
        repository_root.join(".venv").join("bin").join("python")
    };
    let python = if local_python.is_file() {
        local_python
    } else if cfg!(target_os = "windows") {
        PathBuf::from("python")
    } else {
        PathBuf::from("python3")
    };
    let mut command = Command::new(python);
    command
        .args(["-m", "desktop.packaging.sidecar_entry"])
        .current_dir(repository_root);
    Ok(command)
}

#[cfg(not(debug_assertions))]
fn development_service_command() -> Result<Command, String> {
    Err(format!(
        "desktop service resource {BUNDLED_SERVICE_NAME} is missing; set AEGIS_DESKTOP_SERVICE to a valid sidecar"
    ))
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
        if wait_for_service_exit(&mut self.child, SERVICE_SHUTDOWN_GRACE) {
            return;
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

#[tauri::command]
fn desktop_request(
    state: State<'_, Mutex<SidecarClient>>,
    authority: State<'_, Mutex<WorkspaceAuthority>>,
    frame: String,
) -> Result<String, String> {
    let authority_guard = authority
        .lock()
        .map_err(|_| "workspace authority is poisoned".to_string())?;
    authorize_workspace_request(&frame, &authority_guard)?;
    state
        .lock()
        .map_err(|_| "desktop service state is poisoned".to_string())?
        .request(&frame)
}

fn authorize_workspace_request(frame: &str, authority: &WorkspaceAuthority) -> Result<(), String> {
    let request: Value = serde_json::from_str(frame)
        .map_err(|_| "desktop request was not valid JSON".to_string())?;
    let command = request
        .get("command")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let payload = request.get("payload").and_then(Value::as_object);
    if matches!(
        command,
        "extensions.import_skill" | "extensions.import_pack"
    ) {
        return Err("capability imports must use the native folder picker".to_string());
    }
    match command {
        "workspace.open" | "workspace.switch" => {
            if let Some(path) = payload
                .and_then(|value| value.get("workspace_path"))
                .and_then(Value::as_str)
            {
                authority.authorize_existing(path)?;
            }
        }
        "workspace.clone" => {
            if let Some(path) = payload
                .and_then(|value| value.get("destination_root"))
                .and_then(Value::as_str)
            {
                authority.authorize_destination(path)?;
            }
        }
        _ => {}
    }
    Ok(())
}

#[tauri::command]
fn pick_workspace(
    authority: State<'_, Mutex<WorkspaceAuthority>>,
) -> Result<Option<String>, String> {
    let Some(path) = rfd::FileDialog::new()
        .set_title("Open project")
        .pick_folder()
    else {
        return Ok(None);
    };
    let mut authority = authority
        .lock()
        .map_err(|_| "workspace authority is poisoned".to_string())?;
    authority.approve_picker_result(path).map(Some)
}

#[tauri::command]
fn pick_and_import_skill(state: State<'_, Mutex<SidecarClient>>) -> Result<Option<String>, String> {
    let Some(path) = rfd::FileDialog::new()
        .set_title("Import a skill folder containing SKILL.md")
        .pick_folder()
    else {
        return Ok(None);
    };
    let source_path = path
        .to_str()
        .ok_or_else(|| "the selected folder path cannot be represented safely".to_string())?;
    let request_id = format!(
        "native-skill-import-{}",
        NEXT_SKILL_IMPORT_REQUEST_ID.fetch_add(1, Ordering::Relaxed)
    );
    let frame = serde_json::json!({
        "schema": "aegis-desktop-command-v1",
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request_id,
        "command": "extensions.import_skill",
        "payload": {"source_path": source_path}
    });
    let response = state
        .lock()
        .map_err(|_| "desktop service state is poisoned".to_string())?
        .request(&frame.to_string())?;
    let response: Value = serde_json::from_str(&response)
        .map_err(|_| "desktop service returned an invalid skill import result".to_string())?;
    if response.get("status").and_then(Value::as_str) == Some("ok") {
        return response
            .pointer("/result/skill/name")
            .and_then(Value::as_str)
            .map(str::to_string)
            .map(Some)
            .ok_or_else(|| "desktop service returned an invalid imported skill".to_string());
    }
    let code = response
        .pointer("/error/code")
        .and_then(Value::as_str)
        .unwrap_or("SKILL_IMPORT_FAILED");
    let message = response
        .pointer("/error/message")
        .and_then(Value::as_str)
        .unwrap_or("the selected skill could not be imported safely");
    Err(format!("{code}: {message}"))
}

#[tauri::command]
fn pick_and_import_extension_pack(
    state: State<'_, Mutex<SidecarClient>>,
) -> Result<Option<Value>, String> {
    let Some(path) = rfd::FileDialog::new()
        .set_title("Import a capability pack containing extension.toml")
        .pick_folder()
    else {
        return Ok(None);
    };
    import_extension_pack_path(&state, &path).map(Some)
}

#[tauri::command]
fn pick_and_import_extension_archive(
    state: State<'_, Mutex<SidecarClient>>,
) -> Result<Option<Value>, String> {
    let Some(path) = rfd::FileDialog::new()
        .add_filter("ZIP package", &["zip"])
        .set_title("Import a capability pack from a ZIP file")
        .pick_file()
    else {
        return Ok(None);
    };
    import_extension_pack_path(&state, &path).map(Some)
}

fn import_extension_pack_path(
    state: &State<'_, Mutex<SidecarClient>>,
    path: &Path,
) -> Result<Value, String> {
    let source_path = path
        .to_str()
        .ok_or_else(|| "the selected package path cannot be represented safely".to_string())?;
    let request_id = format!(
        "native-extension-pack-import-{}",
        NEXT_EXTENSION_PACK_IMPORT_REQUEST_ID.fetch_add(1, Ordering::Relaxed)
    );
    let frame = serde_json::json!({
        "schema": "aegis-desktop-command-v1",
        "protocol_version": PROTOCOL_VERSION,
        "request_id": request_id,
        "command": "extensions.import_pack",
        "payload": {"source_path": source_path}
    });
    let response = state
        .lock()
        .map_err(|_| "desktop service state is poisoned".to_string())?
        .request(&frame.to_string())?;
    let response: Value = serde_json::from_str(&response).map_err(|_| {
        "desktop service returned an invalid capability pack import result".to_string()
    })?;
    if response.get("status").and_then(Value::as_str) == Some("ok") {
        return response.get("result").cloned().ok_or_else(|| {
            "desktop service returned an invalid imported capability pack".to_string()
        });
    }
    let code = response
        .pointer("/error/code")
        .and_then(Value::as_str)
        .unwrap_or("EXTENSION_PACK_FAILED");
    let message = response
        .pointer("/error/message")
        .and_then(Value::as_str)
        .unwrap_or("the selected capability pack could not be imported safely");
    Err(format!("{code}: {message}"))
}

#[tauri::command]
fn approve_workspace_path(
    authority: State<'_, Mutex<WorkspaceAuthority>>,
    path: String,
) -> Result<String, String> {
    let requested = canonical_directory(Path::new(&path))?;
    {
        let authority = authority
            .lock()
            .map_err(|_| "workspace authority is poisoned".to_string())?;
        if authority.is_within_approved_root(&requested) {
            return Ok(requested.to_string_lossy().into_owned());
        }
    }

    let mut dialog = rfd::FileDialog::new().set_title("Confirm project access");
    if let Some(parent) = requested.parent() {
        dialog = dialog.set_directory(parent);
    }
    let Some(selected) = dialog.pick_folder() else {
        return Err("project access was not confirmed".to_string());
    };
    let mut authority = authority
        .lock()
        .map_err(|_| "workspace authority is poisoned".to_string())?;
    authority.approve_matching_picker_result(selected, &requested)
}

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            let client = SidecarClient::start().map_err(std::io::Error::other)?;
            app.manage(Mutex::new(client));
            app.manage(Mutex::new(WorkspaceAuthority::default()));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            desktop_request,
            pick_workspace,
            approve_workspace_path,
            pick_and_import_skill,
            pick_and_import_extension_pack,
            pick_and_import_extension_archive
        ])
        .run(tauri::generate_context!())
        .expect("error while running AEGIS desktop");
}

#[cfg(test)]
mod tests {
    use super::{
        authorize_workspace_request, wait_for_service_exit, WorkspaceAuthority,
        BUNDLED_SERVICE_NAME, SERVICE_SHUTDOWN_GRACE,
    };
    use std::collections::HashSet;
    use std::fs;
    use std::path::PathBuf;
    use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

    struct TempDirectory(PathBuf);

    impl Drop for TempDirectory {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn bundled_service_name_matches_the_host_platform() {
        #[cfg(target_os = "windows")]
        assert_eq!(BUNDLED_SERVICE_NAME, "aegis-desktop-service.exe");
        #[cfg(not(target_os = "windows"))]
        assert_eq!(BUNDLED_SERVICE_NAME, "aegis-desktop-service");
    }

    #[test]
    fn sidecar_shutdown_child_exits_after_delay() {
        if std::env::var_os("AEGIS_TEST_SIDECAR_CHILD").is_some() {
            std::thread::sleep(Duration::from_millis(200));
        }
    }

    #[test]
    fn service_shutdown_waits_for_child_and_covers_runtime_cleanup_budget() {
        assert!(SERVICE_SHUTDOWN_GRACE > Duration::from_secs(10));
        let mut child = std::process::Command::new(
            std::env::current_exe().expect("test executable path must be available"),
        )
        .args([
            "--exact",
            "tests::sidecar_shutdown_child_exits_after_delay",
            "--nocapture",
        ])
        .env("AEGIS_TEST_SIDECAR_CHILD", "1")
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn()
        .expect("test child must start");
        let started = Instant::now();

        assert!(wait_for_service_exit(&mut child, Duration::from_secs(2)));
        assert!(started.elapsed() >= Duration::from_millis(150));
    }

    #[test]
    fn workspace_authority_allows_descendants_but_not_siblings() {
        let approved = PathBuf::from("approved-project");
        let authority = WorkspaceAuthority {
            approved_roots: HashSet::from([approved.clone()]),
        };

        assert!(authority.is_within_approved_root(&approved.join("src")));
        assert!(
            !authority.is_within_approved_root(PathBuf::from("approved-project-other").as_path())
        );
    }

    #[test]
    fn workspace_request_without_user_path_keeps_app_data_flow_available() {
        let authority = WorkspaceAuthority::default();
        let frame = serde_json::json!({
            "schema": "aegis-desktop-command-v1",
            "protocol_version": 1,
            "request_id": "test-default-workspace",
            "command": "workspace.open",
            "payload": {}
        })
        .to_string();

        assert!(authorize_workspace_request(&frame, &authority).is_ok());
    }

    #[test]
    fn renderer_cannot_submit_capability_import_paths_without_the_native_picker() {
        let authority = WorkspaceAuthority::default();
        for command in ["extensions.import_skill", "extensions.import_pack"] {
            let frame = serde_json::json!({
                "schema": "aegis-desktop-command-v1",
                "protocol_version": 1,
                "request_id": format!("forged-{command}"),
                "command": command,
                "payload": {"source_path": "C:/private"}
            })
            .to_string();

            assert!(authorize_workspace_request(&frame, &authority).is_err());
        }
    }

    #[test]
    fn workspace_authority_enforces_the_selected_root_for_real_paths() {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system clock must be after unix epoch")
            .as_nanos();
        let base = std::env::temp_dir().join(format!("aegis-workspace-authority-{unique}"));
        let approved = base.join("approved");
        let child = approved.join("child");
        let sibling = base.join("sibling");
        let _cleanup = TempDirectory(base.clone());
        fs::create_dir_all(&child).expect("test approved tree must be creatable");
        fs::create_dir_all(&sibling).expect("test sibling tree must be creatable");

        let mut authority = WorkspaceAuthority::default();
        assert!(authority.approve_picker_result(approved.clone()).is_ok());
        assert!(authority
            .authorize_existing(&child.to_string_lossy())
            .is_ok());
        assert!(authority
            .authorize_destination(&approved.join("new-project").to_string_lossy())
            .is_ok());
        let traversal = approved
            .join("missing")
            .join("..")
            .join("..")
            .join("sibling")
            .join("new-project");
        #[cfg(windows)]
        let traversal = PathBuf::from(format!(r"\\?\{}", traversal.to_string_lossy()));
        assert!(authority
            .authorize_destination(&traversal.to_string_lossy())
            .is_err());
        assert!(authority
            .authorize_existing(&sibling.to_string_lossy())
            .is_err());
        assert!(authority
            .authorize_destination(&sibling.join("new-project").to_string_lossy())
            .is_err());
    }

    #[test]
    fn workspace_reapproval_grants_only_the_exact_folder_selected_natively() {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system clock must be after unix epoch")
            .as_nanos();
        let base = std::env::temp_dir().join(format!("aegis-workspace-reapproval-{unique}"));
        let selected = base.join("selected");
        let requested = base.join("requested");
        let _cleanup = TempDirectory(base.clone());
        fs::create_dir_all(&selected).expect("selected folder must be creatable");
        fs::create_dir_all(&requested).expect("requested folder must be creatable");

        let mut authority = WorkspaceAuthority::default();
        assert!(authority
            .approve_matching_picker_result(selected.clone(), &requested)
            .is_err());
        assert!(!authority.is_within_approved_root(&selected));
        assert!(!authority.is_within_approved_root(&requested));

        assert!(authority
            .approve_matching_picker_result(requested.clone(), &requested)
            .is_ok());
        assert!(authority
            .authorize_existing(&requested.to_string_lossy())
            .is_ok());
    }
}
