use std::collections::VecDeque;
use std::fs;
use std::io::{Read, Write};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use zeroize::Zeroize;

use super::{
    BackendError, BackendEvent, LocalOmniLaunch, RealtimeActivityProfile, RealtimeBackend,
    RealtimeCandidateDecision, RealtimeDialogueCandidate,
};

const CONTROL_LIMIT: usize = 256 * 1024;
const CONTROL_PROTOCOL: u64 = 1;
const LOAD_DEADLINE: Duration = Duration::from_secs(90);
const STOP_DEADLINE: Duration = Duration::from_secs(5);
const MICROPHONE_PACKET_BYTES: usize = 640;
const MAX_APPLICATION_AUDIO_BYTES: usize = 32_000;
const MAX_JPEG_BYTES: usize = 8 * 1024 * 1024;
const AUDIO_COMMIT_PACKETS: u32 = 50;

#[derive(Clone, Debug, Serialize)]
struct Identity {
    session_id: String,
    segment_id: String,
    context_epoch: u64,
    sequence: u64,
}

#[derive(Debug, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
enum CommandFrame<'a> {
    Hello {
        #[serde(flatten)]
        identity: Identity,
        protocol_version: u64,
    },
    Load {
        #[serde(flatten)]
        identity: Identity,
        manifest_digest: &'a str,
        model_version: &'a str,
        system_instruction: &'a str,
    },
    ContextBegin {
        #[serde(flatten)]
        identity: Identity,
        context_kind: &'static str,
        token_budget: u64,
        audio_budget_ms: u64,
        frame_budget: u64,
        video_width: u32,
        video_height: u32,
    },
    ContextRotate {
        #[serde(flatten)]
        identity: Identity,
        next_context_epoch: u64,
        reason: &'a str,
        public_summary: &'a str,
    },
    MediaCommit {
        #[serde(flatten)]
        identity: Identity,
        media_sequence: u64,
    },
    CancelGeneration {
        #[serde(flatten)]
        identity: Identity,
    },
    Stop {
        #[serde(flatten)]
        identity: Identity,
    },
}

#[derive(Clone, Debug, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
#[allow(dead_code)] // The full frozen wire schema is intentionally decoded and validated.
enum RuntimeEvent {
    Ready {
        session_id: String,
        segment_id: String,
        context_epoch: u64,
        sequence: u64,
        protocol_version: u64,
        runtime_compatibility: String,
        build_profile: String,
        backend_ready: bool,
    },
    LoadProgress {
        session_id: String,
        segment_id: String,
        context_epoch: u64,
        sequence: u64,
        stage: String,
        completed: u64,
        total: u64,
    },
    ModelReady {
        session_id: String,
        segment_id: String,
        context_epoch: u64,
        sequence: u64,
        model_version: String,
        manifest_digest: String,
        backend_ready: bool,
        reason: String,
    },
    ContextReady {
        session_id: String,
        segment_id: String,
        context_epoch: u64,
        sequence: u64,
        context_kind: String,
        token_budget: u64,
        audio_budget_ms: u64,
        frame_budget: u64,
        video_width: u32,
        video_height: u32,
    },
    ContextRotated {
        session_id: String,
        segment_id: String,
        context_epoch: u64,
        sequence: u64,
        reason: String,
        public_summary: String,
    },
    Decision {
        session_id: String,
        segment_id: String,
        context_epoch: u64,
        sequence: u64,
        decision: String,
        text: String,
        confidence: f64,
        grounding: Vec<String>,
        #[serde(default)]
        activity: Option<RealtimeActivityProfile>,
        #[serde(default)]
        intent: Option<String>,
        #[serde(default)]
        urgency: Option<f64>,
        #[serde(default)]
        needs_online_assistance: bool,
        backend_ready: bool,
    },
    Diagnostic {
        session_id: String,
        segment_id: String,
        context_epoch: u64,
        sequence: u64,
        code: String,
        severity: String,
    },
    Stopped {
        session_id: String,
        segment_id: String,
        context_epoch: u64,
        sequence: u64,
        reason: String,
    },
}

