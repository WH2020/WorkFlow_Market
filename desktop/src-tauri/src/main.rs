#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::env;
use std::fs::{File, OpenOptions};
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};
use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};

#[cfg(windows)]
use std::os::windows::process::CommandExt;
#[cfg(windows)]
use windows_sys::Win32::UI::WindowsAndMessaging::{MessageBoxW, MB_ICONERROR, MB_OK};

#[cfg(windows)]
mod updater;
#[cfg(windows)]
mod runtime_jobs;
#[cfg(any(target_os = "macos", test))]
mod macos_update_plan;
#[cfg(target_os = "macos")]
mod macos_updater;
#[cfg(target_os = "macos")]
mod posix_jobs;

const PROFILE_ID: &str = "sales-director";
const WORKBENCH_PORT: u16 = 8765;
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;
#[cfg(windows)]
const CREATE_NEW_CONSOLE: u32 = 0x0000_0010;

#[derive(Default)]
struct RuntimeChildren(Mutex<Vec<Child>>);

fn is_project_root(path: &Path) -> bool {
    let platform_runtime = if cfg!(windows) {
        path.join("scripts/start-windows.ps1").is_file()
            && path.join("node_modules/.bin/pi.CMD").is_file()
    } else {
        path.join("scripts/start-macos.sh").is_file() && path.join("node_modules/.bin/pi").is_file()
    };
    platform_runtime
        && path.join("ui/server.py").is_file()
        && path.join("profiles/sales-director/profile.json").is_file()
}

#[cfg(target_os = "macos")]
fn configured_macos_project_root() -> Option<PathBuf> {
    // Library/Application Support/Agent4Market/install-root is the ONLY
    // release authority. Ownership, private marker mode and ancestors are
    // checked by the native updater; CWD and executable ancestors are ignored.
    let path = macos_updater::probe_root().or_else(|| macos_updater::configured_root().ok())?;
    is_project_root(&path).then_some(path)
}

#[cfg(all(not(windows), not(target_os = "macos")))]
fn configured_macos_project_root() -> Option<PathBuf> {
    None
}

fn project_root() -> Result<PathBuf, String> {
    // Windows packages are a directory unit. Never search the caller's CWD or
    // ancestors: a copied EXE must not silently execute a different checkout.
    #[cfg(windows)]
    {
        let executable = env::current_exe().map_err(|error| error.to_string())?;
        return windows_project_root(&executable);
    }
    #[cfg(target_os = "macos")]
    {
        return configured_macos_project_root().ok_or_else(|| "Agent4Market.app 尚未关联受保护的运行目录；请先运行 scripts/setup-macos.sh。".into());
    }
    #[cfg(all(not(windows), not(target_os = "macos")))]
    {
        let mut candidates = Vec::new();
        if let Ok(executable) = env::current_exe() {
            if let Some(parent) = executable.parent() {
                candidates.push(parent.to_path_buf());
            }
        }
        if let Ok(current) = env::current_dir() {
            candidates.push(current);
        }
        if let Some(configured) = configured_macos_project_root() {
            candidates.push(configured);
        }
        for start in candidates {
            let mut cursor = Some(start.as_path());
            for _ in 0..8 {
                let Some(path) = cursor else { break };
                if is_project_root(path) {
                    return Ok(path.to_path_buf());
                }
                cursor = path.parent();
            }
        }
        Err(if cfg!(target_os = "macos") {
            "Agent4Market.app 尚未关联完整运行目录；请先运行 scripts/setup-macos.sh。".into()
        } else {
            "Agent4Market.exe 必须放在完整的销售总监助手安装目录中。".into()
        })
    }
}

#[cfg(windows)]
fn windows_project_root(executable: &Path) -> Result<PathBuf, String> {
    let root = executable.parent().ok_or("EXE 缺少运行目录")?;
    if !is_project_root(root) {
        return Err("Agent4Market.exe 必须与完整运行目录一起使用，不能单独移动 EXE。".into());
    }
    Ok(root.to_path_buf())
}

