//! Filesystem-only program transaction. No Python, Node, shell or business data.
//! This is also compiled on Windows for the focused recovery tests below.
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};

pub type Result<T> = std::result::Result<T, String>;
pub const MANIFEST: &str = "runtime/macos-install-manifest.json";
pub const PROTOCOL: &str = "agent_platform/macos_update_protocol.json";

#[cfg(target_os = "macos")]
pub fn macos_acl(path: &Path, mode: u32) -> Result<()> {
    use std::ffi::{c_char, c_int, c_void, CString};
    use std::os::unix::ffi::OsStrExt;
    extern "C" {
        fn acl_get_file(path: *const c_char, kind: c_int) -> *mut c_void;
        fn acl_get_entry(acl: *mut c_void, index: c_int, entry: *mut *mut c_void) -> c_int;
        fn acl_get_tag_type(entry: *mut c_void, tag: *mut c_int) -> c_int;
        fn acl_get_permset_mask_np(entry: *mut c_void, mask: *mut u64) -> c_int;
        fn acl_free(acl: *mut c_void) -> c_int;
    }
    let path = CString::new(path.as_os_str().as_bytes()).map_err(|_| "INVALID_PATH")?;
    unsafe {
        *libc::__error() = 0;
    }
    let acl = unsafe { acl_get_file(path.as_ptr(), 0x100) };
    if acl.is_null() {
        return require(
            matches!(
                std::io::Error::last_os_error().raw_os_error(),
                Some(0 | libc::ENOENT)
            ),
            "UNSAFE_ACL",
        );
    }
    let result = (|| -> Result<()> {
        for index in 0..=128 {
            let mut entry = std::ptr::null_mut();
            if unsafe { acl_get_entry(acl, index, &mut entry) } != 0 {
                return require(
                    std::io::Error::last_os_error().raw_os_error() == Some(libc::EINVAL),
                    "UNSAFE_ACL",
                );
            }
            require(index < 128, "UNSAFE_ACL")?;
            let mut tag = 0;
            let mut mask = 0_u64;
            require(
                unsafe { acl_get_tag_type(entry, &mut tag) } == 0
                    && unsafe { acl_get_permset_mask_np(entry, &mut mask) } == 0
                    && matches!(tag, 1 | 2),
                "UNSAFE_ACL",
            )?;
            if tag == 1 {
                let writes = (1_u64 << 2)
                    | (1 << 4)
                    | (1 << 5)
                    | (1 << 6)
                    | (1 << 8)
                    | (1 << 10)
                    | (1 << 12)
                    | (1 << 13);
                require(
                    mask & writes == 0 && (mode & 0o077 != 0 || mask & !(1 << 20) == 0),
                    "UNSAFE_ACL",
                )?;
            }
        }
        Err("UNSAFE_ACL".into())
    })();
    unsafe {
        acl_free(acl);
    }
    result
}

pub fn require(value: bool, code: &str) -> Result<()> {
    if value {
        Ok(())
    } else {
        Err(code.into())
    }
}

pub fn hex(value: &str, length: usize) -> bool {
    value.len() == length
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

pub fn version(value: &str) -> Option<Vec<u32>> {
    let parts: Vec<_> = value.split('.').collect();
    if parts.len() != 3
        || parts.iter().any(|p| {
            p.is_empty()
                || p.len() > 9
                || (p.len() > 1 && p.starts_with('0'))
                || !p.bytes().all(|b| b.is_ascii_digit())
        })
    {
        return None;
    }
    parts.iter().map(|part| part.parse().ok()).collect()
}

pub fn text<'a>(value: &'a Value, field: &str) -> Result<&'a str> {
    value[field]
        .as_str()
        .ok_or_else(|| "INVALID_MANIFEST".into())
}

pub fn relative(name: &str) -> Result<()> {
    require(
        !name.is_empty()
            && name.len() <= 4096
            && !name.contains(['\\', ':'])
            && !name.chars().any(|c| c.is_control())
            && name.split('/').all(|part| {
                !part.is_empty() && part != "." && part != ".." && !part.ends_with(['.', ' '])
            }),
        "INVALID_PATH",
    )
}