struct LocalDecisionProjection {
    decision: String,
    text: String,
    confidence: f64,
    grounding: Vec<String>,
    activity: Option<RealtimeActivityProfile>,
    intent: Option<String>,
    urgency: Option<f64>,
    needs_online_assistance: bool,
    backend_ready: bool,
}

impl RuntimeEvent {
    fn identity(&self) -> (&str, &str, u64) {
        match self {
            Self::Ready {
                session_id,
                segment_id,
                context_epoch,
                ..
            }
            | Self::LoadProgress {
                session_id,
                segment_id,
                context_epoch,
                ..
            }
            | Self::ModelReady {
                session_id,
                segment_id,
                context_epoch,
                ..
            }
            | Self::ContextReady {
                session_id,
                segment_id,
                context_epoch,
                ..
            }
            | Self::ContextRotated {
                session_id,
                segment_id,
                context_epoch,
                ..
            }
            | Self::Decision {
                session_id,
                segment_id,
                context_epoch,
                ..
            }
            | Self::Diagnostic {
                session_id,
                segment_id,
                context_epoch,
                ..
            }
            | Self::Stopped {
                session_id,
                segment_id,
                context_epoch,
                ..
            } => (session_id, segment_id, *context_epoch),
        }
    }
}

pub struct LocalOmniBackend {
    child: Child,
    input: ChildStdin,
    events: mpsc::Receiver<Result<RuntimeEvent, BackendError>>,
    media: HostMediaPipe,
    session_id: String,
    segment_id: String,
    context_epoch: u64,
    control_sequence: u64,
    media_sequence: u64,
    media_timestamp_us: u64,
    microphone_buffer: VecDeque<u8>,
    microphone_packets_since_commit: u32,
    stopped: bool,
    activity_profile: RealtimeActivityProfile,
    persona_digest: String,
}

impl LocalOmniBackend {
    pub fn connect(
        launch: LocalOmniLaunch,
        session_id: String,
        segment_id: String,
        context_epoch: u64,
        system_instruction: &str,
        activity_profile: RealtimeActivityProfile,
        persona_digest: String,
    ) -> Result<Self, BackendError> {
        validate_launch(&launch)?;
        let pipe_name = format!("fairy-omni-{}-{}", std::process::id(), monotonic_token());
        let media = HostMediaPipe::create(&pipe_name)?;
        let pipe_path = format!(r"\\.\pipe\{pipe_name}");
        let mut command = Command::new(&launch.runtime_path);
        command
            .arg("--stdio")
            .arg("--media-pipe")
            .arg(&pipe_path)
            .arg("--manifest")
            .arg(&launch.manifest_path)
            .arg("--model-root")
            .arg(&launch.model_root)
            .current_dir(
                launch
                    .runtime_path
                    .parent()
                    .ok_or(BackendError::LocalUnavailable)?,
            )
            .env_clear()
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        #[cfg(target_os = "windows")]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x0800_0000);
        }
        let mut child = command.spawn()?;
        let input = child.stdin.take().ok_or(BackendError::LocalProtocol)?;
        let output = child.stdout.take().ok_or(BackendError::LocalProtocol)?;
        if let Err(error) = media.connect_and_verify(child.id()) {
            terminate(&mut child);
            return Err(error);
        }
        let (event_sender, event_receiver) = mpsc::sync_channel(32);
        thread::Builder::new()
            .name("fairy-omni-events".to_owned())
            .spawn(move || read_events(output, event_sender))
            .map_err(BackendError::LocalIo)?;