fn private_runtime(root: &Path) -> bool {
    cfg!(windows) && root.join("runtime/private-runtime.marker").exists()
}

#[cfg(target_os = "macos")]
fn configure_macos_runtime(command: &mut Command, root: &Path) {
    command.env("PYTHONDONTWRITEBYTECODE", "1").env("PYTHONNOUSERSITE", "1")
        .env_remove("PYTHONPATH").env_remove("PYTHONHOME").env_remove("NODE_PATH").env_remove("NODE_OPTIONS")
        .env_remove("DYLD_INSERT_LIBRARIES").env_remove("DYLD_LIBRARY_PATH");
    // Each installation has its own Pi configuration. A probe already has an
    // isolated temporary HOME/PI directory established by native early_entry.
    if env::var("AGENT4MARKET_UPDATE_PROBE").as_deref() != Ok("1") {
        command.env("PI_CODING_AGENT_DIR", root.join(".pi/agent"));
    }
    command.env_remove("AGENT4MARKET_UPDATE_CONTROL");
}

fn configure_private_runtime(command: &mut Command, root: &Path) -> Result<(), String> {
    if !private_runtime(root) {
        return Ok(());
    }
    let marker = root.join("runtime/private-runtime.marker");
    let marker_text = std::fs::read_to_string(&marker).map_err(|error| error.to_string())?;
    if marker_text.trim() != "Agent4Market private runtime v1" {
        return Err("私有运行目录标记无效，请重新打包。".into());
    }
    for relative in [
        ".venv/Scripts/python.exe",
        "runtime/node/node.exe",
        "node_modules/@earendil-works/pi-coding-agent/dist/cli.js",
    ] {
        let path = root.join(relative);
        let resolved = path
            .canonicalize()
            .map_err(|_| format!("包内依赖缺失：{relative}"))?;
        let resolved_root = root.canonicalize().map_err(|error| error.to_string())?;
        if !resolved.is_file() || !resolved.starts_with(&resolved_root) {
            return Err(format!("包内依赖越出运行目录：{relative}"));
        }
    }
    let mut paths = vec![
        root.join("runtime/node"),
        root.join(".venv/Scripts"),
        root.join("node_modules/.bin"),
    ];
    paths.extend(env::split_paths(&env::var_os("PATH").unwrap_or_default()));
    command
        .env(
            "PATH",
            env::join_paths(paths).map_err(|error| error.to_string())?,
        )
        .env("PI_CODING_AGENT_DIR", root.join(".pi/agent"))
        .env("PYTHONNOUSERSITE", "1")
        .env_remove("PYTHONPATH")
        .env_remove("PYTHONHOME")
        .env_remove("NODE_PATH")
        .env_remove("NODE_OPTIONS")
        .env_remove("AGENT4MARKET_UPDATE_CONTROL")
        .env_remove("AGENT4MARKET_UPDATE_PENDING");
    Ok(())
}

fn launcher_log(root: &Path) -> Result<File, String> {
    #[cfg(target_os = "macos")]
    if let Some(path) = macos_updater::probe_log() {
        return OpenOptions::new().create(true).append(true).open(path).map_err(|error| error.to_string());
    }
    let path = root.join(".pi/director-runtime/desktop-launcher.log");
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    }
    OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .map_err(|error| error.to_string())
}

fn ai_core_log(root: &Path) -> Result<File, String> {
    #[cfg(target_os = "macos")]
    if let Some(home) = macos_updater::trial_home() {
        return OpenOptions::new().create(true).append(true).open(home.join("ai-core.log")).map_err(|error| error.to_string());
    }
    let path = root.join(".pi/director-runtime/ai-core.log");
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
    }
    OpenOptions::new()
        .create(true)
        .write(true)
        .truncate(true)
        .open(path)
        .map_err(|error| error.to_string())
}

