//! Owned macOS process groups. Keep a waitable leader until the final signal,
//! so a recycled PID/PGID is never used to kill an unrelated process.
use std::os::unix::process::CommandExt;
use std::process::{Child, Command};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

static OWNED: Mutex<Vec<u32>> = Mutex::new(Vec::new());
static COHORT: AtomicBool = AtomicBool::new(false);

pub fn set_trial_cohort() {
    COHORT.store(true, Ordering::SeqCst);
}
pub fn trial_cohort() -> bool {
    COHORT.load(Ordering::SeqCst)
}

pub fn spawn(command: &mut Command) -> Result<Child, String> {
    let mut owned = OWNED.lock().map_err(|_| "PROCESS_LOCK_FAILED")?;
    // A trial desktop and ALL of its children share the group whose leader
    // the independent helper keeps waitable. That permits safe whole-trial
    // termination even when the new desktop fails before its cleanup runs.
    let group = if trial_cohort() {
        unsafe { libc::getpgrp() }
    } else {
        0
    };
    let child = command
        .process_group(group)
        .spawn()
        .map_err(|_| "PROCESS_START_FAILED")?;
    owned.push(child.id());
    Ok(child)
}

pub fn exited(child: &Child) -> Result<bool, String> {
    let mut status: libc::siginfo_t = unsafe { std::mem::zeroed() };
    let result = unsafe {
        libc::waitid(
            libc::P_PID,
            child.id(),
            &mut status,
            libc::WEXITED | libc::WNOHANG | libc::WNOWAIT,
        )
    };
    if result != 0 {
        return Err("CHILD_NOT_OWNED".into());
    }
    Ok(status.si_pid == child.id() as i32)
}

pub fn succeeded(child: &Child) -> Result<bool, String> {
    let mut status: libc::siginfo_t = unsafe { std::mem::zeroed() };
    let result = unsafe {
        libc::waitid(
            libc::P_PID,
            child.id(),
            &mut status,
            libc::WEXITED | libc::WNOHANG | libc::WNOWAIT,
        )
    };
    if result != 0 {
        return Err("CHILD_NOT_OWNED".into());
    }
    Ok(status.si_pid == child.id() as i32
        && status.si_code == libc::CLD_EXITED
        && status.si_status == 0)
}

pub fn wait_leader(child: &Child, seconds: u64) -> Result<(), String> {
    let deadline = Instant::now() + Duration::from_secs(seconds);
    while Instant::now() < deadline {
        if exited(child)? {
            return Ok(());
        }
        thread::sleep(Duration::from_millis(100));
    }
    Err("PROCESS_STILL_RUNNING".into())
}

fn group_has_no_descendants(child: &Child) -> Result<bool, String> {
    // Called only while an unreaped, exited leader pins this owned PGID.
    if !exited(child)? {
        return Ok(false);
    }
    let mut pids = [0_i32; 16384];
    unsafe {
        *libc::__error() = 0;
    }
    let count = unsafe {
        libc::proc_listpgrppids(
            child.id() as i32,
            pids.as_mut_ptr().cast(),
            std::mem::size_of_val(&pids) as i32,
        )
    };
    if count < 0
        || count as usize >= pids.len()
        || (count == 0 && std::io::Error::last_os_error().raw_os_error() != Some(0))
    {
        return Err("PROCESS_GROUP_QUERY_FAILED".into());
    }
    Ok(pids[..count as usize]
        .iter()
        .all(|pid| *pid == 0 || *pid == child.id() as i32))
}

fn signal_owned(child: &Child, target: i32, signal: i32) -> Result<(), String> {
    exited(child)?; // Never signal a consumed/recycled child ID.
    if unsafe { libc::kill(target, signal) } == 0 {
        return Ok(());
    }
    let error = std::io::Error::last_os_error().raw_os_error();
    // XNU killpg may report EPERM, not ESRCH, for a zombie-only group.
    // Accept neither error on its own: require an owned, exited leader and
    // an exact group enumeration proving no remaining descendant exists.
    if target < -1
        && matches!(error, Some(libc::ESRCH | libc::EPERM))
        && group_has_no_descendants(child)?
    {
        return Ok(());
    }
    Err("PROCESS_STOP_FAILED".into())
}

pub fn stop(child: &mut Child) -> Result<(), String> {
    let mut owned = OWNED.lock().map_err(|_| "PROCESS_LOCK_FAILED")?;
    let index = owned
        .iter()
        .position(|pid| *pid == child.id())
        .ok_or("CHILD_NOT_OWNED")?;
    // waitid does NOT reap. ECHILD refuses all signals if another code path
    // already consumed this child (and the numeric ID might have recycled).
    exited(child)?;
    let pid = i32::try_from(child.id()).map_err(|_| "INVALID_PROCESS")?;
    let expected_group = if trial_cohort() {
        unsafe { libc::getpgrp() }
    } else {
        pid
    };
    let actual_group = unsafe { libc::getpgid(pid) };
    if actual_group != expected_group {
        // Darwin getpgid uses proc_find and excludes zombies. WNOWAIT still
        // proves this is OUR unreaped child and pins its original group ID.
        let absent = actual_group == -1
            && std::io::Error::last_os_error().raw_os_error() == Some(libc::ESRCH);
        if !absent || !exited(child)? {
            return Err("PROCESS_GROUP_CHANGED".into());
        }
    }
    let signal_target = if trial_cohort() { pid } else { -pid };
    signal_owned(child, signal_target, libc::SIGTERM)?;
    let _ = wait_leader(child, 5);
    // Even an exited leader remains waitable and pins the group ID here.
    exited(child)?;
    signal_owned(child, signal_target, libc::SIGKILL)?;
    wait_leader(child, 10)?;
    let deadline = Instant::now() + Duration::from_secs(10);
    if !trial_cohort() {
        while !group_has_no_descendants(child)? {
            if Instant::now() >= deadline {
                return Err("PROCESS_GROUP_STILL_RUNNING".into());
            }
            thread::sleep(Duration::from_millis(100));
        }
    }
    // All group queries/signals precede reaping. Never query a recycled PGID.
    child.wait().map_err(|_| "PROCESS_WAIT_FAILED")?;
    owned.remove(index);
    Ok(())
}

pub fn is_empty() -> bool {
    OWNED.lock().is_ok_and(|owned| owned.is_empty())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn exited_leader_is_reaped_and_its_surviving_group_is_stopped() {
        for script in ["exit 0", "sleep 30 & exit 0"] {
            let mut command = Command::new("/bin/sh");
            command.args(["-c", script]);
            let mut child = spawn(&mut command).unwrap();
            wait_leader(&child, 5).unwrap();
            assert!(succeeded(&child).unwrap());
            stop(&mut child).unwrap();
            assert!(stop(&mut child).is_err()); // Consumed ownership cannot signal again.
        }
        assert!(is_empty());
    }
}