        let mut backend = Self {
            child,
            input,
            events: event_receiver,
            media,
            session_id,
            segment_id,
            context_epoch,
            control_sequence: 0,
            media_sequence: 0,
            media_timestamp_us: 0,
            microphone_buffer: VecDeque::with_capacity(MICROPHONE_PACKET_BYTES * 2),
            microphone_packets_since_commit: 0,
            stopped: false,
            activity_profile,
            persona_digest,
        };
        backend.send_hello()?;
        let ready = backend.recv_until(Instant::now() + Duration::from_secs(5), |event| {
            matches!(event, RuntimeEvent::Ready { .. })
        })?;
        match ready {
            RuntimeEvent::Ready {
                protocol_version,
                runtime_compatibility,
                build_profile,
                backend_ready,
                ..
            } if protocol_version == CONTROL_PROTOCOL
                && runtime_compatibility == "fairy-omni-runtime-v1"
                && build_profile == "production-cuda"
                && !backend_ready => {}
            _ => return backend.fail(BackendError::LocalUnavailable),
        }
        backend.send_load(
            &launch.manifest_digest,
            &launch.model_version,
            system_instruction,
        )?;
        let model = backend.recv_until(Instant::now() + LOAD_DEADLINE, |event| {
            matches!(event, RuntimeEvent::ModelReady { .. })
        })?;
        match model {
            RuntimeEvent::ModelReady {
                model_version,
                manifest_digest,
                backend_ready: true,
                ..
            } if model_version == launch.model_version
                && manifest_digest == launch.manifest_digest => {}
            _ => return backend.fail(BackendError::LocalUnavailable),
        }
        backend.send_context_begin()?;
        backend.recv_until(Instant::now() + Duration::from_secs(5), |event| {
            matches!(event, RuntimeEvent::ContextReady { .. })
        })?;
        Ok(backend)
    }

    fn identity(&mut self) -> Identity {
        self.control_sequence = self.control_sequence.saturating_add(1);
        Identity {
            session_id: self.session_id.clone(),
            segment_id: self.segment_id.clone(),
            context_epoch: self.context_epoch,
            sequence: self.control_sequence,
        }
    }

    fn send_hello(&mut self) -> Result<(), BackendError> {
        let identity = self.identity();
        write_control(
            &mut self.input,
            &CommandFrame::Hello {
                identity,
                protocol_version: CONTROL_PROTOCOL,
            },
        )
    }

    fn send_load(
        &mut self,
        digest: &str,
        version: &str,
        system_instruction: &str,
    ) -> Result<(), BackendError> {
        let identity = self.identity();
        write_control(
            &mut self.input,
            &CommandFrame::Load {
                identity,
                manifest_digest: digest,
                model_version: version,
                system_instruction,
            },
        )
    }

    fn send_context_begin(&mut self) -> Result<(), BackendError> {
        let identity = self.identity();
        write_control(
            &mut self.input,
            &CommandFrame::ContextBegin {
                identity,
                context_kind: "duplex_conversation",
                token_budget: 4096,
                audio_budget_ms: 60_000,
                frame_budget: 900,
                video_width: 0,
                video_height: 0,
            },
        )
    }

    fn rotate_context_acknowledged(
        &mut self,
        next_context_epoch: u64,
        reason: &str,
        public_summary: &str,
    ) -> Result<(), BackendError> {
        if next_context_epoch
            != self
                .context_epoch
                .checked_add(1)
                .ok_or(BackendError::LocalProtocol)?
            || reason.is_empty()
            || reason.len() > 64
            || public_summary.chars().count() > 2_000
        {
            return Err(BackendError::LocalProtocol);
        }
        zeroize_queue(&mut self.microphone_buffer);
        self.microphone_buffer.clear();
        let identity = self.identity();
        write_control(
            &mut self.input,
            &CommandFrame::ContextRotate {
                identity,
                next_context_epoch,
                reason,
                public_summary,
            },
        )?;
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            let remaining = deadline
                .checked_duration_since(Instant::now())
                .ok_or(BackendError::LocalTimeout)?;
            let event = self
                .events
                .recv_timeout(remaining)
                .map_err(|_| BackendError::LocalTimeout)??;
            let (session_id, segment_id, context_epoch) = event.identity();
            if session_id != self.session_id || segment_id != self.segment_id {
                return Err(BackendError::LocalProtocol);
            }
            if let RuntimeEvent::ContextRotated {
                context_epoch,
                reason: acknowledged_reason,
                public_summary: acknowledged_summary,
                ..
            } = event
            {
                if context_epoch != next_context_epoch
                    || acknowledged_reason != reason
                    || acknowledged_summary != public_summary
                {
                    return Err(BackendError::LocalProtocol);
                }
                self.context_epoch = next_context_epoch;
                self.media_sequence = 0;
                self.media_timestamp_us = 0;
                self.microphone_packets_since_commit = 0;
                return Ok(());
            }
            if context_epoch != self.context_epoch {
                return Err(BackendError::LocalProtocol);
            }
        }
    }

    fn commit(&mut self) -> Result<(), BackendError> {
        if self.media_sequence == 0 {
            return Ok(());
        }
        let media_sequence = self.media_sequence;
        let identity = self.identity();
        write_control(
            &mut self.input,
            &CommandFrame::MediaCommit {
                identity,
                media_sequence,
            },
        )?;
        self.microphone_packets_since_commit = 0;
        Ok(())
    }

    fn write_media(&mut self, kind: u16, payload: &[u8]) -> Result<(), BackendError> {
        self.media_sequence = self
            .media_sequence
            .checked_add(1)
            .ok_or(BackendError::LocalProtocol)?;
        if kind == 1 {
            self.media_timestamp_us = self.media_timestamp_us.saturating_add(20_000);
        } else if self.media_timestamp_us == 0 {
            self.media_timestamp_us = 20_000;
        }
        self.media.write_frame(
            kind,
            self.context_epoch,
            self.media_sequence,
            self.media_timestamp_us,
            payload,
        )
    }

    fn recv_until(
        &mut self,
        deadline: Instant,
        predicate: impl Fn(&RuntimeEvent) -> bool,
    ) -> Result<RuntimeEvent, BackendError> {
        loop {
            let remaining = deadline
                .checked_duration_since(Instant::now())
                .ok_or(BackendError::LocalTimeout)?;
            let event = self
                .events
                .recv_timeout(remaining)
                .map_err(|_| BackendError::LocalTimeout)??;
            self.validate_event(&event)?;
            if predicate(&event) {
                return Ok(event);
            }
        }
    }

    fn validate_event(&self, event: &RuntimeEvent) -> Result<(), BackendError> {
        let (session_id, segment_id, context_epoch) = event.identity();
        if session_id == self.session_id
            && segment_id == self.segment_id
            && context_epoch == self.context_epoch
        {
            Ok(())
        } else {
            Err(BackendError::LocalProtocol)
        }
    }

    fn fail<T>(&mut self, error: BackendError) -> Result<T, BackendError> {
        terminate(&mut self.child);
        self.stopped = true;
        Err(error)
    }
}