fn show_ai_core_window(root: &Path) -> bool {
    let path = root.join(".pi/director-runtime/desktop-settings.json");
    let Ok(metadata) = std::fs::symlink_metadata(&path) else {
        return false;
    };
    if !metadata.is_file() || metadata.file_type().is_symlink() || metadata.len() > 4096 {
        return false;
    }
    let Ok(contents) = std::fs::read_to_string(path) else {
        return false;
    };
    let compact: String = contents
        .chars()
        .filter(|character| !character.is_whitespace())
        .collect();
    compact
        .split_once("\"show_ai_core_window\":")
        .is_some_and(|(_, value)| value.starts_with("true"))
}

fn log_launcher_event(root: &Path, event: &str) {
    if let Ok(mut output) = launcher_log(root) {
        let _ = writeln!(output, "[desktop pid={}] {event}", std::process::id());
    }
}

#[cfg(windows)]
fn show_startup_error(message: &str) {
    let title: Vec<u16> = "销售总监智能助手启动失败\0".encode_utf16().collect();
    let body: Vec<u16> = format!(
        "销售总监智能助手未能启动。\n\n{message}\n\n请关闭旧版本后重试；详细记录位于安装目录的 .pi\\director-runtime\\desktop-launcher.log。\0"
    )
    .encode_utf16()
    .collect();
    unsafe {
        MessageBoxW(
            std::ptr::null_mut(),
            body.as_ptr(),
            title.as_ptr(),
            MB_OK | MB_ICONERROR,
        );
    }
}

#[cfg(target_os = "macos")]
fn show_startup_error(message: &str) {
    let safe_message = message
        .replace('\\', "\\\\")
        .replace('"', "\\\"")
        .replace('\r', " ")
        .replace('\n', " ");
    let script = format!(
        "display alert \"销售总监智能助手启动失败\" message \"{}\" as critical buttons {{\"好\"}} default button \"好\"",
        safe_message
    );
    let _ = Command::new("osascript").args(["-e", &script]).status();
}

#[cfg(all(not(windows), not(target_os = "macos")))]
fn show_startup_error(message: &str) {
    eprintln!("销售总监智能助手启动失败：{message}");
}

fn python_command(root: &Path) -> Result<(String, Vec<String>), String> {
    let local = if cfg!(windows) {
        root.join(".venv/Scripts/python.exe")
    } else {
        root.join(".venv/bin/python")
    };
    if local.is_file() || private_runtime(root) {
        if !local.is_file() {
            return Err("私有运行目录缺少包内 Python；不会回退到系统解释器。".into());
        }
        return Ok((local.to_string_lossy().into_owned(), Vec::new()));
    }
    let candidates = if cfg!(windows) {
        vec![("python.exe", vec![]), ("py.exe", vec!["-3.11"])]
    } else {
        vec![("python3", vec![]), ("python", vec![])]
    };
    for (program, prefix) in candidates {
        let output = Command::new(program)
            .args(&prefix)
            .args(["-c", "import sys; print(sys.executable)"])
            .output();
        if let Ok(output) = output {
            if !output.status.success() {
                continue;
            }
            let executable = String::from_utf8_lossy(&output.stdout).trim().to_string();
            if executable.is_empty() || !Path::new(&executable).is_file() {
                continue;
            }
            if Command::new(&executable)
                .arg("--version")
                .output()
                .is_ok_and(|value| value.status.success())
            {
                // Launch the interpreter itself, not a Store/py shim that can
                // exit after spawning an untracked server process.
                return Ok((executable, Vec::new()));
            }
        }
    }
    Err("未找到 Python 3.11+；请先运行安装脚本。".into())
}

fn workbench_healthy(startup_token: &str) -> bool {
    let address = SocketAddr::from(([127, 0, 0, 1], WORKBENCH_PORT));
    let Ok(mut stream) = TcpStream::connect_timeout(&address, Duration::from_millis(700)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(900)));
    if stream
        .write_all(b"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1:8765\r\nConnection: close\r\n\r\n")
        .is_err()
    {
        return false;
    }
    let mut response = Vec::with_capacity(4096);
    let _ = stream.take(4096).read_to_end(&mut response);
    let text = String::from_utf8_lossy(&response);
    health_response_matches(&text, startup_token)
}