pub fn program(name: &str) -> Result<()> {
    relative(name)?;
    let head = name.split('/').next().unwrap();
    let directories = [
        "agent_platform",
        "profiles",
        "vertical_plugins",
        "pi",
        "plugin",
        "ui",
        "scripts",
        "library",
    ];
    let files = [
        "AGENTS.md",
        "README.md",
        "LICENSE",
        "package.json",
        "pnpm-lock.yaml",
        "tsconfig.json",
        "requirements.txt",
        "requirements-wxdecipher.txt",
        "INSTALL-NOTES.md",
    ];
    let lower = name.to_ascii_lowercase();
    require(
        (name.contains('/') && directories.contains(&head) || files.contains(&name))
            && lower != "library/templates/company"
            && !lower.starts_with("library/templates/company/")
            && !name
                .split('/')
                .any(|part| part.starts_with('.') || part.contains('%')),
        "USER_DATA_REFUSED",
    )
}

pub fn directory(path: &Path, private: bool) -> Result<()> {
    for ancestor in path.ancestors() {
        let metadata = fs::symlink_metadata(ancestor).map_err(|_| "MISSING_DIRECTORY")?;
        #[cfg(target_os = "macos")]
        {
            use std::os::unix::fs::MetadataExt;
            macos_acl(ancestor, metadata.mode())?;
        }
        require(
            metadata.is_dir() && !metadata.file_type().is_symlink(),
            "LINK_REFUSED",
        )?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::MetadataExt;
            let uid = unsafe { libc::getuid() };
            require(
                (metadata.uid() == 0 || metadata.uid() == uid) && metadata.mode() & 0o022 == 0,
                "UNSAFE_OWNER_OR_MODE",
            )?;
            if ancestor == path {
                require(
                    metadata.uid() == uid && (!private || metadata.mode() & 0o077 == 0),
                    "PRIVATE_DIRECTORY_REQUIRED",
                )?;
            }
        }
    }
    #[cfg(not(unix))]
    let _ = private;
    Ok(())
}

pub fn regular(root: &Path, name: &str, missing: bool) -> Result<PathBuf> {
    relative(name)?;
    let path = root.join(name);
    let parts: Vec<_> = name.split('/').collect();
    let mut current = root.to_path_buf();
    for (index, part) in parts.iter().enumerate() {
        current.push(part);
        let metadata = match fs::symlink_metadata(&current) {
            Ok(value) => value,
            Err(error) if missing && error.kind() == std::io::ErrorKind::NotFound => continue,
            Err(_) => return Err("MISSING_FILE".into()),
        };
        #[cfg(target_os = "macos")]
        {
            use std::os::unix::fs::MetadataExt;
            macos_acl(&current, metadata.mode())?;
        }
        require(!metadata.file_type().is_symlink(), "LINK_REFUSED")?;
        require(
            if index + 1 == parts.len() {
                metadata.is_file()
            } else {
                metadata.is_dir()
            },
            "NON_REGULAR_FILE",
        )?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::MetadataExt;
            require(
                metadata.uid() == unsafe { libc::getuid() }
                    && metadata.mode() & 0o022 == 0
                    && (index + 1 != parts.len() || metadata.nlink() == 1),
                "UNSAFE_OWNER_OR_MODE",
            )?;
        }
    }
    Ok(path)
}

pub fn sha(path: &Path) -> Result<String> {
    let mut file = File::open(path).map_err(|_| "READ_FAILED")?;
    let mut hash = Sha256::new();
    let mut block = [0_u8; 65536];
    loop {
        let count = file.read(&mut block).map_err(|_| "READ_FAILED")?;
        if count == 0 {
            break;
        }
        hash.update(&block[..count]);
    }
    Ok(format!("{:x}", hash.finalize()))
}

pub fn read(path: &Path, limit: u64) -> Result<Value> {
    regular(
        path.parent().ok_or("INVALID_PATH")?,
        path.file_name()
            .and_then(|v| v.to_str())
            .ok_or("INVALID_PATH")?,
        false,
    )?;
    require(
        path.metadata().map_err(|_| "READ_FAILED")?.len() <= limit,
        "FILE_TOO_LARGE",
    )?;
    serde_json::from_slice(&fs::read(path).map_err(|_| "READ_FAILED")?)
        .map_err(|_| "INVALID_JSON".into())
}

pub fn sync(path: &Path) -> Result<()> {
    #[cfg(unix)]
    File::open(path)
        .and_then(|file| file.sync_all())
        .map_err(|_| "FSYNC_FAILED")?;
    #[cfg(not(unix))]
    let _ = path;
    Ok(())
}

