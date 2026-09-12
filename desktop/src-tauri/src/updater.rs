//! Native-only update handoff. The browser cannot name an executable or path.
use super::*;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::os::windows::fs::MetadataExt;

fn hex(value: &str, length: usize) -> bool {
    value.len() == length && value.bytes().all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn version(value: &str) -> bool {
    let parts: Vec<_> = value.split('.').collect();
    parts.len() == 3 && parts.iter().all(|part| !part.is_empty() && part.len() <= 9 && part.bytes().all(|byte| byte.is_ascii_digit()) && (part.len() == 1 || !part.starts_with('0')))
}

fn pointer(text: &str) -> Result<(String, String), String> {
    let parts: Vec<_> = text.lines().collect();
    if parts.len() != 2 || !hex(parts[0], 32) || !version(parts[1]) {
        return Err("更新任务标记无效；未启动任何更新程序".into());
    }
    Ok((parts[0].into(), parts[1].into()))
}

fn regular(path: &Path) -> Result<std::fs::Metadata, String> {
    for ancestor in path.ancestors() {
        let metadata = std::fs::symlink_metadata(ancestor).map_err(|_| "更新文件缺失")?;
        if metadata.file_attributes() & 0x400 != 0 {
            return Err("更新路径包含重解析点".into());
        }
    }
    let metadata = std::fs::symlink_metadata(path).map_err(|_| "更新文件缺失")?;
    if !metadata.is_file() {
        return Err("更新文件类型无效".into());
    }
    Ok(metadata)
}

fn read(path: &Path, limit: u64) -> Result<Vec<u8>, String> {
    if regular(path)?.len() > limit {
        return Err("更新控制文件过大".into());
    }
    std::fs::read(path).map_err(|_| "无法读取更新控制文件".into())
}

fn sha(path: &Path) -> Result<String, String> {
    let mut input = File::open(path).map_err(|_| "无法读取更新文件")?;
    let mut state = Sha256::new();
    let mut block = [0_u8; 65536];
    loop {
        let count = input.read(&mut block).map_err(|_| "无法核验更新文件")?;
        if count == 0 { break; }
        state.update(&block[..count]);
    }
    Ok(format!("{:x}", state.finalize()))
}

fn verify_file(path: &Path, row: &Value) -> Result<(), String> {
    let size = row["bytes"].as_u64().ok_or("更新大小无效")?;
    let expected = row["sha256"].as_str().ok_or("更新摘要缺失")?;
    if !hex(expected, 64) || regular(path)?.len() != size || sha(path)? != expected {
        return Err("更新文件摘要不匹配；已停止".into());
    }
    Ok(())
}

struct Bundle { job: PathBuf, python: PathBuf, nonce: String }

fn bundle(root: &Path, id: &str, target_version: &str) -> Result<Bundle, String> {
    if !hex(id, 32) || !version(target_version) { return Err("更新任务无效".into()); }
    let job = root.join(".pi/app-updates").join(id);
    let info: Value = serde_json::from_slice(&read(&job.join("job.json"), 65536)?).map_err(|_| "更新任务无效")?;
    let source_root = info["root"].as_str().ok_or("更新目录无效")?;
    let origin = info["origin_version"].as_str().ok_or("原安装版本缺失")?;
    if !version(origin) || !root.to_string_lossy().eq_ignore_ascii_case(source_root)
        || root.file_name().and_then(|name| name.to_str()) != Some(&format!("Agent4Market-{origin}"))
        || info["job_id"].as_str() != Some(id) || info["to_version"].as_str() != Some(target_version)
        || info["format"].as_u64() != Some(1) {
        return Err("更新任务与本安装不一致".into());
    }
    let nonce = info["nonce"].as_str().filter(|value| hex(value, 64)).ok_or("更新握手无效")?.to_string();
    let helpers = info["helpers"].as_object().ok_or("更新程序清单缺失")?;
    if helpers.len() != 5 { return Err("更新程序清单无效".into()); }
    for name in ["worker.py", "engine.py", "privacy.py", "installer_policy.py", "peer.py"] {
        verify_file(&job.join(name), helpers.get(name).ok_or("更新程序缺失")?)?;
    }
    let manifest_path = job.join("new-manifest.json");
    let raw = read(&manifest_path, 32 * 1024 * 1024)?;
    if Some(sha(&manifest_path)?.as_str()) != info["new_manifest_sha256"].as_str() {
        return Err("更新清单已变化".into());
    }
    let manifest: Value = serde_json::from_slice(&raw).map_err(|_| "更新清单无效")?;
    if manifest["version"].as_str() != Some(target_version) { return Err("更新版本不一致".into()); }
    let recovery = job.join("recovery");
    let rows = manifest["files"].as_array().filter(|rows| rows.len() <= 100000).ok_or("更新清单无效")?;
    let mut python_found = false;
    for row in rows {
        let name = row["path"].as_str().ok_or("更新路径无效")?;
        if name.starts_with(".venv/Scripts/") {
            if name.contains('\\') || name.contains(':') || name.split('/').any(|part| part.is_empty() || part == "." || part == ".." || part.ends_with(['.', ' '])) {
                return Err("更新解释器路径无效".into());
            }
            verify_file(&recovery.join(name), row)?;
            python_found |= name == ".venv/Scripts/python.exe";
        }
    }
    if !python_found { return Err("包内更新解释器缺失".into()); }
    let actual = String::from_utf8(read(&job.parent().unwrap().join("active"), 256)?).map_err(|_| "更新指针无效")?;
    if pointer(&actual)? != (id.into(), target_version.into()) { return Err("更新指针已变化".into()); }
    Ok(Bundle { job, python: recovery.join(".venv/Scripts/python.exe"), nonce })
}

fn clean_command(program: &Path) -> Command {
    let mut command = Command::new(program);
    command.env_clear();
    for (name, value) in env::vars_os() {
        let upper = name.to_string_lossy().to_ascii_uppercase();
        if ["SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "USERPROFILE", "USERNAME", "USERDOMAIN", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "COMMONPROGRAMFILES", "PATHEXT", "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS", "PATH"].contains(&upper.as_str()) {
            command.env(name, value);
        }
    }
    command.env("PYTHONDONTWRITEBYTECODE", "1").current_dir(env::var_os("SystemRoot").unwrap_or_else(|| "C:\\Windows".into()))
        .stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).creation_flags(CREATE_NO_WINDOW);
    command
}