fn health_response_matches(text: &str, startup_token: &str) -> bool {
    text.contains("200 OK")
        && text.contains("\"status\": \"ok\"")
        && text.contains("\"profile_id\": \"sales-director\"")
        && text.contains(&format!("\"desktop_startup_token\": \"{startup_token}\""))
}

fn start_workbench(
    root: &Path,
    scheduler_enabled: bool,
    startup_token: &str,
    update_control: Option<&str>,
) -> Result<Child, String> {
    let address = SocketAddr::from(([127, 0, 0, 1], WORKBENCH_PORT));
    if TcpStream::connect_timeout(&address, Duration::from_millis(700)).is_ok() {
        return Err("端口 8765 已被占用；不会接管已有服务，请先关闭占用程序。".into());
    }
    let (program, mut arguments) = python_command(root)?;
    arguments.push(root.join("ui/server.py").to_string_lossy().to_string());
    arguments.extend([
        "--port".into(),
        WORKBENCH_PORT.to_string(),
        "--profile".into(),
        PROFILE_ID.into(),
    ]);
    if !scheduler_enabled {
        arguments.push("--disable-scheduler".into());
    }
    let output = launcher_log(root)?;
    let error = output.try_clone().map_err(|value| value.to_string())?;
    let mut command = Command::new(program);
    configure_private_runtime(&mut command, root)?;
    #[cfg(target_os = "macos")]
    configure_macos_runtime(&mut command, root);
    command
        .args(arguments)
        .current_dir(root)
        .env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .env("PYTHONUNBUFFERED", "1")
        .env("WORKFLOW_AGENT_EDITION_PROFILE", PROFILE_ID)
        .env("AGENT4MARKET_DESKTOP_STARTUP_TOKEN", startup_token)
        .env("AGENT4MARKET_DESKTOP_PID", std::process::id().to_string())
        .stdout(Stdio::from(output))
        .stderr(Stdio::from(error));
    if let Some(control) = update_control {
        command.env("AGENT4MARKET_UPDATE_CONTROL", control);
        if let Some(pending) = env::var_os("AGENT4MARKET_UPDATE_PENDING") {
            command.env("AGENT4MARKET_UPDATE_PENDING", pending);
        }
    }
    #[cfg(windows)]
    return runtime_jobs::spawn(&mut command, CREATE_NO_WINDOW);
    #[cfg(target_os = "macos")]
    return posix_jobs::spawn(&mut command);
    #[cfg(all(not(windows), not(target_os = "macos")))]
    command
        .spawn()
        .map_err(|value| format!("工作台启动失败：{value}"))
}

fn wait_for_workbench(child: &mut Child, startup_token: &str) -> bool {
    // A clean macOS install can spend tens of seconds warming Python and the
    // local package cache.  Treat that as startup latency, not a crash.
    let deadline = Instant::now() + Duration::from_secs(60);
    while Instant::now() < deadline {
        #[cfg(target_os = "macos")]
        if posix_jobs::exited(child).unwrap_or(true) { return false; }
        #[cfg(not(target_os = "macos"))]
        if child.try_wait().ok().flatten().is_some() {
            return false;
        }
        if workbench_healthy(startup_token) {
            return true;
        }
        thread::sleep(Duration::from_millis(250));
    }
    false
}

fn pi_version_ok(root: &Path) -> bool {
    // Packaging self-tests must not depend on a user's saved model/provider
    // configuration. Exercise the reviewed project-local Pi CLI directly;
    // the Python launch wrapper is covered independently and runs again when
    // the user starts the embedded AI core.
    let pi_cli = root.join("node_modules/@earendil-works/pi-coding-agent/dist/cli.js");
    if !pi_cli.is_file() {
        return false;
    }
    let local_node = root.join("runtime/node/node.exe");
    let mut command = if local_node.is_file() || private_runtime(root) {
        Command::new(local_node)
    } else {
        Command::new("node")
    };
    if configure_private_runtime(&mut command, root).is_err() {
        return false;
    }
    #[cfg(target_os = "macos")]
    configure_macos_runtime(&mut command, root);
    command
        .arg(pi_cli)
        .arg("--version")
        .current_dir(root)
        .env("NO_COLOR", "1")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);
    command.output().is_ok_and(|output| {
        output.status.success() && !String::from_utf8_lossy(&output.stdout).trim().is_empty()
    })
}