impl RealtimeBackend for LocalOmniBackend {
    fn push_microphone(&mut self, pcm16_le: &[u8]) -> Result<(), BackendError> {
        self.microphone_buffer.extend(pcm16_le);
        while self.microphone_buffer.len() >= MICROPHONE_PACKET_BYTES {
            let mut packet = Vec::with_capacity(MICROPHONE_PACKET_BYTES);
            for _ in 0..MICROPHONE_PACKET_BYTES {
                packet.push(
                    self.microphone_buffer
                        .pop_front()
                        .ok_or(BackendError::LocalProtocol)?,
                );
            }
            self.write_media(1, &packet)?;
            packet.zeroize();
            self.microphone_packets_since_commit =
                self.microphone_packets_since_commit.saturating_add(1);
            if self.microphone_packets_since_commit >= AUDIO_COMMIT_PACKETS {
                self.commit()?;
            }
        }
        Ok(())
    }

    fn push_application_audio(&mut self, pcm16_le: &[u8]) -> Result<(), BackendError> {
        if pcm16_le.is_empty()
            || pcm16_le.len() > MAX_APPLICATION_AUDIO_BYTES
            || !pcm16_le.len().is_multiple_of(2)
        {
            return Err(BackendError::LocalProtocol);
        }
        self.write_media(2, pcm16_le)
    }

    fn push_video(&mut self, jpeg: &[u8]) -> Result<(), BackendError> {
        if jpeg.is_empty() || jpeg.len() > MAX_JPEG_BYTES {
            return Err(BackendError::LocalProtocol);
        }
        self.write_media(3, jpeg)?;
        self.commit()
    }

    fn push_text(&mut self, _text: &str) -> Result<(), BackendError> {
        Ok(())
    }

    fn push_assistance_result(
        &mut self,
        _call_id: &str,
        _public_summary: &str,
    ) -> Result<(), BackendError> {
        Ok(())
    }

    fn rotate_context(
        &mut self,
        next_context_epoch: u64,
        reason: &str,
        public_summary: &str,
    ) -> Result<(), BackendError> {
        self.rotate_context_acknowledged(next_context_epoch, reason, public_summary)
    }

