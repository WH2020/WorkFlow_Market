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

fn project_root() -> Result<PathBuf, String> {
    let mut candidates = Vec::new();
    if let Ok(executable) = env::current_exe() {
        if let Some(parent) = executable.parent() {
            candidates.push(parent.to_path_buf());
        }
    }
    if let Ok(current) = env::current_dir() {
        candidates.push(current);
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
    Err("Agent4Market.exe 必须放在完整的销售总监助手安装目录中。".into())
}

fn launcher_log(root: &Path) -> Result<File, String> {
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

fn python_command() -> Result<(String, Vec<String>), String> {
    let candidates = if cfg!(windows) {
        vec![("python.exe", vec![]), ("py.exe", vec!["-3.11"])]
    } else {
        vec![("python3", vec![]), ("python", vec![])]
    };
    for (program, prefix) in candidates {
        let output = Command::new(program)
            .args(&prefix)
            .arg("--version")
            .output();
        if output.as_ref().is_ok_and(|value| value.status.success()) {
            return Ok((
                program.to_string(),
                prefix.into_iter().map(str::to_string).collect(),
            ));
        }
    }
    Err("未找到 Python 3.11+；请先运行安装脚本。".into())
}

fn workbench_healthy() -> bool {
    let address = SocketAddr::from(([127, 0, 0, 1], WORKBENCH_PORT));
    let Ok(mut stream) = TcpStream::connect_timeout(&address, Duration::from_millis(700)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(900)));
    if stream
        .write_all(
            b"GET /api/bootstrap HTTP/1.1\r\nHost: 127.0.0.1:8765\r\nConnection: close\r\n\r\n",
        )
        .is_err()
    {
        return false;
    }
    let mut response = Vec::with_capacity(4096);
    let _ = stream.take(4096).read_to_end(&mut response);
    let text = String::from_utf8_lossy(&response);
    text.contains("200 OK") && text.contains("sales-director") && !text.contains("product-director")
}

fn start_workbench(root: &Path) -> Result<Child, String> {
    if workbench_healthy() {
        return Err("端口 8765 已有工作台运行；请先退出旧版本。".into());
    }
    let (program, mut arguments) = python_command()?;
    arguments.push(root.join("ui/server.py").to_string_lossy().to_string());
    arguments.extend([
        "--port".into(),
        WORKBENCH_PORT.to_string(),
        "--profile".into(),
        PROFILE_ID.into(),
    ]);
    let output = launcher_log(root)?;
    let error = output.try_clone().map_err(|value| value.to_string())?;
    let mut command = Command::new(program);
    command
        .args(arguments)
        .current_dir(root)
        .env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .env("WORKFLOW_AGENT_EDITION_PROFILE", PROFILE_ID)
        .stdout(Stdio::from(output))
        .stderr(Stdio::from(error));
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);
    command
        .spawn()
        .map_err(|value| format!("工作台启动失败：{value}"))
}

fn wait_for_workbench(child: &mut Child) -> bool {
    let deadline = Instant::now() + Duration::from_secs(20);
    while Instant::now() < deadline {
        if workbench_healthy() {
            return true;
        }
        if child.try_wait().ok().flatten().is_some() {
            return false;
        }
        thread::sleep(Duration::from_millis(250));
    }
    false
}

#[cfg(windows)]
fn pi_version_ok(root: &Path) -> bool {
    Command::new("powershell.exe")
        .args([
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            &root.join("scripts/start-windows.ps1").to_string_lossy(),
            "--version",
        ])
        .current_dir(root)
        .env("WORKFLOW_AGENT_PROFILE", PROFILE_ID)
        .env("WORKFLOW_AGENT_EDITION_PROFILE", PROFILE_ID)
        .creation_flags(CREATE_NO_WINDOW)
        .output()
        .is_ok_and(|output| output.status.success())
}

#[cfg(not(windows))]
fn pi_version_ok(root: &Path) -> bool {
    Command::new("bash")
        .args([
            root.join("scripts/start-macos.sh")
                .to_string_lossy()
                .as_ref(),
            "--version",
        ])
        .current_dir(root)
        .env("WORKFLOW_AGENT_PROFILE", PROFILE_ID)
        .env("WORKFLOW_AGENT_EDITION_PROFILE", PROFILE_ID)
        .output()
        .is_ok_and(|output| output.status.success())
}

#[cfg(windows)]
fn start_agent(root: &Path) -> Result<Child, String> {
    Command::new("powershell.exe")
        .args([
            "-NoLogo",
            "-NoProfile",
            "-NoExit",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            &root.join("scripts/start-windows.ps1").to_string_lossy(),
            "--approve",
        ])
        .current_dir(root)
        .env("WORKFLOW_AGENT_PROFILE", PROFILE_ID)
        .env("WORKFLOW_AGENT_EDITION_PROFILE", PROFILE_ID)
        .creation_flags(CREATE_NEW_CONSOLE)
        .spawn()
        .map_err(|value| format!("Pi 销售总监运行时启动失败：{value}"))
}

#[cfg(not(windows))]
fn start_agent(root: &Path) -> Result<Child, String> {
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
    let _ = Command::new("taskkill.exe")
        .args(["/PID", &child.id().to_string(), "/T", "/F"])
        .creation_flags(CREATE_NO_WINDOW)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
}

#[cfg(not(windows))]
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
}

fn self_test() -> i32 {
    let Ok(root) = project_root() else { return 2 };
    let Ok(mut server) = start_workbench(&root) else {
        return 2;
    };
    let healthy = wait_for_workbench(&mut server);
    let pi_ok = healthy && pi_version_ok(&root);
    stop_child(&mut server);
    if healthy && pi_ok {
        0
    } else {
        2
    }
}

fn main() {
    if env::args().any(|argument| argument == "--self-test") {
        std::process::exit(self_test());
    }

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
            env::set_current_dir(&root)?;
            let mut server = start_workbench(&root).map_err(std::io::Error::other)?;
            if !wait_for_workbench(&mut server) {
                stop_child(&mut server);
                return Err(std::io::Error::other("销售总监工作台未能启动").into());
            }
            let agent = match start_agent(&root) {
                Ok(child) => child,
                Err(error) => {
                    stop_child(&mut server);
                    return Err(std::io::Error::other(error).into());
                }
            };
            {
                let state = app.state::<RuntimeChildren>();
                let mut children = state
                    .0
                    .lock()
                    .map_err(|_| std::io::Error::other("运行时锁已损坏"))?;
                children.push(server);
                children.push(agent);
            }
            let window_result = WebviewWindowBuilder::new(
                app,
                "main",
                WebviewUrl::External("http://127.0.0.1:8765/".parse().expect("static URL")),
            )
            .title("销售总监 AI 助手")
            .inner_size(1280.0, 860.0)
            .min_inner_size(980.0, 680.0)
            .center()
            .on_navigation(|url| {
                url.scheme() == "http"
                    && url.host_str() == Some("127.0.0.1")
                    && url.port() == Some(WORKBENCH_PORT)
            })
            .build();
            if let Err(error) = window_result {
                cleanup(&app.state::<RuntimeChildren>());
                return Err(error.into());
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("销售总监桌面应用初始化失败");

    application.run(|app, event| {
        if matches!(event, RunEvent::Exit) {
            cleanup(&app.state::<RuntimeChildren>());
        }
    });
}