#[cfg(windows)]
fn start_agent(root: &Path, show_window: bool) -> Result<Child, String> {
    if !show_window {
        let output = ai_core_log(root)?;
        let error = output.try_clone().map_err(|value| value.to_string())?;
        let mut command = Command::new("powershell.exe");
        configure_private_runtime(&mut command, root)?;
        command
            .args([
                "-NoLogo",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                &root.join("scripts/start-windows.ps1").to_string_lossy(),
                "--mode",
                "rpc",
                "--approve",
            ])
            .current_dir(root)
            .env("WORKFLOW_AGENT_PROFILE", PROFILE_ID)
            .env("WORKFLOW_AGENT_EDITION_PROFILE", PROFILE_ID)
            .stdin(Stdio::piped())
            .stdout(Stdio::from(output))
            .stderr(Stdio::from(error))
            .creation_flags(CREATE_NO_WINDOW);
        return runtime_jobs::spawn(&mut command, CREATE_NO_WINDOW);
    }
    // Optional diagnostics mode gets its own console.  Launch PowerShell
    // directly so cmd.exe cannot reinterpret a localized window title as a file.
    let mut command = Command::new("powershell.exe");
    configure_private_runtime(&mut command, root)?;
    command
        .args([
            "-NoLogo",
            "-NoProfile",
            "-NoExit",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            &root.join("scripts/start-windows.ps1").to_string_lossy(),
            "-KeepOpen",
            "--approve",
        ])
        .current_dir(root)
        .env("WORKFLOW_AGENT_PROFILE", PROFILE_ID)
        .env("WORKFLOW_AGENT_EDITION_PROFILE", PROFILE_ID);
    runtime_jobs::spawn(&mut command, CREATE_NEW_CONSOLE)
}

#[cfg(target_os = "macos")]
fn start_agent(root: &Path, show_window: bool) -> Result<Child, String> {
    if show_window {
        // Older settings may request an unowned Terminal launch. Keep that
        // setting/data intact, but use the controlled core so updates can
        // safely stop every worker and an old preference cannot block boot.
        log_launcher_event(root, "macOS diagnostics requested: using controlled embedded AI core; see ai-core.log");
    }
    let output = ai_core_log(root)?;
    let error = output.try_clone().map_err(|value| value.to_string())?;
    if let Some(home) = macos_updater::trial_home() {
        let mut command = Command::new("node");
        configure_macos_runtime(&mut command, root);
        command.arg(root.join("node_modules/@earendil-works/pi-coding-agent/dist/cli.js"))
            .args(["--mode", "rpc", "--no-session", "--no-extensions", "--no-skills", "--no-prompt-templates", "--no-themes", "--extension"])
            .arg(root.join("pi/extensions/vertical-workflow.ts"))
            .current_dir(root).env("HOME", &home).env("TMPDIR", home.join("tmp"))
            .env("PI_CODING_AGENT_DIR", home.join("pi")).env_remove("CODEX_HOME")
            .env("CLAUDE_CONFIG_DIR", home.join("claude"))
            .env("WORKFLOW_AGENT_PROFILE", PROFILE_ID).env("WORKFLOW_AGENT_EDITION_PROFILE", PROFILE_ID)
            .stdin(Stdio::piped()).stdout(Stdio::from(output)).stderr(Stdio::from(error));
        return posix_jobs::spawn(&mut command);
    }
    let (python, prefix) = python_command(root)?;
    let mut command = Command::new(python);
    configure_macos_runtime(&mut command, root);
    // Direct argv avoids Terminal/shell quoting and uses exactly the same
    // interpreter as the workbench, including the enrolled .venv on macOS.
    command.args(prefix).args(["-m", "agent_platform", "launch", "--", "--mode", "rpc", "--approve"])
        .current_dir(root).env("WORKFLOW_AGENT_PROFILE", PROFILE_ID).env("WORKFLOW_AGENT_EDITION_PROFILE", PROFILE_ID)
        .stdin(Stdio::piped()).stdout(Stdio::from(output)).stderr(Stdio::from(error));
    posix_jobs::spawn(&mut command)
}