fn handoff(root: &Path, id: &str, version: &str, recover: bool) -> Result<Bundle, String> {
    let accepted = bundle(root, id, version)?;
    let mut command = clean_command(&accepted.python);
    command.args(["-I", "-B"]).arg(accepted.job.join("worker.py"))
        .args(["--spawn", "--root"]).arg(root).args(["--job", id, "--version", version, "--parent-pid", &std::process::id().to_string()]);
    if recover { command.arg("--recover"); }
    let mut process = command.spawn().map_err(|_| "更新辅助程序无法启动")?;
    let deadline = Instant::now() + Duration::from_secs(50);
    while Instant::now() < deadline {
        match process.try_wait() {
            Ok(Some(status)) if status.success() => return Ok(accepted),
            Ok(Some(status)) if status.code() == Some(3) => return Err("UPDATE_ALREADY_RUNNING".into()),
            Ok(Some(_)) | Err(_) => return Err("更新辅助程序未完成安全握手".into()),
            _ => thread::sleep(Duration::from_millis(100)),
        }
    }
    let _ = process.kill();
    let _ = process.wait();
    Err("更新辅助程序握手超时".into())
}

fn control(nonce: &str, route: &str, post: bool) -> Result<String, String> {
    let address = SocketAddr::from(([127, 0, 0, 1], WORKBENCH_PORT));
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_secs(1)).map_err(|_| "更新控制连接失败")?;
    stream.set_read_timeout(Some(Duration::from_secs(3))).map_err(|_| "更新控制连接失败")?;
    stream.set_write_timeout(Some(Duration::from_secs(3))).map_err(|_| "更新控制连接失败")?;
    let (method, content) = if post { ("POST", "Content-Type: application/json\r\nContent-Length: 2\r\n") } else { ("GET", "") };
    let body = if post { "{}" } else { "" };
    let request = format!("{method} {route} HTTP/1.1\r\nHost: 127.0.0.1:8765\r\nX-Update-Control: {nonce}\r\n{content}Connection: close\r\n\r\n{body}");
    stream.write_all(request.as_bytes()).map_err(|_| "更新控制连接失败")?;
    let mut raw = Vec::new();
    stream.take(4097).read_to_end(&mut raw).map_err(|_| "更新控制响应失败")?;
    if raw.len() > 4096 { return Err("更新控制响应无效".into()); }
    let text = String::from_utf8(raw).map_err(|_| "更新控制响应无效")?;
    let (head, body) = text.split_once("\r\n\r\n").ok_or("更新控制响应无效")?;
    if !head.lines().next().is_some_and(|line| line == "HTTP/1.0 200 OK" || line == "HTTP/1.1 200 OK") { return Err("更新控制请求被拒绝".into()); }
    Ok(body.into())
}

fn wait_child(child: &mut Child, seconds: u64) -> bool {
    let deadline = Instant::now() + Duration::from_secs(seconds);
    while Instant::now() < deadline {
        if child.try_wait().ok().flatten().is_some() { return true; }
        thread::sleep(Duration::from_millis(100));
    }
    false
}