    fn poll(&mut self) -> Result<Vec<BackendEvent>, BackendError> {
        let mut output = Vec::new();
        loop {
            let event = match self.events.try_recv() {
                Ok(event) => event?,
                Err(mpsc::TryRecvError::Empty) => break,
                Err(mpsc::TryRecvError::Disconnected) => {
                    return Err(BackendError::LocalProtocol);
                }
            };
            self.validate_event(&event)?;
            match event {
                RuntimeEvent::Decision {
                    decision,
                    text,
                    confidence,
                    grounding,
                    activity,
                    intent,
                    urgency,
                    needs_online_assistance,
                    backend_ready,
                    ..
                } => {
                    let candidate = project_local_decision(
                        LocalDecisionProjection {
                            decision,
                            text,
                            confidence,
                            grounding,
                            activity,
                            intent,
                            urgency,
                            needs_online_assistance,
                            backend_ready,
                        },
                        self.activity_profile,
                        &self.persona_digest,
                    )?;
                    output.push(BackendEvent::PerceptionCandidate(candidate));
                }
                RuntimeEvent::Diagnostic { code, severity, .. } => {
                    if code.len() > 128 || severity.len() > 32 {
                        return Err(BackendError::LocalProtocol);
                    }
                }
                RuntimeEvent::Stopped { .. } => self.stopped = true,
                RuntimeEvent::Ready { .. }
                | RuntimeEvent::LoadProgress { .. }
                | RuntimeEvent::ModelReady { .. }
                | RuntimeEvent::ContextReady { .. }
                | RuntimeEvent::ContextRotated { .. } => {}
            }
        }
        Ok(output)
    }

    fn pause(&mut self) -> Result<(), BackendError> {
        zeroize_queue(&mut self.microphone_buffer);
        self.microphone_buffer.clear();
        let identity = self.identity();
        write_control(
            &mut self.input,
            &CommandFrame::CancelGeneration { identity },
        )
    }

    fn stop(&mut self) -> Result<(), BackendError> {
        if self.stopped {
            return Ok(());
        }
        let identity = self.identity();
        let _ = write_control(&mut self.input, &CommandFrame::Stop { identity });
        let deadline = Instant::now() + STOP_DEADLINE;
        loop {
            if self.child.try_wait()?.is_some() {
                self.stopped = true;
                return Ok(());
            }
            if Instant::now() >= deadline {
                terminate(&mut self.child);
                self.stopped = true;
                return Ok(());
            }
            thread::sleep(Duration::from_millis(20));
        }
    }
}

fn project_local_decision(
    projection: LocalDecisionProjection,
    default_activity: RealtimeActivityProfile,
    persona_digest: &str,
) -> Result<RealtimeDialogueCandidate, BackendError> {
    if !projection.backend_ready
        || !projection.confidence.is_finite()
        || !(0.0..=1.0).contains(&projection.confidence)
    {
        return Err(BackendError::LocalProtocol);
    }
    let decision = match projection.decision.as_str() {
        "listen" => RealtimeCandidateDecision::Listen,
        "speak" => RealtimeCandidateDecision::Speak,
        "request_assistance" => RealtimeCandidateDecision::RequestAssistance,
        _ => return Err(BackendError::LocalProtocol),
    };
    let candidate = RealtimeDialogueCandidate {
        decision,
        activity: projection.activity.unwrap_or(default_activity),
        confidence: projection.confidence,
        intent: projection.intent.unwrap_or_else(|| match decision {
            RealtimeCandidateDecision::Listen => "observe".to_owned(),
            RealtimeCandidateDecision::Speak => "comment".to_owned(),
            RealtimeCandidateDecision::RequestAssistance => "assist".to_owned(),
        }),
        grounding: projection.grounding,
        text: projection.text,
        urgency: projection.urgency.unwrap_or(0.0),
        needs_online_assistance: projection.needs_online_assistance,
        response_to_user: false,
        stable: true,
        persona_digest: persona_digest.to_owned(),
    };
    candidate
        .is_valid()
        .then_some(candidate)
        .ok_or(BackendError::LocalProtocol)
}

