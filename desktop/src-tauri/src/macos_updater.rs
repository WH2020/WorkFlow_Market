//! Independent native macOS update worker, including pre-runtime crash recovery.
//! The copied Mach-O executes this branch before Tauri/WebView/Python startup.
use super::macos_update_plan as plan;
use super::*;
use serde_json::{json, Value};
use std::ffi::CString;
use std::fs;
use std::os::fd::AsRawFd;
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt};
use std::os::unix::process::CommandExt;
use std::sync::OnceLock;

static PROBE_ROOT: OnceLock<PathBuf> = OnceLock::new();
static PROBE_LOG: OnceLock<PathBuf> = OnceLock::new();
static TRIAL_HOME: OnceLock<PathBuf> = OnceLock::new();

pub fn probe_root() -> Option<PathBuf> {
    PROBE_ROOT.get().cloned()
}
pub fn probe_log() -> Option<PathBuf> {
    PROBE_LOG.get().cloned()
}
pub fn trial_home() -> Option<PathBuf> {
    TRIAL_HOME.get().cloned()
}

fn support() -> plan::Result<PathBuf> {
    let home = PathBuf::from(env::var_os("HOME").ok_or("HOME_UNAVAILABLE")?);
    let directory = home.join("Library/Application Support/Agent4Market");
    plan::directory(&directory, true)?;
    Ok(directory)
}

fn read_text(path: &Path, limit: u64) -> plan::Result<String> {
    plan::regular(
        path.parent().ok_or("INVALID_PATH")?,
        path.file_name()
            .and_then(|v| v.to_str())
            .ok_or("INVALID_PATH")?,
        false,
    )?;
    plan::require(
        path.metadata().map_err(|_| "READ_FAILED")?.len() <= limit,
        "FILE_TOO_LARGE",
    )?;
    fs::read_to_string(path).map_err(|_| "READ_FAILED".into())
}

pub fn configured_root() -> plan::Result<PathBuf> {
    let marker = support()?.join("install-root");
    let value = read_text(&marker, 4096)?;
    plan::require(
        marker.metadata().map_err(|_| "READ_FAILED")?.mode() & 0o077 == 0,
        "PRIVATE_DIRECTORY_REQUIRED",
    )?;
    let root = PathBuf::from(value.trim());
    plan::require(root.is_absolute(), "INVALID_ROOT")?;
    plan::directory(&root, false)?;
    plan::require(
        root.canonicalize().map_err(|_| "INVALID_ROOT")? == root,
        "INVALID_ROOT",
    )?;
    Ok(root)
}

fn pointer(path: &Path) -> plan::Result<(String, String)> {
    let value = read_text(path, 256)?;
    let parts: Vec<_> = value.lines().collect();
    plan::require(
        parts.len() == 2 && plan::hex(parts[0], 32) && plan::version(parts[1]).is_some(),
        "INVALID_JOB",
    )?;
    Ok((parts[0].into(), parts[1].into()))
}

struct Job {
    id: String,
    root: PathBuf,
    directory: PathBuf,
    workspace: PathBuf,
    app: PathBuf,
    staged_app: PathBuf,
    info: Value,
    old: Value,
    new: Value,
}

fn load(id: &str) -> plan::Result<Job> {
    plan::require(plan::hex(id, 32), "INVALID_JOB")?;
    let directory = support()?.join("app-updates").join(id);
    plan::directory(&directory, true)?;
    let info = plan::read(&directory.join("job.json"), 65536)?;
    let root = configured_root()?;
    plan::require(
        info["format"] == 1
            && info["platform"] == "macos-universal"
            && info["job_id"] == id
            && Path::new(plan::text(&info, "root")?) == root
            && plan::hex(plan::text(&info, "nonce")?, 64)
            && plan::version(plan::text(&info, "from_version")?).is_some()
            && plan::version(plan::text(&info, "to_version")?).is_some(),
        "INVALID_JOB",
    )?;
    let (active, version) = pointer(&directory.parent().unwrap().join("active"))?;
    plan::require(active == id && info["to_version"] == version, "INVALID_JOB")?;
    let helper = plan::regular(&directory, "native-helper", false)?;
    plan::require(
        plan::sha(&helper)? == plan::text(&info, "helper_sha256")?,
        "HELPER_CHANGED",
    )?;
    let old = plan::read(&directory.join("old-manifest.json"), 32 * 1024 * 1024)?;
    let new = plan::read(&directory.join("new-manifest.json"), 32 * 1024 * 1024)?;
    plan::require(
        plan::sha(&directory.join("old-manifest.json"))?
            == plan::text(&info, "old_manifest_sha256")?
            && plan::sha(&directory.join("new-manifest.json"))?
                == plan::text(&info, "new_manifest_sha256")?
            && old["version"] == info["from_version"]
            && new["version"] == info["to_version"],
        "PLAN_CHANGED",
    )?;
    plan::operations(&old, &new)?;
    let workspace = root.join(".pi/app-updates").join(id);
    plan::directory(&workspace, true)?;
    plan::require(
        plan::read(&workspace.join("job.json"), 65536)? == info,
        "PLAN_CHANGED",
    )?;
    let home = PathBuf::from(env::var_os("HOME").ok_or("HOME_UNAVAILABLE")?);
    let applications = home.join("Applications");
    plan::directory(&applications, false)?;
    Ok(Job {
        id: id.into(),
        root,
        directory,
        workspace,
        app: applications.join("Agent4Market.app"),
        staged_app: applications.join(format!(".Agent4Market-update-{id}.app")),
        info,
        old,
        new,
    })
}