fn stop_for_update(app: &tauri::AppHandle, nonce: &str) -> Result<(), String> {
    let state = app.state::<RuntimeChildren>();
    let mut children = state.0.lock().map_err(|_| "运行时锁不可用")?;
    if children.len() != 2 || children[1].stdin.is_none() { return Err("请先关闭独立诊断窗口后重试更新".into()); }
    // Pi RPC consumes EOF, disposes its runtime/CLI hosts and removes leases.
    drop(children[1].stdin.take());
    if !wait_child(&mut children[1], 25) { return Err("智能核心未能安全退出".into()); }
    control(nonce, "/api/app-updates/native-stop", true)?;
    if !wait_child(&mut children[0], 15) { return Err("工作台尚未完成安全退出".into()); }
    runtime_jobs::wait_empty(10)?;
    children.clear();
    Ok(())
}

pub fn watch(app: tauri::AppHandle, root: PathBuf, nonce: String) {
    thread::spawn(move || loop {
        thread::sleep(Duration::from_millis(500));
        let Ok(response) = control(&nonce, "/api/app-updates/native", false) else { continue; };
        if response == "none\n" { continue; }
        let Ok((id, version)) = pointer(&response) else { continue; };
        let accepted = match handoff(&root, &id, &version, false) {
            Ok(value) => value,
            Err(_) => { let _ = control(&nonce, "/api/app-updates/native-abort", true); continue; }
        };
        if stop_for_update(&app, &nonce).is_ok() {
            let receipt = serde_json::json!({"nonce": accepted.nonce});
            if let Ok(mut file) = OpenOptions::new().write(true).create_new(true).open(accepted.job.join("stopped.json")) {
                let _ = file.write_all(receipt.to_string().as_bytes()).and_then(|_| file.sync_all());
            }
        }
        // Without the persisted stop receipt the worker only restores/starts
        // the old version. Ordinary cleanup is not accepted as a safe update.
        app.exit(0);
        break;
    });
}

pub fn recover_on_start() -> Result<bool, String> {
    env::remove_var("AGENT4MARKET_UPDATE_PENDING");
    let executable = env::current_exe().map_err(|_| "无法确认本安装目录")?;
    let root = executable.parent().ok_or("无法确认本安装目录")?;
    let active = root.join(".pi/app-updates/active");
    if !active.exists() { return Ok(false); }
    let text = String::from_utf8(read(&active, 256)?).map_err(|_| "更新恢复标记无效")?;
    let (id, version) = pointer(&text)?;
    let arguments: Vec<_> = env::args().collect();
    if arguments.len() == 4 && arguments[1] == "--update-trial" && arguments[2] == id {
        let accepted = bundle(root, &id, &version)?;
        let journal: Value = serde_json::from_slice(&read(&accepted.job.join("journal.json"), 32 * 1024 * 1024)?).map_err(|_| "更新启动日志无效")?;
        let package: Value = serde_json::from_slice(&read(&root.join("package.json"), 65536)?).map_err(|_| "更新版本无效")?;
        if arguments[3] != accepted.nonce || journal["phase"].as_str() != Some("validating") || package["version"].as_str() != Some(&version) {
            return Err("更新试运行握手无效".into());
        }
        env::set_var("AGENT4MARKET_UPDATE_PENDING", id);
        return Ok(false);
    }
    match handoff(root, &id, &version, true) {
        Err(code) if code == "UPDATE_ALREADY_RUNNING" => return Ok(true),
        result => { result?; }
    }
    Ok(true) // No server/model/UI is started from a potentially mixed tree.
}

pub fn trial_ready(root: &Path) -> Result<(), String> {
    let Ok(id) = env::var("AGENT4MARKET_UPDATE_PENDING") else { return Ok(()); };
    let text = String::from_utf8(read(&root.join(".pi/app-updates/active"), 256)?).map_err(|_| "更新启动标记无效")?;
    let (active_id, version) = pointer(&text)?;
    if active_id != id { return Err("更新启动任务已变化".into()); }
    let accepted = bundle(root, &id, &version)?;
    let receipt = serde_json::json!({"nonce": accepted.nonce, "pid": std::process::id()});
    let mut file = OpenOptions::new().write(true).create_new(true).open(accepted.job.join("launch-ready.json")).map_err(|_| "无法确认新版启动")?;
    file.write_all(receipt.to_string().as_bytes()).and_then(|_| file.sync_all()).map_err(|_| "无法保存新版启动确认".into())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn pointers_cannot_supply_paths_commands_or_versions_with_suffixes() {
        assert!(pointer(&format!("{}\n0.20.5\n", "a".repeat(32))).is_ok());
        for text in ["../outside\n0.20.5\n", "a\n0.20.5\n", "none\n", "aaaa\n1.2.3\nextra", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n1.2.3;cmd\n"] {
            assert!(pointer(text).is_err());
        }
        assert!(!version("01.2.3"));
        assert!(!version("1.2.3-rc.1"));
    }
}
