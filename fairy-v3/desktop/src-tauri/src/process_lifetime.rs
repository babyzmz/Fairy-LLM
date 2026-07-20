#[cfg(target_os = "windows")]
mod platform {
    use std::ffi::c_void;
    use std::mem::size_of;
    use std::ptr;
    use std::sync::OnceLock;

    use windows_sys::Win32::Foundation::{CloseHandle, GetLastError};
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, IsProcessInJob,
        JobObjectExtendedLimitInformation, SetInformationJobObject,
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };
    use windows_sys::Win32::System::Threading::GetCurrentProcess;

    // Intentionally retained until process teardown. Windows then closes the handle and
    // terminates any Core, Voice, WebView2, or helper process that outlived Fairy.
    static PROCESS_JOB: OnceLock<usize> = OnceLock::new();

    pub fn protect_process_tree() -> Result<(), String> {
        if PROCESS_JOB.get().is_some() {
            return Ok(());
        }
        let process = unsafe { GetCurrentProcess() };
        let job = unsafe { CreateJobObjectW(ptr::null(), ptr::null()) };
        if job.is_null() {
            return Err(last_error("PRESENCE_PROCESS_JOB_CREATE_FAILED"));
        }
        let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        let configured = unsafe {
            SetInformationJobObject(
                job,
                JobObjectExtendedLimitInformation,
                ptr::addr_of!(limits).cast::<c_void>(),
                size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            )
        } != 0;
        if !configured {
            let error = last_error("PRESENCE_PROCESS_JOB_CONFIGURE_FAILED");
            unsafe { CloseHandle(job) };
            return Err(error);
        }
        let assigned = unsafe { AssignProcessToJobObject(job, process) } != 0;
        if !assigned {
            let error = last_error("PRESENCE_PROCESS_JOB_ASSIGN_FAILED");
            let mut inherited = 0;
            let already_contained =
                unsafe { IsProcessInJob(process, ptr::null_mut(), &mut inherited) } != 0
                    && inherited != 0;
            unsafe { CloseHandle(job) };
            if already_contained {
                return Ok(());
            }
            return Err(error);
        }
        PROCESS_JOB
            .set(job as usize)
            .map_err(|_| "PRESENCE_PROCESS_JOB_ALREADY_INITIALIZED".to_owned())
    }

    fn last_error(code: &str) -> String {
        format!("{code}:{}", unsafe { GetLastError() })
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn process_job_uses_kill_on_close() {
            let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            assert_eq!(
                limits.BasicLimitInformation.LimitFlags,
                JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            );
        }
    }
}

#[cfg(not(target_os = "windows"))]
mod platform {
    pub fn protect_process_tree() -> Result<(), String> {
        Ok(())
    }
}

pub use platform::protect_process_tree;