impl Drop for LocalOmniBackend {
    fn drop(&mut self) {
        zeroize_queue(&mut self.microphone_buffer);
        self.microphone_buffer.clear();
        let _ = self.stop();
    }
}

fn validate_launch(launch: &LocalOmniLaunch) -> Result<(), BackendError> {
    let runtime = fs::symlink_metadata(&launch.runtime_path)?;
    let manifest = fs::symlink_metadata(&launch.manifest_path)?;
    let model_root = fs::symlink_metadata(&launch.model_root)?;
    let valid_runtime_name = launch
        .runtime_path
        .file_name()
        .and_then(|name| name.to_str())
        == Some("fairy-omni-runtime.exe");
    let valid_digest = launch.manifest_digest.len() == 64
        && launch
            .manifest_digest
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase());
    if runtime.file_type().is_symlink()
        || !runtime.is_file()
        || manifest.file_type().is_symlink()
        || !manifest.is_file()
        || model_root.file_type().is_symlink()
        || !model_root.is_dir()
        || !valid_runtime_name
        || !valid_digest
        || launch.model_version.trim().is_empty()
        || launch.model_version.len() > 128
    {
        return Err(BackendError::LocalUnavailable);
    }
    Ok(())
}

fn write_control(output: &mut impl Write, value: &impl Serialize) -> Result<(), BackendError> {
    let mut payload = serde_json::to_vec(value).map_err(|_| BackendError::LocalProtocol)?;
    if payload.is_empty() || payload.len() > CONTROL_LIMIT {
        payload.zeroize();
        return Err(BackendError::LocalProtocol);
    }
    output.write_all(&(payload.len() as u32).to_le_bytes())?;
    output.write_all(&payload)?;
    output.flush()?;
    payload.zeroize();
    Ok(())
}

fn read_events(mut input: impl Read, sender: mpsc::SyncSender<Result<RuntimeEvent, BackendError>>) {
    loop {
        let event = read_event(&mut input);
        let terminal = event.is_err();
        if sender.send(event).is_err() || terminal {
            return;
        }
    }
}

fn read_event(input: &mut impl Read) -> Result<RuntimeEvent, BackendError> {
    let mut prefix = [0_u8; 4];
    input.read_exact(&mut prefix)?;
    let length = u32::from_le_bytes(prefix) as usize;
    if length == 0 || length > CONTROL_LIMIT {
        return Err(BackendError::LocalProtocol);
    }
    let mut payload = vec![0_u8; length];
    input.read_exact(&mut payload)?;
    let event = serde_json::from_slice(&payload).map_err(|_| BackendError::LocalProtocol);
    payload.zeroize();
    event
}

fn terminate(child: &mut Child) {
    let _ = child.kill();
    let _ = child.wait();
}

fn zeroize_queue(queue: &mut VecDeque<u8>) {
    for byte in queue.iter_mut() {
        *byte = 0;
    }
}

fn monotonic_token() -> u128 {
    use std::time::SystemTime;
    SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .map_or(0, |duration| duration.as_nanos())
}

#[cfg(target_os = "windows")]
struct HostMediaPipe {
    handle: std::os::windows::io::OwnedHandle,
}

#[cfg(target_os = "windows")]
impl HostMediaPipe {
    fn create(name: &str) -> Result<Self, BackendError> {
        use std::ffi::OsStr;
        use std::os::windows::ffi::OsStrExt;
        use std::os::windows::io::{FromRawHandle, RawHandle};
        use std::ptr;
        use windows_sys::Win32::Foundation::INVALID_HANDLE_VALUE;
        use windows_sys::Win32::Storage::FileSystem::{
            FILE_FLAG_FIRST_PIPE_INSTANCE, PIPE_ACCESS_OUTBOUND,
        };
        use windows_sys::Win32::System::Pipes::{
            CreateNamedPipeW, PIPE_NOWAIT, PIPE_READMODE_BYTE, PIPE_REJECT_REMOTE_CLIENTS,
            PIPE_TYPE_BYTE,
        };

        if name.is_empty()
            || name.len() > 96
            || !name
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
        {
            return Err(BackendError::LocalProtocol);
        }
        let path = format!(r"\\.\pipe\{name}");
        let mut wide = OsStr::new(&path).encode_wide().collect::<Vec<_>>();
        wide.push(0);
        let handle = unsafe {
            CreateNamedPipeW(
                wide.as_ptr(),
                PIPE_ACCESS_OUTBOUND | FILE_FLAG_FIRST_PIPE_INSTANCE,
                PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_NOWAIT | PIPE_REJECT_REMOTE_CLIENTS,
                1,
                1024 * 1024,
                0,
                5_000,
                ptr::null(),
            )
        };
        if handle == INVALID_HANDLE_VALUE {
            return Err(BackendError::LocalIo(std::io::Error::last_os_error()));
        }
        Ok(Self {
            handle: unsafe {
                std::os::windows::io::OwnedHandle::from_raw_handle(handle as RawHandle)
            },
        })
    }