#[cfg(all(not(windows), not(target_os = "macos")))]
fn start_agent(root: &Path, show_window: bool) -> Result<Child, String> {
    if !show_window {
        let output = ai_core_log(root)?;
        let error = output.try_clone().map_err(|value| value.to_string())?;
        return Command::new("bash")
            .arg(root.join("scripts/start-macos.sh"))
            .args(["--mode", "rpc", "--approve"])
            .current_dir(root)
            .env("WORKFLOW_AGENT_PROFILE", PROFILE_ID)
            .env("WORKFLOW_AGENT_EDITION_PROFILE", PROFILE_ID)
            .stdin(Stdio::piped())
            .stdout(Stdio::from(output))
            .stderr(Stdio::from(error))
            .spawn()
            .map_err(|value| format!("嵌入式 Pi 销售总监运行时启动失败：{value}"));
    }
    let script = format!(
        "cd '{}' && WORKFLOW_AGENT_PROFILE={} WORKFLOW_AGENT_EDITION_PROFILE={} ./scripts/start-macos.sh --approve",
        root.display(), PROFILE_ID, PROFILE_ID
    );
    Command::new("osascript")
        .args([
            "-e",
            &format!("tell application \"Terminal\" to do script {:?}", script),
        ])
        .spawn()
        .map_err(|value| format!("Pi 销售总监运行时启动失败：{value}"))
}

#[cfg(windows)]
fn stop_child(child: &mut Child) {
    if runtime_jobs::stop(child.id()) { let _ = child.wait(); return; }
    let _ = Command::new("taskkill.exe")
        .args(["/PID", &child.id().to_string(), "/T", "/F"])
        .creation_flags(CREATE_NO_WINDOW)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
}

#[cfg(target_os = "macos")]
fn stop_child(child: &mut Child) {
    let _ = posix_jobs::stop(child);
}

#[cfg(all(not(windows), not(target_os = "macos")))]
fn stop_child(child: &mut Child) {
    let _ = child.kill();
}

fn cleanup(children: &RuntimeChildren) {
    if let Ok(mut locked) = children.0.lock() {
        for child in locked.iter_mut().rev() {
            stop_child(child);
        }
        locked.clear();
    }
    #[cfg(target_os = "macos")]
    if posix_jobs::trial_cohort() {
        // A committed trial keeps its isolated native-led cohort. On final
        // GUI exit, no WebView/Python/Node descendant may outlive that cohort.
        unsafe { libc::kill(-libc::getpgrp(), libc::SIGKILL); }
    }
}