fn clean(program: &Path) -> Command {
    let mut command = Command::new(program);
    command.env_clear();
    for name in [
        "HOME",
        "USER",
        "LOGNAME",
        "PATH",
        "LANG",
        "LC_CTYPE",
        "TMPDIR",
        "WORKFLOW_LIBREOFFICE_PATH",
    ] {
        if let Some(value) = env::var_os(name) {
            command.env(name, value);
        }
    }
    command
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .env("PYTHONNOUSERSITE", "1")
        .current_dir("/")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    command
}

fn checked(mut command: Command, seconds: u64) -> plan::Result<()> {
    let mut child = posix_jobs::spawn(&mut command)?;
    let waited = posix_jobs::wait_leader(&child, seconds);
    let succeeded = waited.is_ok() && posix_jobs::succeeded(&child)?;
    posix_jobs::stop(&mut child)?;
    plan::require(succeeded, "STARTUP_PROBE_FAILED")
}

fn bundle_rows(root: &Path) -> plan::Result<Vec<Value>> {
    plan::directory(root, false)?;
    let mut result = Vec::new();
    let mut directories = vec![root.to_path_buf()];
    while let Some(directory) = directories.pop() {
        for entry in fs::read_dir(&directory).map_err(|_| "BUNDLE_READ_FAILED")? {
            let entry = entry.map_err(|_| "BUNDLE_READ_FAILED")?;
            let path = entry.path();
            let relative = path
                .strip_prefix(root)
                .map_err(|_| "INVALID_PATH")?
                .to_str()
                .ok_or("INVALID_PATH")?
                .to_string();
            plan::relative(&relative)?;
            plan::require(
                relative == "Contents" || relative.starts_with("Contents/"),
                "INVALID_BUNDLE",
            )?;
            let metadata = fs::symlink_metadata(&path).map_err(|_| "BUNDLE_READ_FAILED")?;
            plan::require(
                metadata.uid() == unsafe { libc::getuid() },
                "UNSAFE_OWNER_OR_MODE",
            )?;
            if metadata.file_type().is_symlink() {
                let target = fs::read_link(&path).map_err(|_| "LINK_REFUSED")?;
                plan::require(
                    !target.is_absolute()
                        && path
                            .canonicalize()
                            .map_err(|_| "LINK_REFUSED")?
                            .starts_with(root.join("Contents")),
                    "LINK_REFUSED",
                )?;
                result.push(
                    json!({"path": relative, "link": target.to_str().ok_or("INVALID_PATH")?}),
                );
            } else {
                plan::macos_acl(&path, metadata.mode())?;
                plan::require(metadata.mode() & 0o022 == 0, "UNSAFE_OWNER_OR_MODE")?;
                if metadata.is_dir() {
                    directories.push(path);
                } else {
                    plan::require(
                        metadata.is_file() && metadata.nlink() == 1,
                        "NON_REGULAR_FILE",
                    )?;
                    result.push(json!({"path": relative, "bytes": metadata.len(), "sha256": plan::sha(&path)?, "mode": metadata.mode() & 0o777}));
                }
            }
            plan::require(
                result.len() + directories.len() <= 110000,
                "PAYLOAD_TOO_LARGE",
            )?;
        }
    }
    result.sort_by(|a, b| a["path"].as_str().cmp(&b["path"].as_str()));
    plan::require(
        result
            .iter()
            .any(|row| row["path"] == "Contents/MacOS/Agent4Market")
            && result
                .iter()
                .any(|row| row["path"] == "Contents/Info.plist"),
        "INVALID_BUNDLE",
    )?;
    Ok(result)
}