    fn connect_and_verify(&self, expected_pid: u32) -> Result<(), BackendError> {
        use std::os::windows::io::AsRawHandle;
        use windows_sys::Win32::Foundation::{
            GetLastError, ERROR_PIPE_CONNECTED, ERROR_PIPE_LISTENING,
        };
        use windows_sys::Win32::System::Pipes::{ConnectNamedPipe, GetNamedPipeClientProcessId};

        let handle = self.handle.as_raw_handle() as _;
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            let connected = unsafe { ConnectNamedPipe(handle, std::ptr::null_mut()) };
            if connected != 0 || unsafe { GetLastError() } == ERROR_PIPE_CONNECTED {
                break;
            }
            if unsafe { GetLastError() } != ERROR_PIPE_LISTENING {
                return Err(BackendError::LocalIo(std::io::Error::last_os_error()));
            }
            if Instant::now() >= deadline {
                return Err(BackendError::LocalTimeout);
            }
            thread::sleep(Duration::from_millis(5));
        }
        let mut client_pid = 0_u32;
        if unsafe { GetNamedPipeClientProcessId(handle, &mut client_pid) } == 0 {
            return Err(BackendError::LocalIo(std::io::Error::last_os_error()));
        }
        if client_pid != expected_pid {
            return Err(BackendError::LocalProtocol);
        }
        Ok(())
    }

    fn write_frame(
        &self,
        kind: u16,
        context_epoch: u64,
        sequence: u64,
        timestamp_us: u64,
        payload: &[u8],
    ) -> Result<(), BackendError> {
        let length = u32::try_from(payload.len()).map_err(|_| BackendError::LocalProtocol)?;
        let mut header = [0_u8; 36];
        header[..4].copy_from_slice(b"FOMI");
        header[4..6].copy_from_slice(&1_u16.to_le_bytes());
        header[6..8].copy_from_slice(&kind.to_le_bytes());
        header[8..16].copy_from_slice(&context_epoch.to_le_bytes());
        header[16..24].copy_from_slice(&sequence.to_le_bytes());
        header[24..32].copy_from_slice(&timestamp_us.to_le_bytes());
        header[32..36].copy_from_slice(&length.to_le_bytes());
        self.write_all(&header)?;
        self.write_all(payload)
    }

    fn write_all(&self, mut bytes: &[u8]) -> Result<(), BackendError> {
        use std::os::windows::io::AsRawHandle;
        use windows_sys::Win32::Storage::FileSystem::WriteFile;

        while !bytes.is_empty() {
            let mut written = 0_u32;
            let chunk = bytes.len().min(u32::MAX as usize);
            let ok = unsafe {
                WriteFile(
                    self.handle.as_raw_handle() as _,
                    bytes.as_ptr().cast(),
                    chunk as u32,
                    &mut written,
                    std::ptr::null_mut(),
                )
            };
            if ok == 0 || written == 0 {
                return Err(BackendError::LocalIo(std::io::Error::last_os_error()));
            }
            bytes = &bytes[written as usize..];
        }
        Ok(())
    }
}

#[cfg(not(target_os = "windows"))]
struct HostMediaPipe;