pub fn mkdir(path: &Path) -> Result<()> {
    if !path.exists() {
        let parent = path.parent().ok_or("INVALID_PATH")?;
        directory(parent, false)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::DirBuilderExt;
            fs::DirBuilder::new()
                .mode(0o700)
                .create(path)
                .map_err(|_| "MKDIR_FAILED")?;
        }
        #[cfg(not(unix))]
        fs::create_dir(path).map_err(|_| "MKDIR_FAILED")?;
        sync(parent)?;
    }
    directory(path, false)
}

pub fn mkdirs(root: &Path, name: &str) -> Result<()> {
    relative(name)?;
    let mut current = root.to_path_buf();
    for part in name.split('/') {
        current.push(part);
        mkdir(&current)?;
    }
    Ok(())
}

pub fn write(path: &Path, value: &Value) -> Result<()> {
    let bytes = serde_json::to_vec_pretty(value).map_err(|_| "INVALID_JSON")?;
    require(bytes.len() <= 32 * 1024 * 1024, "FILE_TOO_LARGE")?;
    atomic(path, &bytes, 0o600)
}

pub fn atomic(path: &Path, bytes: &[u8], mode: u32) -> Result<()> {
    atomic_with(path, mode, |file| {
        file.write_all(bytes).map_err(|_| "WRITE_FAILED".into())
    })
}

fn atomic_with(path: &Path, mode: u32, fill: impl FnOnce(&mut File) -> Result<()>) -> Result<()> {
    let parent = path.parent().ok_or("INVALID_PATH")?;
    directory(parent, false)?;
    regular(
        parent,
        path.file_name()
            .and_then(|v| v.to_str())
            .ok_or("INVALID_PATH")?,
        true,
    )?;
    let mut random = [0_u8; 16];
    getrandom::fill(&mut random).map_err(|_| "RANDOM_FAILED")?;
    let suffix: String = random.iter().map(|b| format!("{b:02x}")).collect();
    let temporary = parent.join(format!(".update-{suffix}"));
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(mode);
    }
    #[cfg(not(unix))]
    let _ = mode;
    let mut file = options.open(&temporary).map_err(|_| "WRITE_FAILED")?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        file.set_permissions(fs::Permissions::from_mode(mode))
            .map_err(|_| "WRITE_FAILED")?;
    }
    let filled = fill(&mut file).and_then(|_| file.sync_all().map_err(|_| "WRITE_FAILED".into()));
    drop(file);
    if let Err(error) = filled {
        // Exact generated temporary file only; never a manifest/user target.
        let _ = fs::remove_file(&temporary);
        let _ = sync(parent);
        return Err(error);
    }
    fs::rename(temporary, path).map_err(|_| "RENAME_FAILED")?;
    sync(parent)
}

pub fn rows(manifest: &Value) -> Result<BTreeMap<String, Value>> {
    require(
        manifest["format"].as_u64() == Some(1)
            && manifest["platform"] == "macos-universal"
            && version(text(manifest, "version")?).is_some()
            && hex(text(manifest, "dependency_contract")?, 64),
        "INVALID_MANIFEST",
    )?;
    let values = manifest["files"].as_array().ok_or("INVALID_MANIFEST")?;
    require(
        !values.is_empty() && values.len() <= 100000,
        "INVALID_MANIFEST",
    )?;
    let mut result = BTreeMap::new();
    let mut seen = BTreeSet::new();
    let mut total = 0_u64;
    for row in values {
        let name = text(row, "path")?;
        program(name)?;
        require(
            seen.insert(name.to_lowercase())
                && hex(text(row, "sha256")?, 64)
                && matches!(row["mode"].as_u64(), Some(0o644 | 0o755)),
            "INVALID_MANIFEST",
        )?;
        let size = row["bytes"].as_u64().ok_or("INVALID_MANIFEST")?;
        require(size <= 1024 * 1024 * 1024, "PAYLOAD_TOO_LARGE")?;
        total = total.checked_add(size).ok_or("PAYLOAD_TOO_LARGE")?;
        require(total <= 4 * 1024 * 1024 * 1024, "PAYLOAD_TOO_LARGE")?;
        result.insert(name.into(), row.clone());
    }
    require(
        [
            "package.json",
            PROTOCOL,
            "ui/server.py",
            "scripts/start-macos.sh",
        ]
        .iter()
        .all(|name| result.contains_key(*name)),
        "UPDATE_PROTOCOL_UNSUPPORTED",
    )?;
    for name in result.keys() {
        for parent in Path::new(name).ancestors().skip(1) {
            require(
                !seen.contains(&parent.to_string_lossy().to_lowercase()),
                "PATH_COLLISION",
            )?;
        }
    }
    Ok(result)
}