fn verify_bundle(path: &Path, manifest: &Value) -> plan::Result<()> {
    plan::require(
        json!(bundle_rows(path)?) == manifest["app_files"],
        "BUNDLE_MODIFIED",
    )?;
    let info = plist::Value::from_file(plan::regular(path, "Contents/Info.plist", false)?)
        .map_err(|_| "INVALID_BUNDLE")?;
    let info = info.as_dictionary().ok_or("INVALID_BUNDLE")?;
    plan::require(
        info.get("CFBundleIdentifier")
            .and_then(plist::Value::as_string)
            == Some("com.workflowmarket.salesdirector")
            && info
                .get("CFBundleExecutable")
                .and_then(plist::Value::as_string)
                == Some("Agent4Market")
            && info
                .get("CFBundleShortVersionString")
                .and_then(plist::Value::as_string)
                == manifest["version"].as_str(),
        "BUNDLE_VERSION_CHANGED",
    )?;
    let mut command = clean(Path::new("/usr/bin/codesign"));
    command.args(["--verify", "--deep", "--strict"]).arg(path);
    checked(command, 60).map_err(|_| "BUNDLE_SIGNATURE_INVALID".into())
}

fn swap_apps(job: &Job) -> plan::Result<()> {
    plan::directory(&job.app, false)?;
    plan::directory(&job.staged_app, false)?;
    plan::require(
        job.app.metadata().map_err(|_| "READ_FAILED")?.dev()
            == job.staged_app.metadata().map_err(|_| "READ_FAILED")?.dev(),
        "CROSS_VOLUME_APP_SWAP",
    )?;
    let current = CString::new(job.app.as_os_str().as_bytes()).map_err(|_| "INVALID_PATH")?;
    let stage = CString::new(job.staged_app.as_os_str().as_bytes()).map_err(|_| "INVALID_PATH")?;
    // Apple xnu sys/stdio.h: RENAME_SWAP=0x2. Unsupported filesystems refuse;
    // there is deliberately no two-rename fallback with a missing-app window.
    let result = unsafe { libc::renamex_np(current.as_ptr(), stage.as_ptr(), 0x2) };
    plan::require(result == 0, "ATOMIC_APP_SWAP_UNAVAILABLE")?;
    plan::sync(job.app.parent().unwrap())
}

fn port_free() -> bool {
    TcpStream::connect_timeout(
        &SocketAddr::from(([127, 0, 0, 1], WORKBENCH_PORT)),
        Duration::from_millis(700),
    )
    .is_err()
}

fn receipt(job: &Job, name: &str) -> bool {
    plan::read(&job.workspace.join(name), 4096)
        .is_ok_and(|value| value["nonce"] == job.info["nonce"])
}

fn clear_pointer(parent: &Path, destination: &Path, job: &Job) -> plan::Result<()> {
    let path = parent.join("active");
    if !path.exists() {
        return Ok(());
    }
    let (id, version) = pointer(&path)?;
    plan::require(
        id == job.id && job.info["to_version"] == version,
        "INVALID_JOB",
    )?;
    fs::rename(path, destination.join("finished-active")).map_err(|_| "RENAME_FAILED")?;
    plan::sync(parent)?;
    plan::sync(destination)
}

fn finish(job: &Job, status: &str, code: Option<&str>) -> plan::Result<()> {
    let result = json!({"status": status, "from_version": job.info["from_version"], "to_version": job.info["to_version"],
        "code": code, "job_id": job.id, "program_only": true, "data_migrated": false});
    for directory in [&job.directory, &job.workspace] {
        plan::write(&directory.join("result.json"), &result)?;
        plan::write(
            &directory.parent().unwrap().join("last-result.json"),
            &result,
        )?;
    }
    // A durable complete journal precedes releasing the Pi/business-store gate.
    clear_pointer(job.workspace.parent().unwrap(), &job.workspace, job)?;
    clear_pointer(job.directory.parent().unwrap(), &job.directory, job)
}