#[cfg(not(target_os = "windows"))]
impl HostMediaPipe {
    fn create(_name: &str) -> Result<Self, BackendError> {
        Err(BackendError::LocalUnavailable)
    }
    fn connect_and_verify(&self, _expected_pid: u32) -> Result<(), BackendError> {
        Err(BackendError::LocalUnavailable)
    }
    fn write_frame(
        &self,
        _kind: u16,
        _context_epoch: u64,
        _sequence: u64,
        _timestamp_us: u64,
        _payload: &[u8],
    ) -> Result<(), BackendError> {
        Err(BackendError::LocalUnavailable)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn control_frames_are_little_endian_bounded_and_secret_free() {
        let mut bytes = Vec::new();
        let command = CommandFrame::Hello {
            identity: Identity {
                session_id: "session".to_owned(),
                segment_id: "segment".to_owned(),
                context_epoch: 1,
                sequence: 1,
            },
            protocol_version: CONTROL_PROTOCOL,
        };
        write_control(&mut bytes, &command).expect("write");
        let length = u32::from_le_bytes(bytes[..4].try_into().expect("prefix")) as usize;
        assert_eq!(length, bytes.len() - 4);
        assert!(length <= CONTROL_LIMIT);
    }

    #[test]
    fn runtime_events_reject_unknown_fields() {
        let value = serde_json::json!({
            "type": "diagnostic",
            "session_id": "session",
            "segment_id": "segment",
            "context_epoch": 1,
            "sequence": 1,
            "code": "safe",
            "severity": "info",
            "prompt": "not allowed"
        });
        assert!(serde_json::from_value::<RuntimeEvent>(value).is_err());
    }

    #[test]
    fn local_context_rotation_uses_the_frozen_acknowledged_wire_shape() {
        let command = CommandFrame::ContextRotate {
            identity: Identity {
                session_id: "session".to_owned(),
                segment_id: "segment".to_owned(),
                context_epoch: 1,
                sequence: 4,
            },
            next_context_epoch: 2,
            reason: "profile_changed",
            public_summary: "",
        };
        let value = serde_json::to_value(command).expect("command");
        assert_eq!(value["type"], "context_rotate");
        assert_eq!(value["context_epoch"], 1);
        assert_eq!(value["next_context_epoch"], 2);
        assert_eq!(value["reason"], "profile_changed");
        assert_eq!(value["public_summary"], "");

        let event: RuntimeEvent = serde_json::from_value(serde_json::json!({
            "type": "context_rotated",
            "session_id": "session",
            "segment_id": "segment",
            "context_epoch": 2,
            "sequence": 4,
            "reason": "profile_changed",
            "public_summary": ""
        }))
        .expect("acknowledgement");
        assert_eq!(event.identity(), ("session", "segment", 2));
    }

    #[test]
    fn local_decisions_preserve_structured_candidate_fields() {
        let candidate = project_local_decision(
            LocalDecisionProjection {
                decision: "request_assistance".to_owned(),
                text: "Check the current encounter.".to_owned(),
                confidence: 0.82,
                grounding: vec!["current_window: encounter changed".to_owned()],
                activity: Some(RealtimeActivityProfile::Game),
                intent: Some("assist".to_owned()),
                urgency: Some(0.4),
                needs_online_assistance: true,
                backend_ready: true,
            },
            RealtimeActivityProfile::Auto,
            &"a".repeat(64),
        )
        .expect("structured candidate");

        assert_eq!(
            candidate.decision,
            RealtimeCandidateDecision::RequestAssistance
        );
        assert_eq!(candidate.activity, RealtimeActivityProfile::Game);
        assert_eq!(candidate.intent, "assist");
        assert_eq!(candidate.grounding.len(), 1);
        assert_eq!(candidate.urgency, 0.4);
        assert!(candidate.needs_online_assistance);
    }

    #[test]
    fn local_speak_without_grounding_remains_a_candidate_for_the_director() {
        let candidate = project_local_decision(
            LocalDecisionProjection {
                decision: "speak".to_owned(),
                text: "A guess that the Director must reject.".to_owned(),
                confidence: 0.7,
                grounding: Vec::new(),
                activity: None,
                intent: None,
                urgency: None,
                needs_online_assistance: false,
                backend_ready: true,
            },
            RealtimeActivityProfile::Focus,
            &"a".repeat(64),
        )
        .expect("bounded candidate");
        assert!(candidate.grounding.is_empty());
        assert_eq!(candidate.activity, RealtimeActivityProfile::Focus);
    }
}