pub fn matches(root: &Path, name: &str, row: Option<&Value>) -> Result<bool> {
    let path = regular(root, name, true)?;
    let Some(row) = row else {
        return Ok(!path.exists());
    };
    if !path.is_file()
        || path.metadata().map_err(|_| "READ_FAILED")?.len()
            != row["bytes"].as_u64().ok_or("INVALID_MANIFEST")?
    {
        return Ok(false);
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if path
            .metadata()
            .map_err(|_| "READ_FAILED")?
            .permissions()
            .mode()
            & 0o777
            != row["mode"].as_u64().ok_or("INVALID_MANIFEST")? as u32
        {
            return Ok(false);
        }
    }
    Ok(sha(&path)? == text(row, "sha256")?)
}

pub fn verify(root: &Path, manifest: &Value) -> Result<()> {
    directory(root, false)?;
    for (name, row) in rows(manifest)? {
        require(matches(root, &name, Some(&row))?, "PROGRAM_MODIFIED")?;
    }
    Ok(())
}

pub fn operations(old: &Value, new: &Value) -> Result<Vec<Value>> {
    let before = rows(old)?;
    let after = rows(new)?;
    require(
        version(text(new, "version")?) > version(text(old, "version")?)
            && old["dependency_contract"] == new["dependency_contract"]
            && before.get(PROTOCOL) == after.get(PROTOCOL),
        "DEPENDENCIES_CHANGED",
    )?;
    let names: BTreeSet<_> = before.keys().chain(after.keys()).collect();
    Ok(names
        .into_iter()
        .filter(|name| before.get(*name) != after.get(*name))
        .map(|name| json!({"path": name, "old": before.get(name), "new": after.get(name)}))
        .collect())
}

pub fn verify_outcome(root: &Path, old: &Value, new: &Value, target_new: bool) -> Result<()> {
    verify(root, if target_new { new } else { old })?;
    for op in operations(old, new)? {
        let row = &op[if target_new { "new" } else { "old" }];
        require(
            matches(root, text(&op, "path")?, (!row.is_null()).then_some(row))?,
            "PROGRAM_MODIFIED",
        )?;
    }
    Ok(())
}

pub fn copy(root: &Path, source: &Path, name: &str, row: &Value) -> Result<()> {
    require(matches(source, name, Some(row))?, "PROGRAM_MODIFIED")?;
    if let Some((parent, _)) = name.rsplit_once('/') {
        mkdirs(root, parent)?;
    }
    let target = regular(root, name, true)?;
    let mut input = File::open(regular(source, name, false)?).map_err(|_| "READ_FAILED")?;
    atomic_with(
        &target,
        row["mode"].as_u64().ok_or("INVALID_MANIFEST")? as u32,
        |output| {
            let mut block = [0_u8; 65536];
            let mut hash = Sha256::new();
            let mut total = 0_u64;
            let expected = row["bytes"].as_u64().ok_or("INVALID_MANIFEST")?;
            loop {
                let count = input.read(&mut block).map_err(|_| "READ_FAILED")?;
                if count == 0 {
                    break;
                }
                total += count as u64;
                require(total <= expected, "COPY_HASH_MISMATCH")?;
                hash.update(&block[..count]);
                output
                    .write_all(&block[..count])
                    .map_err(|_| "WRITE_FAILED")?;
            }
            require(
                total == expected && format!("{:x}", hash.finalize()) == text(row, "sha256")?,
                "COPY_HASH_MISMATCH",
            )
        },
    )?;
    require(matches(root, name, Some(row))?, "COPY_HASH_MISMATCH")
}

pub fn backup(
    root: &Path,
    stage: &Path,
    workspace: &Path,
    job: &Path,
    old: &Value,
    new: &Value,
) -> Result<()> {
    verify(root, old)?;
    verify(stage, new)?;
    let plan = operations(old, new)?;
    let backup = workspace.join("backup");
    mkdir(&backup)?;
    for op in &plan {
        let name = text(op, "path")?;
        require(
            matches(root, name, (!op["old"].is_null()).then_some(&op["old"]))?,
            "PROGRAM_MODIFIED",
        )?;
        if !op["old"].is_null() {
            copy(&backup, root, name, &op["old"])?;
        }
    }
    write(
        &job.join("journal.json"),
        &json!({"phase": "backed_up", "operations": plan}),
    )
}