fn self_test() -> i32 {
    let root = match project_root() {
        Ok(root) => root,
        Err(error) => {
            eprintln!("Agent4Market self-test could not find its runtime: {error}");
            return 2;
        }
    };
    let startup_token = match new_startup_token() {
        Ok(token) => token,
        Err(_) => return 2,
    };
    let mut server = match start_workbench(&root, false, &startup_token, None) {
        Ok(server) => server,
        Err(error) => {
            eprintln!("Agent4Market self-test could not start the workbench: {error}");
            return 2;
        }
    };
    let healthy = wait_for_workbench(&mut server, &startup_token);
    let pi_ok = healthy && pi_version_ok(&root);
    if !healthy {
        #[cfg(target_os = "macos")]
        let child_state = if posix_jobs::exited(&server).unwrap_or(true) { "exited" } else { "still running" };
        #[cfg(not(target_os = "macos"))]
        let child_state = server
            .try_wait()
            .ok()
            .flatten()
            .map_or_else(|| "still running".to_string(), |status| status.to_string());
        eprintln!("Agent4Market self-test health check failed; workbench child is {child_state}.");
        let log_path = root.join(".pi/director-runtime/desktop-launcher.log");
        #[cfg(target_os = "macos")]
        let log_path = macos_updater::probe_log().unwrap_or(log_path);
        if let Ok(log) = std::fs::read_to_string(&log_path) {
            let tail_start = log
                .char_indices()
                .rev()
                .nth(3_999)
                .map_or(0, |(index, _)| index);
            eprintln!("Agent4Market workbench log tail:\n{}", &log[tail_start..]);
        } else {
            eprintln!(
                "Agent4Market workbench log is unavailable at {}.",
                log_path.display()
            );
        }
    } else if !pi_ok {
        eprintln!("Agent4Market self-test could not validate the Pi runtime.");
    }
    stop_child(&mut server);
    if healthy && pi_ok {
        0
    } else {
        2
    }
}

fn new_startup_token() -> Result<String, getrandom::Error> {
    let mut bytes = [0_u8; 32];
    getrandom::fill(&mut bytes)?;
    Ok(bytes.iter().map(|value| format!("{value:02x}")).collect())
}