fn start_application(job: &Job, trial: bool) -> plan::Result<Child> {
    plan::require(port_free(), "PORT_BUSY")?;
    let mut command = clean(&job.app.join("Contents/MacOS/Agent4Market"));
    if trial {
        command.args([
            "--macos-update-trial",
            &job.id,
            plan::text(&job.info, "nonce")?,
        ]);
    }
    posix_jobs::spawn(&mut command)
}

fn verify_source_side(job: &Job, new: bool) -> plan::Result<()> {
    plan::verify_outcome(&job.root, &job.old, &job.new, new)?;
    plan::require(
        plan::sha(&plan::regular(&job.root, plan::MANIFEST, false)?)?
            == plan::text(
                &job.info,
                if new {
                    "new_manifest_sha256"
                } else {
                    "old_manifest_sha256"
                },
            )?,
        "MANIFEST_CHANGED",
    )
}

fn restore(job: &Job) -> plan::Result<()> {
    plan::require(port_free(), "PORT_BUSY")?;
    let current = json!(bundle_rows(&job.app)?);
    if current == job.new["app_files"] {
        verify_bundle(&job.staged_app, &job.old)?;
        swap_apps(job)?;
    } else {
        plan::require(current == job.old["app_files"], "BUNDLE_MODIFIED")?;
    }
    let journal = job.directory.join("journal.json");
    if journal.exists() {
        plan::rollback(
            &job.root,
            &job.workspace,
            &job.directory,
            &job.old,
            &job.new,
        )?;
    }
    verify_source_side(job, false)?;
    verify_bundle(&job.app, &job.old)
}

fn probe(job: &Job, app: &Path) -> plan::Result<()> {
    let mut command = clean(&app.join("Contents/MacOS/Agent4Market"));
    command.args(["--macos-update-probe", &job.id]);
    checked(command, 120)?;
    plan::require(port_free(), "PORT_BUSY")
}

fn wait_trial(job: &Job, child: &Child) -> plan::Result<()> {
    let deadline = Instant::now() + Duration::from_secs(120);
    let mut ready = None;
    while Instant::now() < deadline {
        plan::require(!posix_jobs::exited(child)?, "STARTUP_TRIAL_FAILED")?;
        if receipt(job, "launch-ready.json") && receipt(job, "core-ready.json") {
            let native = plan::read(&job.workspace.join("launch-ready.json"), 4096)?;
            let core = plan::read(&job.workspace.join("core-ready.json"), 4096)?;
            let core_pid = core["pid"]
                .as_i64()
                .and_then(|v| i32::try_from(v).ok())
                .ok_or("STARTUP_TRIAL_FAILED")?;
            plan::require(
                native["pid"].as_u64() == Some(child.id() as u64)
                    && core_pid > 1
                    && core_pid != child.id() as i32
                    && unsafe { libc::getpgid(core_pid) } == child.id() as i32,
                "STARTUP_TRIAL_FAILED",
            )?;
            let since = ready.get_or_insert_with(Instant::now);
            if since.elapsed() >= Duration::from_secs(3) {
                return Ok(());
            }
        }
        thread::sleep(Duration::from_millis(200));
    }
    Err("STARTUP_TRIAL_FAILED".into())
}