pub fn apply(root: &Path, stage: &Path, job: &Path, old: &Value, new: &Value) -> Result<()> {
    let mut journal = read(&job.join("journal.json"), 32 * 1024 * 1024)?;
    let plan = operations(old, new)?;
    require(
        journal["phase"] == "backed_up" && journal["operations"] == json!(plan),
        "PLAN_CHANGED",
    )?;
    journal["phase"] = json!("applying");
    write(&job.join("journal.json"), &journal)?;
    for op in &plan {
        let name = text(op, "path")?;
        require(
            matches(root, name, (!op["old"].is_null()).then_some(&op["old"]))?,
            "PROGRAM_MODIFIED",
        )?;
        if op["new"].is_null() {
            fs::remove_file(regular(root, name, false)?).map_err(|_| "REMOVE_PROGRAM_FAILED")?;
            sync(root.join(name).parent().ok_or("INVALID_PATH")?)?;
        } else {
            copy(root, stage, name, &op["new"])?;
        }
    }
    atomic(
        &regular(root, MANIFEST, false)?,
        &fs::read(job.join("new-manifest.json")).map_err(|_| "READ_FAILED")?,
        0o600,
    )?;
    verify_outcome(root, old, new, true)?;
    journal["phase"] = json!("validating");
    write(&job.join("journal.json"), &journal)
}

pub fn rollback(root: &Path, workspace: &Path, job: &Path, old: &Value, new: &Value) -> Result<()> {
    let mut journal = read(&job.join("journal.json"), 32 * 1024 * 1024)?;
    let plan = operations(old, new)?;
    require(
        journal["operations"] == json!(plan)
            && matches!(
                journal["phase"].as_str(),
                Some("backed_up" | "applying" | "validating" | "rolled_back")
            ),
        "PLAN_CHANGED",
    )?;
    let installed = sha(&regular(root, MANIFEST, false)?)?;
    require(
        installed == sha(&job.join("old-manifest.json"))?
            || installed == sha(&job.join("new-manifest.json"))?,
        "MANIFEST_CHANGED",
    )?;
    // Preflight ALL paths before restoring any one of them.
    for op in &plan {
        let name = text(op, "path")?;
        require(
            matches(root, name, (!op["old"].is_null()).then_some(&op["old"]))?
                || matches(root, name, (!op["new"].is_null()).then_some(&op["new"]))?,
            "PROGRAM_MODIFIED",
        )?;
    }
    for op in plan.iter().rev() {
        let name = text(op, "path")?;
        if matches(root, name, (!op["old"].is_null()).then_some(&op["old"]))? {
            continue;
        }
        if op["old"].is_null() {
            fs::remove_file(regular(root, name, false)?).map_err(|_| "REMOVE_PROGRAM_FAILED")?;
            sync(root.join(name).parent().ok_or("INVALID_PATH")?)?;
        } else {
            copy(root, &workspace.join("backup"), name, &op["old"])?;
        }
    }
    atomic(
        &regular(root, MANIFEST, false)?,
        &fs::read(job.join("old-manifest.json")).map_err(|_| "READ_FAILED")?,
        0o600,
    )?;
    verify_outcome(root, old, new, false)?;
    journal["phase"] = json!("rolled_back");
    write(&job.join("journal.json"), &journal)
}

