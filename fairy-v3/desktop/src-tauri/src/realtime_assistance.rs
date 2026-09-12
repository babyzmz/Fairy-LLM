use std::collections::{HashMap, HashSet};
use std::io::Write;
use std::process::ChildStdin;
use std::sync::atomic::{AtomicBool, AtomicI64, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use fairy_core_bridge::{CoreBridge, CoreBridgeError};
use fairy_realtime_worker::{write_frame, HostCommand};
use serde::Serialize;
use serde_json::{json, Value};
use tauri::{AppHandle, Emitter};

use crate::realtime_worker::REALTIME_WORKER_EVENT;

const CORE_RETRY_DELAY: Duration = Duration::from_millis(400);
const CORE_POLL_DELAY: Duration = Duration::from_millis(250);
const PUBLIC_FAILURE_SUMMARY: &str = "I couldn't complete that lookup.";

#[derive(Clone, Debug, PartialEq, Eq)]
struct AssistanceCandidate {
    session_id: String,
    segment_id: String,
    context_epoch: u64,
    request_id: String,
    question: String,
    activity_profile: String,
    needs_online_assistance: bool,
}

impl AssistanceCandidate {
    fn parse(value: &Value) -> Option<Self> {
        if value.get("type").and_then(Value::as_str) != Some("assistance_request") {
            return None;
        }
        let candidate = Self {
            session_id: bounded_string(value, "session_id", 128)?,
            segment_id: bounded_string(value, "segment_id", 128)?,
            context_epoch: value.get("context_epoch")?.as_u64()?,
            request_id: bounded_string(value, "request_id", 128)?,
            question: bounded_string(value, "public_intent", 4_000)?,
            activity_profile: bounded_string(value, "activity", 32)?,
            needs_online_assistance: value
                .get("needs_online_assistance")
                .and_then(Value::as_bool)
                .unwrap_or(false),
        };
        (candidate.context_epoch >= 1).then_some(candidate)
    }
}

fn bounded_string(value: &Value, key: &str, max_chars: usize) -> Option<String> {
    let text = value.get(key)?.as_str()?.trim();
    (!text.is_empty() && text.chars().count() <= max_chars).then(|| text.to_owned())
}

#[derive(Clone, Debug)]
struct AssistanceSessionConfig {
    locale: String,
    online_assistance_enabled: bool,
}

trait CoreAssistanceClient: Send + Sync {
    fn call(&self, method: &str, params: Value) -> Result<Value, CoreAssistanceError>;
}

#[derive(Clone, Debug, PartialEq, Eq)]
enum CoreAssistanceError {
    Unavailable,
    Rpc(String),
}

struct SharedCoreAssistanceClient {
    core: Arc<Mutex<Option<CoreBridge>>>,
    next_request_id: AtomicI64,
}

impl SharedCoreAssistanceClient {
    fn new(core: Arc<Mutex<Option<CoreBridge>>>) -> Self {
        Self {
            core,
            next_request_id: AtomicI64::new(10_000),
        }
    }
}

impl CoreAssistanceClient for SharedCoreAssistanceClient {
    fn call(&self, method: &str, params: Value) -> Result<Value, CoreAssistanceError> {
        let request_id = self.next_request_id.fetch_add(1, Ordering::Relaxed);
        let guard = self
            .core
            .lock()
            .map_err(|_| CoreAssistanceError::Unavailable)?;
        let bridge = guard
            .as_ref()
            .ok_or(CoreAssistanceError::Unavailable)?
            .clone();
        drop(guard);
        let response = bridge
            .call(json!({
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }))
            .map_err(|error| match error {
                CoreBridgeError::WorkerInterrupted
                | CoreBridgeError::Io(_)
                | CoreBridgeError::LockPoisoned => CoreAssistanceError::Unavailable,
                _ => CoreAssistanceError::Rpc("ASSISTANCE_CORE_PROTOCOL".to_owned()),
            })?;
        if let Some(error) = response.get("error") {
            let code = error
                .pointer("/data/error_code")
                .and_then(Value::as_str)
                .unwrap_or("ASSISTANCE_CORE_REJECTED");
            return Err(CoreAssistanceError::Rpc(code.to_owned()));
        }
        response
            .get("result")
            .cloned()
            .ok_or_else(|| CoreAssistanceError::Rpc("ASSISTANCE_CORE_PROTOCOL".to_owned()))
    }
}

trait AssistanceCommandSink: Send + Sync {
    fn send(&self, command: &HostCommand) -> Result<(), ()>;
}

struct WorkerCommandSink {
    input: Arc<Mutex<ChildStdin>>,
}

impl AssistanceCommandSink for WorkerCommandSink {
    fn send(&self, command: &HostCommand) -> Result<(), ()> {
        let mut input = self.input.lock().map_err(|_| ())?;
        write_frame(&mut *input, command).map_err(|_| ())?;
        input.flush().map_err(|_| ())
    }
}

trait AssistanceEventSink: Send + Sync {
    fn emit(&self, event: Value);
}

struct AppEventSink(AppHandle);

impl AssistanceEventSink for AppEventSink {
    fn emit(&self, event: Value) {
        let _ = self.0.emit(REALTIME_WORKER_EVENT, event);
    }
}

struct RouterSession {
    cancel: Arc<AtomicBool>,
    config: AssistanceSessionConfig,
    worker: Arc<dyn AssistanceCommandSink>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct RealtimeAssistancePublicState {
    pub session_id: String,
    pub segment_id: String,
    pub context_epoch: u64,
    pub request_id: String,
    pub public_intent: String,
    pub status: String,
    pub error_code: Option<String>,
    pub public_summary: Option<String>,
}

pub struct RealtimeAssistanceRouter {
    core: Arc<dyn CoreAssistanceClient>,
    sessions: Mutex<HashMap<String, RouterSession>>,
    active_jobs: Mutex<HashSet<(String, String)>>,
    projections: Mutex<HashMap<String, HashMap<String, RealtimeAssistancePublicState>>>,
}

impl RealtimeAssistanceRouter {
    pub fn new(core: Arc<Mutex<Option<CoreBridge>>>) -> Self {
        Self::with_client(Arc::new(SharedCoreAssistanceClient::new(core)))
    }

    fn with_client(core: Arc<dyn CoreAssistanceClient>) -> Self {
        Self {
            core,
            sessions: Mutex::new(HashMap::new()),
            active_jobs: Mutex::new(HashSet::new()),
            projections: Mutex::new(HashMap::new()),
        }
    }

    pub fn attach_session(
        &self,
        session_id: &str,
        locale: &str,
        online_assistance_enabled: bool,
        input: Arc<Mutex<ChildStdin>>,
    ) {
        self.attach_sink(
            session_id,
            AssistanceSessionConfig {
                locale: normalize_locale(locale),
                online_assistance_enabled,
            },
            Arc::new(WorkerCommandSink { input }),
        );
    }

    fn attach_sink(
        &self,
        session_id: &str,
        config: AssistanceSessionConfig,
        worker: Arc<dyn AssistanceCommandSink>,
    ) {
        let Ok(mut sessions) = self.sessions.lock() else {
            return;
        };
        for (other_id, session) in sessions.iter() {
            if other_id != session_id {
                session.cancel.store(true, Ordering::Release);
            }
        }
        sessions.retain(|other_id, _| other_id == session_id);
        if let Ok(mut projections) = self.projections.lock() {
            projections.retain(|other_id, _| other_id == session_id);
        }
        if let Some(current) = sessions.get_mut(session_id) {
            current.config = config;
            current.worker = worker;
            return;
        }
        sessions.insert(
            session_id.to_owned(),
            RouterSession {
                cancel: Arc::new(AtomicBool::new(false)),
                config,
                worker,
            },
        );
    }

    pub fn end_session(&self, session_id: &str) {
        if let Ok(mut sessions) = self.sessions.lock() {
            if let Some(session) = sessions.remove(session_id) {
                session.cancel.store(true, Ordering::Release);
            }
        }
        if let Ok(mut projections) = self.projections.lock() {
            projections.remove(session_id);
        }
    }

    pub fn shutdown(&self) {
        if let Ok(mut sessions) = self.sessions.lock() {
            for session in sessions.values() {
                session.cancel.store(true, Ordering::Release);
            }
            sessions.clear();
        }
        if let Ok(mut projections) = self.projections.lock() {
            projections.clear();
        }
    }

    pub fn snapshot(&self, session_id: Option<&str>) -> Vec<RealtimeAssistancePublicState> {
        let Some(session_id) = session_id else {
            return Vec::new();
        };
        let mut values = self
            .projections
            .lock()
            .ok()
            .and_then(|projections| projections.get(session_id).cloned())
            .map(|states| states.into_values().collect::<Vec<_>>())
            .unwrap_or_default();
        values.sort_by(|left, right| left.request_id.cmp(&right.request_id));
        values
    }

    pub fn route(self: &Arc<Self>, app: AppHandle, projection: Value) {
        self.route_with_sink(projection, Arc::new(AppEventSink(app)));
    }

    fn route_with_sink(self: &Arc<Self>, projection: Value, events: Arc<dyn AssistanceEventSink>) {
        let Some(candidate) = AssistanceCandidate::parse(&projection) else {
            return;
        };
        let key = (candidate.session_id.clone(), candidate.request_id.clone());
        let session_exists = self
            .sessions
            .lock()
            .ok()
            .is_some_and(|sessions| sessions.contains_key(&candidate.session_id));
        if !session_exists {
            return;
        }
        let Ok(mut active_jobs) = self.active_jobs.lock() else {
            return;
        };
        if !active_jobs.insert(key.clone()) {
            return;
        }
        drop(active_jobs);

        self.emit_state(&events, &candidate, "pending", None, None);
        let router = Arc::clone(self);
        let job_candidate = candidate.clone();
        let job_key = key.clone();
        let job_events = Arc::clone(&events);
        let spawn = thread::Builder::new()
            .name("fairy-realtime-assistance".to_owned())
            .spawn(move || {
                router.run_job(&job_candidate, &job_events);
                if let Ok(mut active_jobs) = router.active_jobs.lock() {
                    active_jobs.remove(&job_key);
                }
            });
        if spawn.is_err() {
            if let Ok(mut active_jobs) = self.active_jobs.lock() {
                active_jobs.remove(&key);
            }
            self.emit_state(
                &events,
                &candidate,
                "failed",
                Some("ASSISTANCE_ROUTER_UNAVAILABLE"),
                Some(PUBLIC_FAILURE_SUMMARY),
            );
            let _ = self.deliver(&candidate, PUBLIC_FAILURE_SUMMARY, false);
        }
    }

    fn run_job(&self, candidate: &AssistanceCandidate, events: &Arc<dyn AssistanceEventSink>) {
        let Some((cancel, config)) = self.session_snapshot(&candidate.session_id) else {
            return;
        };
        if candidate.needs_online_assistance && !config.online_assistance_enabled {
            self.finish_failed(
                candidate,
                events,
                "ASSISTANCE_NETWORK_DISABLED",
                PUBLIC_FAILURE_SUMMARY,
            );
            return;
        }

        let mut conversation_id = None;
        let mut submitted = false;
        let mut revision = None;
        let mut core_paused = false;
        loop {
            if cancel.load(Ordering::Acquire) {
                self.cancel_core(candidate, revision);
                return;
            }

            if conversation_id.is_none() {
                match self.core.call(
                    "realtime.sessions.get",
                    json!({ "session_id": candidate.session_id }),
                ) {
                    Ok(session) => {
                        conversation_id = session
                            .get("conversation_id")
                            .and_then(Value::as_str)
                            .map(str::to_owned);
                        if conversation_id.is_none() {
                            self.finish_failed(
                                candidate,
                                events,
                                "ASSISTANCE_CONVERSATION_UNAVAILABLE",
                                PUBLIC_FAILURE_SUMMARY,
                            );
                            return;
                        }
                        core_paused = false;
                    }
                    Err(CoreAssistanceError::Unavailable) => {
                        if !core_paused {
                            self.emit_state(
                                events,
                                candidate,
                                "paused",
                                Some("ASSISTANCE_CORE_UNAVAILABLE"),
                                None,
                            );
                            core_paused = true;
                        }
                        wait_or_cancel(&cancel, CORE_RETRY_DELAY);
                        continue;
                    }
                    Err(CoreAssistanceError::Rpc(code)) => {
                        self.finish_failed(candidate, events, &code, PUBLIC_FAILURE_SUMMARY);
                        return;
                    }
                }
            }

            let response = if submitted {
                self.core.call(
                    "realtime.assistance.get",
                    json!({
                        "session_id": candidate.session_id,
                        "request_id": candidate.request_id,
                    }),
                )
            } else {
                self.core.call(
                    "realtime.assistance.request",
                    assistance_request(
                        candidate,
                        &config,
                        conversation_id.as_deref().unwrap_or(""),
                    ),
                )
            };
            let assistance = match response {
                Ok(assistance) => {
                    core_paused = false;
                    assistance
                }
                Err(CoreAssistanceError::Unavailable) => {
                    if !core_paused {
                        self.emit_state(
                            events,
                            candidate,
                            "paused",
                            Some("ASSISTANCE_CORE_UNAVAILABLE"),
                            None,
                        );
                        core_paused = true;
                    }
                    wait_or_cancel(&cancel, CORE_RETRY_DELAY);
                    continue;
                }
                Err(CoreAssistanceError::Rpc(code)) => {
                    self.finish_failed(candidate, events, &code, PUBLIC_FAILURE_SUMMARY);
                    return;
                }
            };
            revision = assistance.get("revision").and_then(Value::as_u64);
            let status = assistance
                .get("status")
                .and_then(Value::as_str)
                .unwrap_or("failed");
            let error_code = assistance.get("error_code").and_then(Value::as_str);
            match status {
                "queued" if error_code == Some("ASSISTANCE_CONVERSATION_BUSY") => {
                    self.emit_state(events, candidate, "pending", error_code, None);
                    submitted = false;
                    wait_or_cancel(&cancel, CORE_RETRY_DELAY);
                }
                "queued" | "running" | "awaiting_approval" => {
                    self.emit_state(events, candidate, status, error_code, None);
                    submitted = true;
                    wait_or_cancel(&cancel, CORE_POLL_DELAY);
                }
                "completed" => {
                    let summary = assistance
                        .get("spoken_summary")
                        .and_then(Value::as_str)
                        .filter(|summary| !summary.trim().is_empty())
                        .unwrap_or("The answer is ready in the main chat.");
                    self.emit_state(events, candidate, "completed", None, Some(summary));
                    if !self.deliver(candidate, summary, true) {
                        self.emit_state(
                            events,
                            candidate,
                            "failed",
                            Some("ASSISTANCE_WORKER_UNAVAILABLE"),
                            Some("The answer is ready in the main chat."),
                        );
                    }
                    return;
                }
                "cancelled" => {
                    self.emit_state(events, candidate, "cancelled", error_code, None);
                    return;
                }
                _ => {
                    self.finish_failed(
                        candidate,
                        events,
                        error_code.unwrap_or("ASSISTANCE_FAILED"),
                        PUBLIC_FAILURE_SUMMARY,
                    );
                    return;
                }
            }
        }
    }

    fn session_snapshot(
        &self,
        session_id: &str,
    ) -> Option<(Arc<AtomicBool>, AssistanceSessionConfig)> {
        let sessions = self.sessions.lock().ok()?;
        let session = sessions.get(session_id)?;
        Some((Arc::clone(&session.cancel), session.config.clone()))
    }

    fn finish_failed(
        &self,
        candidate: &AssistanceCandidate,
        events: &Arc<dyn AssistanceEventSink>,
        error_code: &str,
        summary: &str,
    ) {
        self.emit_state(events, candidate, "failed", Some(error_code), Some(summary));
        let _ = self.deliver(candidate, summary, false);
    }

    fn emit_state(
        &self,
        events: &Arc<dyn AssistanceEventSink>,
        candidate: &AssistanceCandidate,
        status: &str,
        error_code: Option<&str>,
        public_summary: Option<&str>,
    ) {
        let projection = RealtimeAssistancePublicState {
            session_id: candidate.session_id.clone(),
            segment_id: candidate.segment_id.clone(),
            context_epoch: candidate.context_epoch,
            request_id: candidate.request_id.clone(),
            public_intent: candidate.question.clone(),
            status: status.to_owned(),
            error_code: error_code.map(str::to_owned),
            public_summary: public_summary.map(|summary| summary.chars().take(2_000).collect()),
        };
        if let Ok(mut projections) = self.projections.lock() {
            let session = projections.entry(candidate.session_id.clone()).or_default();
            session.insert(candidate.request_id.clone(), projection.clone());
            if session.len() > 16 {
                let mut keys = session.keys().cloned().collect::<Vec<_>>();
                keys.sort();
                for key in keys.into_iter().take(session.len().saturating_sub(16)) {
                    session.remove(&key);
                }
            }
        }
        events.emit(serde_json::to_value(projection).map_or_else(
            |_| json!({ "type": "assistance_state" }),
            |mut value| {
                value["type"] = json!("assistance_state");
                value
            },
        ));
    }

    fn deliver(&self, candidate: &AssistanceCandidate, summary: &str, succeeded: bool) -> bool {
        let worker = self.sessions.lock().ok().and_then(|sessions| {
            sessions
                .get(&candidate.session_id)
                .map(|session| Arc::clone(&session.worker))
        });
        if let Some(worker) = worker {
            return worker
                .send(&HostCommand::AssistanceResult {
                    session_id: candidate.session_id.clone(),
                    request_id: candidate.request_id.clone(),
                    public_summary: summary.chars().take(2_000).collect(),
                    succeeded,
                })
                .is_ok();
        }
        false
    }

    fn cancel_core(&self, candidate: &AssistanceCandidate, revision: Option<u64>) {
        let Some(expected_revision) = revision else {
            return;
        };
        let _ = self.core.call(
            "realtime.assistance.cancel",
            json!({
                "session_id": candidate.session_id,
                "request_id": candidate.request_id,
                "expected_revision": expected_revision,
            }),
        );
    }
}

fn normalize_locale(locale: &str) -> String {
    let locale = locale.trim();
    if (2..=32).contains(&locale.chars().count()) {
        locale.to_owned()
    } else {
        "zh-CN".to_owned()
    }
}

fn assistance_request(
    candidate: &AssistanceCandidate,
    config: &AssistanceSessionConfig,
    conversation_id: &str,
) -> Value {
    json!({
        "session_id": candidate.session_id,
        "conversation_id": conversation_id,
        "request_id": candidate.request_id,
        "segment_id": candidate.segment_id,
        "context_epoch": candidate.context_epoch,
        "question": candidate.question,
        "activity_profile": candidate.activity_profile,
        "application_title": null,
        "observed_facts": [],
        "allow_network": candidate.needs_online_assistance
            && config.online_assistance_enabled,
        "locale": config.locale,
    })
}

fn wait_or_cancel(cancel: &AtomicBool, duration: Duration) {
    let slices = (duration.as_millis() / 25).max(1);
    for _ in 0..slices {
        if cancel.load(Ordering::Acquire) {
            return;
        }
        thread::sleep(Duration::from_millis(25));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::VecDeque;

    #[derive(Default)]
    struct FakeCore {
        calls: Mutex<Vec<(String, Value)>>,
        responses: Mutex<VecDeque<Result<Value, CoreAssistanceError>>>,
    }

    impl FakeCore {
        fn with_responses(responses: Vec<Result<Value, CoreAssistanceError>>) -> Self {
            Self {
                calls: Mutex::new(Vec::new()),
                responses: Mutex::new(responses.into()),
            }
        }
    }

    impl CoreAssistanceClient for FakeCore {
        fn call(&self, method: &str, params: Value) -> Result<Value, CoreAssistanceError> {
            self.calls
                .lock()
                .expect("calls")
                .push((method.to_owned(), params));
            self.responses
                .lock()
                .expect("responses")
                .pop_front()
                .unwrap_or(Err(CoreAssistanceError::Unavailable))
        }
    }

    #[derive(Default)]
    struct FakeWorker {
        commands: Mutex<Vec<Value>>,
    }

    impl AssistanceCommandSink for FakeWorker {
        fn send(&self, command: &HostCommand) -> Result<(), ()> {
            self.commands
                .lock()
                .expect("commands")
                .push(serde_json::to_value(command).expect("serialize command"));
            Ok(())
        }
    }

    struct FailingWorker;

    impl AssistanceCommandSink for FailingWorker {
        fn send(&self, _command: &HostCommand) -> Result<(), ()> {
            Err(())
        }
    }

    #[derive(Default)]
    struct FakeEvents {
        values: Mutex<Vec<Value>>,
    }

    impl AssistanceEventSink for FakeEvents {
        fn emit(&self, event: Value) {
            self.values.lock().expect("events").push(event);
        }
    }

    fn candidate() -> Value {
        json!({
            "type": "assistance_request",
            "session_id": "session-1",
            "segment_id": "segment-1",
            "context_epoch": 2,
            "request_id": "request-1",
            "public_intent": "Where is the east gate?",
            "activity": "game",
            "needs_online_assistance": true,
        })
    }

    fn wait_for_terminal(events: &FakeEvents) {
        for _ in 0..100 {
            if events.values.lock().expect("events").iter().any(|event| {
                matches!(
                    event.get("status").and_then(Value::as_str),
                    Some("completed" | "failed" | "cancelled")
                )
            }) {
                return;
            }
            thread::sleep(Duration::from_millis(10));
        }
        panic!("assistance did not become terminal");
    }

    #[test]
    fn candidate_parser_rejects_unbounded_or_stale_input() {
        assert_eq!(
            AssistanceCandidate::parse(&candidate())
                .expect("candidate")
                .request_id,
            "request-1"
        );
        let mut invalid = candidate();
        invalid["context_epoch"] = json!(0);
        assert!(AssistanceCandidate::parse(&invalid).is_none());
        invalid["context_epoch"] = json!(1);
        invalid["public_intent"] = json!("x".repeat(4_001));
        assert!(AssistanceCandidate::parse(&invalid).is_none());
    }

    #[test]
    fn assistance_request_is_bounded_and_network_fenced() {
        let parsed = AssistanceCandidate::parse(&candidate()).expect("candidate");
        let request = assistance_request(
            &parsed,
            &AssistanceSessionConfig {
                locale: "en-AU".to_owned(),
                online_assistance_enabled: false,
            },
            "conversation-1",
        );
        assert_eq!(request["conversation_id"], "conversation-1");
        assert_eq!(request["allow_network"], false);
        assert_eq!(request["observed_facts"], json!([]));
    }

    #[test]
    fn completed_assistance_delivers_only_spoken_summary_to_worker() {
        let core = Arc::new(FakeCore::with_responses(vec![
            Ok(json!({ "conversation_id": "conversation-1" })),
            Ok(json!({
                "status": "running",
                "revision": 1,
                "display_markdown": "# Full private answer",
            })),
            Ok(json!({
                "status": "completed",
                "revision": 2,
                "spoken_summary": "Use the east gate.",
                "display_markdown": "# Full private answer",
                "citations": [{ "title": "Guide", "url": "https://example.test" }],
            })),
        ]));
        let router = Arc::new(RealtimeAssistanceRouter::with_client(core.clone()));
        let worker = Arc::new(FakeWorker::default());
        let events = Arc::new(FakeEvents::default());
        router.attach_sink(
            "session-1",
            AssistanceSessionConfig {
                locale: "en-AU".to_owned(),
                online_assistance_enabled: true,
            },
            worker.clone(),
        );
        router.route_with_sink(candidate(), events.clone());
        wait_for_terminal(&events);

        let commands = worker.commands.lock().expect("commands");
        assert_eq!(commands.len(), 1);
        assert_eq!(commands[0]["type"], "assistance_result");
        assert_eq!(commands[0]["public_summary"], "Use the east gate.");
        assert!(commands[0].get("display_markdown").is_none());
        assert!(commands[0].get("citations").is_none());
        assert_eq!(
            core.calls.lock().expect("calls")[1].0,
            "realtime.assistance.request"
        );
    }

    #[test]
    fn online_assistance_is_denied_before_core_when_disabled() {
        let core = Arc::new(FakeCore::default());
        let router = Arc::new(RealtimeAssistanceRouter::with_client(core.clone()));
        let worker = Arc::new(FakeWorker::default());
        let events = Arc::new(FakeEvents::default());
        router.attach_sink(
            "session-1",
            AssistanceSessionConfig {
                locale: "en-AU".to_owned(),
                online_assistance_enabled: false,
            },
            worker.clone(),
        );
        router.route_with_sink(candidate(), events.clone());
        wait_for_terminal(&events);

        assert!(core.calls.lock().expect("calls").is_empty());
        assert_eq!(
            worker.commands.lock().expect("commands")[0]["succeeded"],
            false
        );
        assert!(events.values.lock().expect("events").iter().any(|event| {
            event["status"] == "failed" && event["error_code"] == "ASSISTANCE_NETWORK_DISABLED"
        }));
    }

    #[test]
    fn duplicate_request_is_coalesced_and_session_end_cancels_job() {
        let core = Arc::new(FakeCore::with_responses(vec![Ok(json!({
            "conversation_id": "conversation-1"
        }))]));
        let router = Arc::new(RealtimeAssistanceRouter::with_client(core.clone()));
        let worker = Arc::new(FakeWorker::default());
        let events = Arc::new(FakeEvents::default());
        router.attach_sink(
            "session-1",
            AssistanceSessionConfig {
                locale: "en-AU".to_owned(),
                online_assistance_enabled: true,
            },
            worker,
        );
        router.route_with_sink(candidate(), events.clone());
        router.route_with_sink(candidate(), events);
        thread::sleep(Duration::from_millis(40));
        assert_eq!(
            router.active_jobs.lock().expect("jobs").len(),
            1,
            "duplicate request must share one native job"
        );
        router.end_session("session-1");
        for _ in 0..40 {
            if router.active_jobs.lock().expect("jobs").is_empty() {
                break;
            }
            thread::sleep(Duration::from_millis(10));
        }
        assert!(router.active_jobs.lock().expect("jobs").is_empty());
    }

    #[test]
    fn same_session_worker_rotation_receives_the_inflight_result() {
        let core = Arc::new(FakeCore::with_responses(vec![
            Ok(json!({ "conversation_id": "conversation-1" })),
            Ok(json!({ "status": "running", "revision": 1 })),
            Ok(json!({
                "status": "completed",
                "revision": 2,
                "spoken_summary": "Rotation-safe result.",
            })),
        ]));
        let router = Arc::new(RealtimeAssistanceRouter::with_client(core));
        let first_worker = Arc::new(FakeWorker::default());
        let next_worker = Arc::new(FakeWorker::default());
        let events = Arc::new(FakeEvents::default());
        let config = AssistanceSessionConfig {
            locale: "en-AU".to_owned(),
            online_assistance_enabled: true,
        };
        router.attach_sink("session-1", config.clone(), first_worker.clone());
        router.route_with_sink(candidate(), events.clone());
        thread::sleep(Duration::from_millis(40));
        router.attach_sink("session-1", config, next_worker.clone());
        wait_for_terminal(&events);

        assert!(first_worker.commands.lock().expect("commands").is_empty());
        assert_eq!(
            next_worker.commands.lock().expect("commands")[0]["public_summary"],
            "Rotation-safe result."
        );
    }

    #[test]
    fn ending_a_session_revision_fences_core_cancellation() {
        let core = Arc::new(FakeCore::with_responses(vec![
            Ok(json!({ "conversation_id": "conversation-1" })),
            Ok(json!({ "status": "running", "revision": 7 })),
        ]));
        let router = Arc::new(RealtimeAssistanceRouter::with_client(core.clone()));
        router.attach_sink(
            "session-1",
            AssistanceSessionConfig {
                locale: "en-AU".to_owned(),
                online_assistance_enabled: true,
            },
            Arc::new(FakeWorker::default()),
        );
        router.route_with_sink(candidate(), Arc::new(FakeEvents::default()));
        for _ in 0..50 {
            if core.calls.lock().expect("calls").len() >= 2 {
                break;
            }
            thread::sleep(Duration::from_millis(10));
        }
        router.end_session("session-1");
        for _ in 0..50 {
            if core
                .calls
                .lock()
                .expect("calls")
                .iter()
                .any(|(method, _)| method == "realtime.assistance.cancel")
            {
                break;
            }
            thread::sleep(Duration::from_millis(10));
        }
        let calls = core.calls.lock().expect("calls");
        let cancellation = calls
            .iter()
            .find(|(method, _)| method == "realtime.assistance.cancel")
            .expect("cancel call");
        assert_eq!(cancellation.1["expected_revision"], 7);
    }

    #[test]
    fn worker_delivery_failure_does_not_discard_the_main_chat_answer() {
        let core = Arc::new(FakeCore::with_responses(vec![
            Ok(json!({ "conversation_id": "conversation-1" })),
            Ok(json!({
                "status": "completed",
                "revision": 2,
                "spoken_summary": "Answer ready.",
            })),
        ]));
        let router = Arc::new(RealtimeAssistanceRouter::with_client(core));
        router.attach_sink(
            "session-1",
            AssistanceSessionConfig {
                locale: "en-AU".to_owned(),
                online_assistance_enabled: true,
            },
            Arc::new(FailingWorker),
        );
        router.route_with_sink(candidate(), Arc::new(FakeEvents::default()));
        for _ in 0..50 {
            if router.active_jobs.lock().expect("jobs").is_empty() {
                break;
            }
            thread::sleep(Duration::from_millis(10));
        }

        let snapshot = router.snapshot(Some("session-1"));
        assert_eq!(snapshot[0].status, "failed");
        assert_eq!(
            snapshot[0].error_code.as_deref(),
            Some("ASSISTANCE_WORKER_UNAVAILABLE")
        );
        assert_eq!(
            snapshot[0].public_summary.as_deref(),
            Some("The answer is ready in the main chat.")
        );
    }
}