fn run_worker(id: &str, recover: bool) -> plan::Result<()> {
    let parent = unsafe { libc::getppid() };
    plan::require(parent > 1, "PARENT_NOT_RUNNING")?;
    let job = load(id)?;
    let lock_path = plan::regular(&job.directory, "worker.lock", true)?;
    let lock = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .mode(0o600)
        .open(lock_path)
        .map_err(|_| "LOCK_FAILED")?;
    plan::require(
        unsafe { libc::flock(lock.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } == 0,
        "UPDATE_ALREADY_RUNNING",
    )?;
    plan::write(
        &job.directory.join("ready.json"),
        &json!({"pid": std::process::id(), "nonce": job.info["nonce"]}),
    )?;
    // Reparenting observes this actual parent relationship, not a reusable PID.
    let deadline = Instant::now() + Duration::from_secs(120);
    while unsafe { libc::getppid() } == parent && Instant::now() < deadline {
        thread::sleep(Duration::from_millis(100));
    }
    plan::require(
        unsafe { libc::getppid() } != parent && port_free(),
        "PARENT_STILL_RUNNING",
    )?;
    let journal_path = job.directory.join("journal.json");
    if journal_path.exists() && plan::read(&journal_path, 32 * 1024 * 1024)?["phase"] == "complete"
    {
        verify_source_side(&job, true)?;
        verify_bundle(&job.app, &job.new)?;
        finish(&job, "complete", None)?;
        start_application(&job, false)?;
        return Ok(());
    }
    if recover || !receipt(&job, "stopped.json") {
        restore(&job)?;
        finish(&job, "rolled_back", Some("INTERRUPTED"))?;
        start_application(&job, false)?;
        return Ok(());
    }
    let mut trial = None;
    let update = (|| -> plan::Result<()> {
        plan::require(!journal_path.exists(), "INVALID_PHASE")?;
        verify_source_side(&job, false)?;
        verify_bundle(&job.app, &job.old)?;
        verify_bundle(&job.staged_app, &job.new)?;
        plan::backup(
            &job.root,
            &job.workspace.join("stage"),
            &job.workspace,
            &job.directory,
            &job.old,
            &job.new,
        )?;
        plan::apply(
            &job.root,
            &job.workspace.join("stage"),
            &job.directory,
            &job.old,
            &job.new,
        )?;
        probe(&job, &job.staged_app)?;
        swap_apps(&job)?;
        verify_bundle(&job.app, &job.new)?;
        probe(&job, &job.app)?;
        trial = Some(start_application(&job, true)?);
        wait_trial(&job, trial.as_ref().unwrap())?;
        verify_source_side(&job, true)?;
        verify_bundle(&job.app, &job.new)?;
        // Never release the business gate in the deliberately unauthenticated
        // trial. A normal fresh start loads real provider settings AFTER commit.
        posix_jobs::stop(trial.as_mut().ok_or("STARTUP_TRIAL_FAILED")?)?;
        trial = None;
        plan::require(port_free(), "PORT_BUSY")?;
        let mut journal = plan::read(&journal_path, 32 * 1024 * 1024)?;
        journal["phase"] = json!("complete");
        plan::write(&journal_path, &journal)
    })();
    if let Err(code) = update {
        if let Some(mut child) = trial {
            // The trial leader remains waitable. Its whole cohort includes
            // WebView, Python and Pi, even if native startup failed abruptly.
            posix_jobs::stop(&mut child)?;
        }
        restore(&job)?;
        finish(&job, "rolled_back", Some(&code))?;
        start_application(&job, false)?;
    } else {
        finish(&job, "complete", None)?;
        start_application(&job, false)?;
    }
    Ok(())
}

fn launch_worker(job: &Job, recover: bool) -> plan::Result<Child> {
    let mut command = clean(&job.directory.join("native-helper"));
    command.args([
        "--macos-update-worker",
        &job.id,
        if recover { "recover" } else { "apply" },
    ]);
    // Intentionally outside all groups managed by the retiring desktop.
    let mut child = command
        .process_group(0)
        .spawn()
        .map_err(|_| "WORKER_NOT_READY")?;
    let deadline = Instant::now() + Duration::from_secs(30);
    while Instant::now() < deadline {
        if let Some(status) = child.try_wait().map_err(|_| "WORKER_NOT_READY")? {
            return Err(if status.code() == Some(3) {
                "UPDATE_ALREADY_RUNNING"
            } else {
                "WORKER_NOT_READY"
            }
            .into());
        }
        if plan::read(&job.directory.join("ready.json"), 4096).is_ok_and(|value| {
            value["pid"].as_u64() == Some(child.id() as u64) && value["nonce"] == job.info["nonce"]
        }) {
            return Ok(child);
        }
        thread::sleep(Duration::from_millis(100));
    }
    let _ = child.kill();
    let _ = child.wait();
    Err("WORKER_NOT_READY".into())
}

fn control(nonce: &str, route: &str, post: bool) -> plan::Result<String> {
    let mut stream = TcpStream::connect_timeout(
        &SocketAddr::from(([127, 0, 0, 1], WORKBENCH_PORT)),
        Duration::from_secs(1),
    )
    .map_err(|_| "CONTROL_FAILED")?;
    stream
        .set_read_timeout(Some(Duration::from_secs(3)))
        .map_err(|_| "CONTROL_FAILED")?;
    stream
        .set_write_timeout(Some(Duration::from_secs(3)))
        .map_err(|_| "CONTROL_FAILED")?;
    let (method, extra, body) = if post {
        (
            "POST",
            "Content-Type: application/json\r\nContent-Length: 2\r\n",
            "{}",
        )
    } else {
        ("GET", "", "")
    };
    stream.write_all(format!("{method} {route} HTTP/1.1\r\nHost: 127.0.0.1:8765\r\nX-Update-Control: {nonce}\r\n{extra}Connection: close\r\n\r\n{body}").as_bytes()).map_err(|_| "CONTROL_FAILED")?;
    let mut raw = Vec::new();
    stream
        .take(4097)
        .read_to_end(&mut raw)
        .map_err(|_| "CONTROL_FAILED")?;
    plan::require(raw.len() <= 4096, "CONTROL_FAILED")?;
    let text = String::from_utf8(raw).map_err(|_| "CONTROL_FAILED")?;
    let (header, body) = text.split_once("\r\n\r\n").ok_or("CONTROL_FAILED")?;
    plan::require(
        matches!(
            header.lines().next(),
            Some("HTTP/1.0 200 OK" | "HTTP/1.1 200 OK")
        ),
        "CONTROL_FAILED",
    )?;
    Ok(body.into())
}

fn stop_for_update(app: &tauri::AppHandle, nonce: &str) -> plan::Result<()> {
    let state = app.state::<RuntimeChildren>();
    let mut children = state.0.lock().map_err(|_| "PROCESS_LOCK_FAILED")?;
    plan::require(
        children.len() == 2 && children[1].stdin.is_some(),
        "NATIVE_STOP_FAILED",
    )?;
    drop(children[1].stdin.take());
    posix_jobs::wait_leader(&children[1], 25)?;
    posix_jobs::stop(&mut children[1])?;
    control(nonce, "/api/app-updates/native-stop", true)?;
    posix_jobs::wait_leader(&children[0], 15)?;
    posix_jobs::stop(&mut children[0])?;
    children.clear();
    plan::require(posix_jobs::is_empty() && port_free(), "NATIVE_STOP_FAILED")
}

pub fn watch(app: tauri::AppHandle, root: PathBuf, nonce: String) {
    thread::spawn(move || loop {
        thread::sleep(Duration::from_millis(500));
        let Ok(response) = control(&nonce, "/api/app-updates/native", false) else {
            continue;
        };
        if response == "none\n" {
            continue;
        }
        let parts: Vec<_> = response.lines().collect();
        if parts.len() != 2 || !plan::hex(parts[0], 32) {
            continue;
        }
        let Ok(job) = load(parts[0]) else {
            let _ = control(&nonce, "/api/app-updates/native-abort", true);
            continue;
        };
        if job.root != root || job.info["to_version"] != parts[1] {
            continue;
        }
        let _worker = match launch_worker(&job, false) {
            Ok(child) => child,
            Err(_) => {
                let _ = control(&nonce, "/api/app-updates/native-abort", true);
                continue;
            }
        };
        if stop_for_update(&app, &nonce).is_ok() {
            let _ = plan::write(
                &job.workspace.join("stopped.json"),
                &json!({"nonce": job.info["nonce"]}),
            );
        }
        app.exit(0); // Without the durable stop receipt the helper only restores.
        break;
    });
}

pub fn trial_ready(root: &Path) -> plan::Result<()> {
    let Ok(id) = env::var("AGENT4MARKET_UPDATE_PENDING") else {
        return Ok(());
    };
    let job = load(&id)?;
    plan::require(job.root == root, "INVALID_ROOT")?;
    plan::write(
        &job.workspace.join("launch-ready.json"),
        &json!({"pid": std::process::id(), "nonce": job.info["nonce"]}),
    )
}

pub fn recover_on_start() -> plan::Result<bool> {
    env::remove_var("AGENT4MARKET_UPDATE_PENDING");
    let active = match support() {
        Ok(path) => path.join("app-updates/active"),
        Err(_) => return Ok(false),
    };
    if !active.exists() {
        return Ok(false);
    }
    let (id, _) = pointer(&active)?;
    let job = load(&id)?;
    let arguments: Vec<_> = env::args().collect();
    if arguments.len() == 4 && arguments[1] == "--macos-update-trial" && arguments[2] == id {
        plan::require(
            arguments[3] == plan::text(&job.info, "nonce")?
                && plan::read(&job.directory.join("journal.json"), 32 * 1024 * 1024)?["phase"]
                    == "validating"
                && plan::read(&job.root.join("package.json"), 65536)?["version"]
                    == job.info["to_version"],
            "INVALID_TRIAL",
        )?;
        plan::require(
            unsafe { libc::getpgrp() } == std::process::id() as i32,
            "INVALID_TRIAL_GROUP",
        )?;
        posix_jobs::set_trial_cohort();
        let home = job.directory.join("trial-home");
        plan::mkdir(&home)?;
        plan::mkdirs(&home, "tmp")?;
        PROBE_LOG
            .set(home.join("launcher.log"))
            .map_err(|_| "INVALID_TRIAL")?;
        TRIAL_HOME.set(home).map_err(|_| "INVALID_TRIAL")?;
        env::set_var("AGENT4MARKET_UPDATE_PENDING", id);
        return Ok(false);
    }
    match launch_worker(&job, true) {
        Err(code) if code == "UPDATE_ALREADY_RUNNING" => Ok(true),
        result => {
            result?;
            Ok(true)
        }
    }
}

fn isolate_probe(root: PathBuf, home: PathBuf) -> plan::Result<i32> {
    fs::create_dir_all(home.join("tmp")).map_err(|_| "MKDIR_FAILED")?;
    PROBE_ROOT.set(root).map_err(|_| "INVALID_TRIAL")?;
    PROBE_LOG
        .set(home.join("launcher.log"))
        .map_err(|_| "INVALID_TRIAL")?;
    env::set_var("AGENT4MARKET_UPDATE_PROBE", "1");
    env::remove_var("AGENT4MARKET_UPDATE_PENDING");
    env::set_var("HOME", &home);
    env::set_var("TMPDIR", home.join("tmp"));
    env::set_var("PI_CODING_AGENT_DIR", home.join("pi"));
    env::remove_var("CODEX_HOME");
    env::set_var("CLAUDE_CONFIG_DIR", home.join("claude"));
    posix_jobs::set_trial_cohort();
    Ok(self_test())
}

pub fn early_entry() -> Option<i32> {
    let arguments: Vec<_> = env::args().collect();
    match arguments.get(1).map(String::as_str) {
        Some("--macos-build-self-test") if arguments.len() == 3 => {
            // Explicit build-only root, never a CWD search. The probe cannot
            // open business APIs/stores or discover a user's CLI credentials.
            let result = (|| -> plan::Result<i32> {
                let root = PathBuf::from(&arguments[2])
                    .canonicalize()
                    .map_err(|_| "INVALID_ROOT")?;
                plan::require(is_project_root(&root), "INVALID_ROOT")?;
                let home = env::temp_dir().join(format!(
                    "agent4market-build-probe-{}",
                    new_startup_token().map_err(|_| "RANDOM_FAILED")?
                ));
                fs::DirBuilder::new()
                    .mode(0o700)
                    .create(&home)
                    .map_err(|_| "MKDIR_FAILED")?;
                isolate_probe(root, home)
            })();
            Some(result.unwrap_or(2))
        }
        Some("--macos-update-helper-self-test") if arguments.len() == 2 => {
            println!("Agent4Market macOS native recovery v1");
            Some(0)
        }
        Some("--macos-update-worker")
            if arguments.len() == 4 && matches!(arguments[3].as_str(), "apply" | "recover") =>
        {
            let outcome = run_worker(&arguments[2], arguments[3] == "recover");
            Some(match outcome {
                Ok(()) => 0,
                Err(code) if code == "UPDATE_ALREADY_RUNNING" => 3,
                Err(code) => {
                    if let Ok(job) = load(&arguments[2]) {
                        let _ = plan::write(
                            &job.directory.join("worker-error.json"),
                            &json!({"code": code}),
                        );
                    }
                    eprintln!("macOS update stopped: {code}; recovery backups retained.");
                    show_startup_error("macOS 更新未完成，恢复材料已保留。请关闭工作台后重新打开；不要删除更新目录。若仍无法启动，请保留 app-updates 中的 worker-error.json 供排查。");
                    2
                }
            })
        }
        Some("--macos-update-probe") if arguments.len() == 3 => {
            let result = (|| -> plan::Result<i32> {
                let job = load(&arguments[2])?;
                plan::require(
                    plan::read(&job.directory.join("journal.json"), 32 * 1024 * 1024)?["phase"]
                        == "validating",
                    "INVALID_TRIAL",
                )?;
                let home = job.directory.join("probe-home");
                plan::mkdir(&home)?;
                isolate_probe(job.root.clone(), home)
            })();
            Some(result.unwrap_or(2))
        }
        _ => None,
    }
}