#[cfg(test)]
mod tests {
    use super::*;
    struct Fixture {
        path: PathBuf,
    }
    impl Fixture {
        fn new() -> Self {
            let mut bytes = [0_u8; 16];
            getrandom::fill(&mut bytes).unwrap();
            let suffix: String = bytes.iter().map(|b| format!("{b:02x}")).collect();
            #[cfg(unix)]
            let base = PathBuf::from(std::env::var_os("HOME").unwrap());
            #[cfg(not(unix))]
            let base = std::env::temp_dir();
            let path = base.join(format!("Agent4Market-plan-test-{suffix}"));
            mkdir(&path).unwrap();
            Self { path }
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            assert!(
                self.path.is_absolute()
                    && self
                        .path
                        .file_name()
                        .unwrap()
                        .to_string_lossy()
                        .starts_with("Agent4Market-plan-test-")
            );
            let _ = fs::remove_dir_all(&self.path); // This newly-created synthetic fixture only.
        }
    }
    fn synthetic(root: &Path, version: &str) -> Value {
        mkdir(root).unwrap();
        let mut files = Vec::new();
        for (name, bytes) in [
            ("package.json", format!("{{\"version\":\"{version}\"}}")),
            (PROTOCOL, "constant protocol".into()),
            ("ui/server.py", format!("program {version}")),
            ("scripts/start-macos.sh", "constant launcher".into()),
            (
                if version == "1.0.0" {
                    "ui/retired.js"
                } else {
                    "ui/new.js"
                },
                "synthetic version-specific file".into(),
            ),
        ] {
            if let Some((parent, _)) = name.rsplit_once('/') {
                mkdirs(root, parent).unwrap();
            }
            atomic(&root.join(name), bytes.as_bytes(), 0o644).unwrap();
            files.push(json!({"path": name, "bytes": bytes.len(), "sha256": sha(&root.join(name)).unwrap(), "mode": 0o644}));
        }
        json!({"format": 1, "platform": "macos-universal", "version": version, "dependency_contract": "a".repeat(64), "files": files})
    }
    #[test]
    fn user_data_dependencies_and_ambiguous_names_are_never_program_targets() {
        for name in [
            "../ui/server.py",
            "data/a.json",
            ".pi/config.json",
            "outputs/a.pdf",
            "node_modules/a.js",
            ".venv/bin/python",
            "library/templates/company/brand.png",
            "ui/a/../x",
            "ui\\x",
            "/ui/x",
        ] {
            assert!(program(name).is_err(), "{name}");
        }
        assert!(program("ui/server.py").is_ok());
        assert!(version("0.20.4").is_some());
        assert!(version("01.20.4").is_none());
    }
    #[test]
    fn real_file_apply_and_idempotent_rollback_preserve_business_canary() {
        let fixture = Fixture::new();
        let root = fixture.path.join("root");
        let stage = fixture.path.join("stage");
        let workspace = fixture.path.join("workspace");
        let job = fixture.path.join("job");
        mkdir(&workspace).unwrap();
        mkdir(&job).unwrap();
        let old = synthetic(&root, "1.0.0");
        let new = synthetic(&stage, "1.1.0");
        mkdirs(&root, "runtime").unwrap();
        mkdirs(&root, "data").unwrap();
        atomic(&root.join("data/canary"), b"synthetic business data", 0o600).unwrap();
        write(&root.join(MANIFEST), &old).unwrap();
        write(&job.join("old-manifest.json"), &old).unwrap();
        write(&job.join("new-manifest.json"), &new).unwrap();
        backup(&root, &stage, &workspace, &job, &old, &new).unwrap();
        apply(&root, &stage, &job, &old, &new).unwrap();
        verify_outcome(&root, &old, &new, true).unwrap();
        assert!(!root.join("ui/retired.js").exists());
        atomic(
            &root.join("ui/retired.js"),
            b"synthetic version-specific file",
            0o644,
        )
        .unwrap();
        assert!(verify_outcome(&root, &old, &new, true).is_err());
        rollback(&root, &workspace, &job, &old, &new).unwrap();
        rollback(&root, &workspace, &job, &old, &new).unwrap();
        verify(&root, &old).unwrap();
        assert!(!root.join("ui/new.js").exists());
        assert_eq!(
            fs::read(root.join("data/canary")).unwrap(),
            b"synthetic business data"
        );
    }
    #[test]
    fn rollback_preflights_local_changes_before_any_replacement() {
        let fixture = Fixture::new();
        let root = fixture.path.join("root");
        let stage = fixture.path.join("stage");
        let workspace = fixture.path.join("workspace");
        let job = fixture.path.join("job");
        mkdir(&workspace).unwrap();
        mkdir(&job).unwrap();
        let old = synthetic(&root, "1.0.0");
        let new = synthetic(&stage, "1.1.0");
        mkdirs(&root, "runtime").unwrap();
        write(&root.join(MANIFEST), &old).unwrap();
        write(&job.join("old-manifest.json"), &old).unwrap();
        write(&job.join("new-manifest.json"), &new).unwrap();
        backup(&root, &stage, &workspace, &job, &old, &new).unwrap();
        apply(&root, &stage, &job, &old, &new).unwrap();
        atomic(&root.join("ui/server.py"), b"user edit", 0o644).unwrap();
        let package = fs::read(root.join("package.json")).unwrap();
        assert!(rollback(&root, &workspace, &job, &old, &new).is_err());
        assert_eq!(fs::read(root.join("package.json")).unwrap(), package);
        assert_eq!(fs::read(root.join("ui/server.py")).unwrap(), b"user edit");
    }
}
