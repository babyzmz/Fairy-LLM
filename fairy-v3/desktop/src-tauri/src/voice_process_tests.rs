//! Real child-process lifecycle, with a tiny fixture (no model or audio device).
#![cfg(windows)]

use std::path::PathBuf;
use std::sync::atomic::Ordering;
use std::time::{Duration, Instant};

use super::{spawn_worker, VoiceWorkerLaunch, VoiceWorkerManager};

fn launch(root: &std::path::Path, handshake: &str) -> VoiceWorkerLaunch {
    let python =
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../core/.venv/Scripts/python.exe");
    assert!(
        python.is_file(),
        "Core development Python is required for process gates"
    );
    VoiceWorkerLaunch {
        program: python,
        arguments: vec!["-c".into(), format!(
            "import os,pathlib,time; pathlib.Path(os.environ['FAIRY_VOICE_DATA_DIR'],'pid').write_text(str(os.getpid())); print({handshake:?},flush=True); time.sleep(60)"
        )],
        worker_python_path: root.to_owned(),
        cosyvoice_root: root.to_owned(),
        assets_dir: root.to_owned(),
        data_dir: root.to_owned(),
        log_path: root.join("fixture.log"),
    }
}

fn assert_exited(pid: u32) {
    use windows_sys::Win32::Foundation::{CloseHandle, WAIT_OBJECT_0};
    use windows_sys::Win32::System::Threading::{
        OpenProcess, TerminateProcess, WaitForSingleObject, PROCESS_SYNCHRONIZE, PROCESS_TERMINATE,
    };
    unsafe {
        let handle = OpenProcess(PROCESS_SYNCHRONIZE | PROCESS_TERMINATE, 0, pid);
        if handle.is_null() {
            return;
        }
        let result = WaitForSingleObject(handle, 1_000);
        if result != WAIT_OBJECT_0 {
            // Cleanup is limited to the child PID written by this isolated fixture.
            let _ = TerminateProcess(handle, 1);
            let _ = WaitForSingleObject(handle, 1_000);
        }
        CloseHandle(handle);
        assert_eq!(result, WAIT_OBJECT_0, "worker survived lifecycle cleanup");
    }
}

#[test]
fn malformed_handshake_reaps_child_instead_of_leaking_it() {
    for response in ["invalid-json", r#"{"protocol":"wrong","port":9}"#] {
        let root = tempfile::tempdir().unwrap();
        let result = spawn_worker(&launch(root.path(), response));
        assert!(result.is_err());
        let pid = std::fs::read_to_string(root.path().join("pid"))
            .unwrap()
            .parse()
            .unwrap();
        assert_exited(pid);
    }
}

#[test]
fn idle_model_process_releases_after_five_minutes_but_not_during_use() {
    let root = tempfile::tempdir().unwrap();
    let launch = launch(
        root.path(),
        r#"{"protocol":"fairy-voice-worker-v1","port":9}"#,
    );
    let worker = spawn_worker(&launch).unwrap();
    let pid = worker.child.id();
    let manager = VoiceWorkerManager::new(launch);
    *manager.process.lock().unwrap() = Some(worker);
    manager.transition("ready", None);
    let mut consumer = manager.queue_playback(1).unwrap();
    consumer.activate();
    manager.lifecycle.lock().unwrap().idle_since = Some(Instant::now() - Duration::from_secs(301));
    assert!(!manager.release_if_idle().unwrap());
    drop(consumer);
    assert!(!manager.release_if_idle().unwrap());
    manager.lifecycle.lock().unwrap().idle_since = Some(Instant::now() - Duration::from_secs(301));
    assert!(manager.release_if_idle().unwrap());
    assert_eq!(manager.idle_unloads.load(Ordering::Relaxed), 1);
    assert!(manager.process.lock().unwrap().is_none());
    assert_exited(pid);
}

#[test]
fn explicit_shutdown_reaps_a_resident_worker_and_is_idempotent() {
    let root = tempfile::tempdir().unwrap();
    let launch = launch(
        root.path(),
        r#"{"protocol":"fairy-voice-worker-v1","port":9}"#,
    );
    let worker = spawn_worker(&launch).unwrap();
    let pid = worker.child.id();
    let manager = VoiceWorkerManager::new(launch);
    *manager.process.lock().unwrap() = Some(worker);
    manager.shutdown();
    manager.shutdown();
    assert_exited(pid);
}