fn main() {
    #[cfg(target_os = "macos")]
    if let Some(code) = macos_updater::early_entry() { std::process::exit(code); }
    #[cfg(not(target_os = "macos"))]
    if env::args().any(|argument| argument == "--update-self-test") {
        // The server's probe mode does not open databases, run schedules or
        // expose business APIs. Program rollback therefore needs no data undo.
        env::set_var("AGENT4MARKET_UPDATE_PROBE", "1");
        std::process::exit(self_test());
    }
    #[cfg(not(target_os = "macos"))]
    if env::args().any(|argument| argument == "--self-test") {
        std::process::exit(self_test());
    }

    #[cfg(windows)]
    match updater::recover_on_start() {
        Ok(true) => return,
        Err(error) => { show_startup_error(&error); return; }
        Ok(false) => {}
    }
    #[cfg(target_os = "macos")]
    match macos_updater::recover_on_start() {
        Ok(true) => return,
        Err(error) => { show_startup_error(&error); return; }
        Ok(false) => {}
    }
    #[cfg(target_os = "macos")]
    if env::args().any(|argument| argument == "--self-test") { std::process::exit(self_test()); }

    let application = tauri::Builder::default()
        .manage(RuntimeChildren::default())
        .plugin(tauri_plugin_single_instance::init(
            |app, _arguments, _cwd| {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.show();
                    let _ = window.set_focus();
                }
            },
        ))
        .setup(|app| {
            let root = project_root().map_err(std::io::Error::other)?;
            log_launcher_event(&root, "setup started");
            env::set_current_dir(&root)?;
            let ui_self_test = env::args().any(|argument| argument == "--ui-self-test");
            let update_control = new_startup_token().map_err(|error| std::io::Error::other(error.to_string()))?;
            // Separate from the public health-instance token; never returned
            // by bootstrap and never accepted from browser update requests.
            let startup_token =
                new_startup_token().map_err(|error| std::io::Error::other(error.to_string()))?;
            let mut server = start_workbench(&root, !ui_self_test, &startup_token, Some(&update_control))
                .map_err(std::io::Error::other)?;
            if !wait_for_workbench(&mut server, &startup_token) {
                stop_child(&mut server);
                return Err(std::io::Error::other("销售总监工作台未能启动").into());
            }
            log_launcher_event(&root, "workbench ready");
            log_launcher_event(&root, "building main window");
            let window_builder = WebviewWindowBuilder::new(
                app,
                "main",
                WebviewUrl::External("http://127.0.0.1:8765/".parse().expect("static URL")),
            )
            .title("销售总监智能助手")
            .inner_size(1280.0, 860.0)
            .min_inner_size(980.0, 680.0)
            .center()
            .on_navigation(|url| {
                url.scheme() == "http"
                    && url.host_str() == Some("127.0.0.1")
                    && url.port() == Some(WORKBENCH_PORT)
            });
            #[cfg(target_os = "macos")]
            let window_builder = window_builder.visible(macos_updater::trial_home().is_none());
            #[cfg(windows)]
            let window_builder = if private_runtime(&root) {
                window_builder.data_directory(root.join(".pi/webview2"))
            } else {
                window_builder
            };
            let window = match window_builder.build() {
                Ok(window) => window,
                Err(error) => {
                    stop_child(&mut server);
                    return Err(error.into());
                }
            };
            log_launcher_event(&root, "main window ready");
            if ui_self_test {
                log_launcher_event(&root, "UI self-test: scheduler and AI core disabled");
                let state = app.state::<RuntimeChildren>();
                match state.0.lock() {
                    Ok(mut children) => children.push(server),
                    Err(_) => {
                        let _ = window.close();
                        stop_child(&mut server);
                        return Err(std::io::Error::other("运行时锁已损坏").into());
                    }
                }
                return Ok(());
            }
            let show_core_window = show_ai_core_window(&root);
            log_launcher_event(
                &root,
                if show_core_window {
                    "starting AI core in visible diagnostics mode"
                } else {
                    "starting embedded AI core"
                },
            );
            let mut agent = match start_agent(&root, show_core_window) {
                Ok(child) => child,
                Err(error) => {
                    let _ = window.close();
                    stop_child(&mut server);
                    return Err(std::io::Error::other(error).into());
                }
            };
            log_launcher_event(&root, "AI core launcher started");
            let state = app.state::<RuntimeChildren>();
            match state.0.lock() {
                Ok(mut children) => {
                    children.push(server);
                    children.push(agent);
                }
                Err(_) => {
                    let _ = window.close();
                    stop_child(&mut agent);
                    stop_child(&mut server);
                    return Err(std::io::Error::other("运行时锁已损坏").into());
                }
            }
            #[cfg(windows)]
            if private_runtime(&root) {
                updater::trial_ready(&root).map_err(std::io::Error::other)?;
                updater::watch(app.handle().clone(), root.clone(), update_control);
            }
            #[cfg(target_os = "macos")]
            {
                macos_updater::trial_ready(&root).map_err(std::io::Error::other)?;
                macos_updater::watch(app.handle().clone(), root.clone(), update_control);
            }
            Ok(())
        })
        .build(tauri::generate_context!());

    let application = match application {
        Ok(application) => application,
        Err(error) => {
            show_startup_error(&error.to_string());
            return;
        }
    };

    application.run(|app, event| {
        if matches!(event, RunEvent::Exit) {
            cleanup(&app.state::<RuntimeChildren>());
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn startup_token_is_unique_and_256_bit() {
        let first = new_startup_token().unwrap();
        let second = new_startup_token().unwrap();
        assert_eq!(first.len(), 64);
        assert!(first.chars().all(|value| value.is_ascii_hexdigit()));
        assert_ne!(first, second);
    }

    #[test]
    fn health_requires_this_launch_token() {
        let response = "HTTP/1.1 200 OK\r\n\r\n{\"status\": \"ok\", \"profile_id\": \"sales-director\", \"desktop_startup_token\": \"owned\"}";
        assert!(health_response_matches(response, "owned"));
        assert!(!health_response_matches(response, "foreign"));
        assert!(!health_response_matches(
            "200 OK {\"status\": \"ok\", \"profile_id\": \"sales-director\"}",
            "owned"
        ));
    }

    #[cfg(windows)]
    #[test]
    fn windows_root_is_only_the_executable_parent() {
        let root = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .parent()
            .unwrap();
        assert_eq!(
            windows_project_root(&root.join("Agent4Market.exe")).unwrap(),
            root
        );
        assert!(windows_project_root(&root.join("desktop/src-tauri/Agent4Market.exe")).is_err());
        assert!(windows_project_root(&root.join("outputs/detached/Agent4Market.exe")).is_err());
    }
}
