//! Own backend/core descendants before their first instruction, including
//! grandchildren whose short-lived parents disappear between process scans.
use super::*;
use std::os::windows::io::AsRawHandle;
use std::sync::OnceLock;
use windows_sys::Win32::Foundation::{CloseHandle, HANDLE, INVALID_HANDLE_VALUE};
use windows_sys::Win32::System::Diagnostics::ToolHelp::*;
use windows_sys::Win32::System::JobObjects::*;
use windows_sys::Win32::System::Threading::{OpenThread, ResumeThread, CREATE_SUSPENDED, THREAD_SUSPEND_RESUME};

struct OwnedJob { handle: isize, pid: u32 }
impl OwnedJob {
    fn active(&self) -> Result<u32, String> {
        let mut info: JOBOBJECT_BASIC_ACCOUNTING_INFORMATION = unsafe { std::mem::zeroed() };
        if unsafe { QueryInformationJobObject(self.handle as HANDLE, JobObjectBasicAccountingInformation,
            &mut info as *mut _ as _, std::mem::size_of_val(&info) as u32, std::ptr::null_mut()) } == 0 {
            return Err("无法核验运行时进程组".into());
        }
        Ok(info.ActiveProcesses)
    }
}
impl Drop for OwnedJob {
    fn drop(&mut self) { unsafe { CloseHandle(self.handle as HANDLE); } }
}
fn jobs() -> &'static Mutex<Vec<OwnedJob>> {
    static JOBS: OnceLock<Mutex<Vec<OwnedJob>>> = OnceLock::new();
    JOBS.get_or_init(|| Mutex::new(Vec::new()))
}

fn resume_initial_thread(pid: u32) -> Result<(), String> {
    let snapshot = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0) };
    if snapshot == INVALID_HANDLE_VALUE { return Err("无法启动受控运行时线程".into()); }
    let mut entry: THREADENTRY32 = unsafe { std::mem::zeroed() };
    entry.dwSize = std::mem::size_of_val(&entry) as u32;
    let mut matches = Vec::new();
    let mut good = unsafe { Thread32First(snapshot, &mut entry) };
    while good != 0 {
        if entry.th32OwnerProcessID == pid { matches.push(entry.th32ThreadID); }
        good = unsafe { Thread32Next(snapshot, &mut entry) };
    }
    unsafe { CloseHandle(snapshot); }
    if matches.len() != 1 { return Err("受控运行时启动线程不唯一".into()); }
    let thread = unsafe { OpenThread(THREAD_SUSPEND_RESUME, 0, matches[0]) };
    if thread.is_null() { return Err("无法打开受控运行时线程".into()); }
    let resumed = unsafe { ResumeThread(thread) };
    unsafe { CloseHandle(thread); }
    if resumed != 1 { return Err("受控运行时启动状态无效".into()); }
    Ok(())
}

pub fn spawn(command: &mut Command, flags: u32) -> Result<Child, String> {
    let handle = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
    if handle.is_null() { return Err("无法创建运行时进程组".into()); }
    let mut job = OwnedJob { handle: handle as isize, pid: 0 };
    let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { std::mem::zeroed() };
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    if unsafe { SetInformationJobObject(handle, JobObjectExtendedLimitInformation,
        &limits as *const _ as _, std::mem::size_of_val(&limits) as u32) } == 0 {
        return Err("无法设置运行时生命周期保护".into());
    }
    let mut child = command.creation_flags(flags | CREATE_SUSPENDED).spawn().map_err(|_| "受控运行时无法启动")?;
    job.pid = child.id();
    if unsafe { AssignProcessToJobObject(handle, child.as_raw_handle() as HANDLE) } == 0 {
        let _ = child.kill(); let _ = child.wait();
        return Err("无法托管运行时；未使用无保护启动".into());
    }
    if let Err(error) = resume_initial_thread(child.id()) {
        drop(job); let _ = child.wait(); return Err(error);
    }
    let mut owned = match jobs().lock() {
        Ok(owned) => owned,
        Err(_) => { drop(job); let _ = child.wait(); return Err("运行时进程组锁不可用".into()); }
    };
    owned.push(job);
    Ok(child)
}

pub fn stop(pid: u32) -> bool {
    let Ok(owned) = jobs().lock() else { return false; };
    let Some(job) = owned.iter().find(|job| job.pid == pid) else { return false; };
    unsafe { TerminateJobObject(job.handle as HANDLE, 2); }
    true
}

pub fn wait_empty(seconds: u64) -> Result<(), String> {
    let deadline = Instant::now() + Duration::from_secs(seconds);
    loop {
        let owned = jobs().lock().map_err(|_| "运行时进程组锁不可用")?;
        let counts: Result<Vec<_>, _> = owned.iter().map(OwnedJob::active).collect();
        if counts?.iter().all(|count| *count == 0) { return Ok(()); }
        drop(owned);
        if Instant::now() >= deadline { return Err("运行时子进程尚未退出".into()); }
        thread::sleep(Duration::from_millis(100));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn job_holds_grandchild_after_short_lived_bridge_exits() {
        // Entirely synthetic hidden PowerShell tree. The bridge exits before
        // the parent; the long-lived grandchild must remain tracked by the job.
        let mut command = Command::new("powershell.exe");
        command.args(["-NoProfile", "-NonInteractive", "-Command",
            "Start-Process powershell.exe -WindowStyle Hidden -ArgumentList '-NoProfile -NonInteractive -Command Start-Sleep -Seconds 30' | Out-Null"])
            .stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
        let mut child = spawn(&mut command, CREATE_NO_WINDOW).unwrap();
        let pid = child.id();
        let _ = child.wait().unwrap();
        assert!(wait_empty(0).is_err(), "orphan descendant must remain in the kernel job");
        assert!(stop(pid));
        wait_empty(5).unwrap();
    }
}
