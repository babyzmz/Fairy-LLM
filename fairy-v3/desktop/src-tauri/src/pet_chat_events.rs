use crate::pet_chat_broker::{PetChatBroker, PetChatContext};
use fairy_core_bridge::{CoreBridge, CoreEventSubscription};
use serde_json::{json, Value};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

const RPC_TIMEOUT: Duration = Duration::from_secs(2);
const RECOVERY_INTERVAL: Duration = Duration::from_secs(5);

pub struct PetChatEvents {
    stop: Arc<AtomicBool>,
    revision: Arc<AtomicU64>,
    worker: Option<JoinHandle<()>>,
}

impl PetChatEvents {
    pub fn start(
        core: Arc<Mutex<Option<CoreBridge>>>,
        broker: Arc<PetChatBroker>,
        publish: impl Fn(PetChatContext) + Send + 'static,
    ) -> Result<Self, String> {
        let stop = Arc::new(AtomicBool::new(false));
        let revision = Arc::new(AtomicU64::new(1));
        let worker_stop = stop.clone();
        let worker_revision = revision.clone();
        let worker = thread::Builder::new()
            .name("pet-core-events".into())
            .spawn(move || {
                run(core, broker, worker_stop, worker_revision, publish);
            })
            .map_err(|_| "PET_CHAT_EVENTS_UNAVAILABLE".to_owned())?;
        Ok(Self {
            stop,
            revision,
            worker: Some(worker),
        })
    }

    pub fn wake(&self) {
        self.revision.fetch_add(1, Ordering::Relaxed);
        if let Some(worker) = &self.worker {
            worker.thread().unpark();
        }
    }

    pub fn stop(&mut self) {
        self.stop.store(true, Ordering::Release);
        if let Some(worker) = self.worker.take() {
            worker.thread().unpark();
            let _ = worker.join();
        }
    }
}

impl Drop for PetChatEvents {
    fn drop(&mut self) {
        self.stop();
    }
}

fn call(bridge: &CoreBridge, method: &str, params: Value) -> Result<Value, ()> {
    let response = bridge
        .call_with_timeout(
            json!({"id": 0, "method": method, "params": params}),
            RPC_TIMEOUT,
        )
        .map_err(|_| ())?;
    if response.get("error").is_some() {
        return Err(());
    }
    response.get("result").cloned().ok_or(())
}

fn close(watcher: &mut Option<CoreEventSubscription>) {
    if let Some(subscription) = watcher.take() {
        subscription.cancellation().close_with_timeout(RPC_TIMEOUT);
    }
}

fn run(
    core: Arc<Mutex<Option<CoreBridge>>>,
    broker: Arc<PetChatBroker>,
    stop: Arc<AtomicBool>,
    wake: Arc<AtomicU64>,
    publish: impl Fn(PetChatContext),
) {
    let mut watcher = None;
    let mut connected = None;
    let mut last_binding = None;
    let mut last_wake = 0;
    let mut retry_at = Instant::now();
    let mut last_snapshot = Instant::now() - RECOVERY_INTERVAL;
    let mut dirty = true;
    while !stop.load(Ordering::Acquire) {
        let Ok(context) = broker.context() else {
            break;
        };
        if context.conversation_id.is_none() {
            close(&mut watcher);
            connected = None;
            thread::park_timeout(RECOVERY_INTERVAL);
            continue;
        }
        let wake_revision = wake.load(Ordering::Acquire);
        if last_binding != Some(context.revision) || last_wake != wake_revision {
            last_binding = Some(context.revision);
            last_wake = wake_revision;
            dirty = true;
            if watcher.is_none() {
                retry_at = Instant::now();
            }
        }
        if watcher.is_none() && Instant::now() >= retry_at {
            connected = core.lock().ok().and_then(|slot| slot.clone());
            if let Some(bridge) = &connected {
                watcher = call(bridge, "events.state", json!({}))
                    .ok()
                    .and_then(|state| state["latest_cursor"].as_u64())
                    .and_then(|cursor| {
                        bridge
                            .subscribe_events_with_timeout(cursor, RPC_TIMEOUT)
                            .ok()
                    });
            }
            retry_at = Instant::now() + RECOVERY_INTERVAL;
            dirty = true;
        }
        if dirty || (watcher.is_none() && last_snapshot.elapsed() >= RECOVERY_INTERVAL) {
            dirty = false;
            last_snapshot = Instant::now();
            if let Ok(Some(ticket)) = broker.prepare_snapshot() {
                let snapshot = connected.as_ref().ok_or(()).and_then(|bridge| {
                    call(
                        bridge,
                        "assistant.conversations.presentation.get",
                        json!({"conversation_id": ticket.conversation_id}),
                    )
                });
                match snapshot {
                    Ok(value) if broker.accept_snapshot(&ticket, &value) == Ok(true) => {
                        if let Ok(current) = broker.context() {
                            publish(current);
                        }
                    }
                    Err(()) => {
                        if broker.reject_snapshot(&ticket) == Ok(true) {
                            if let Ok(current) = broker.context() {
                                publish(current);
                            }
                        }
                        close(&mut watcher);
                        connected = None;
                    }
                    _ => {}
                }
            }
        }
        if stop.load(Ordering::Acquire) {
            break;
        }
        if let Some(subscription) = &watcher {
            match subscription.recv_timeout(Duration::from_millis(500)) {
                Ok(Some(batch)) => {
                    dirty = batch["items"].as_array().is_some_and(|items| {
                        items.iter().any(|event| {
                            event["conversation_id"]
                                .as_str()
                                .and_then(|id| id.parse().ok())
                                == context.conversation_id
                                && event["event_type"].as_str().is_some_and(|kind| {
                                    kind.starts_with("assistant.turn.")
                                        || kind == "assistant.message.persisted"
                                })
                        })
                    });
                }
                Ok(None) => {}
                Err(_) => {
                    close(&mut watcher);
                    connected = None;
                    retry_at = Instant::now();
                }
            }
        } else {
            thread::park_timeout(RECOVERY_INTERVAL);
        }
    }
    close(&mut watcher);
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::pet_chat_broker::{PetChatBindingInput, PetChatBroker};
    use fairy_core_bridge::{CoreBridge, CoreLaunchSpec};
    use serde_json::json;
    use std::path::PathBuf;
    use std::sync::{mpsc, Arc, Mutex};
    use std::time::{Duration, Instant};

    #[test]
    fn host_event_owner_updates_two_chats_and_releases_its_subscription_on_stop() {
        let data = tempfile::tempdir().unwrap();
        let core_root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../core");
        let mut launch = CoreLaunchSpec::development(&core_root, data.path());
        launch.current_dir = Some(core_root.canonicalize().unwrap());
        launch.args = vec![
            "-u".into(),
            "-c".into(),
            r#"
import os, sys
from pathlib import Path
from fairy_core.providers import ModelDelta, ProviderRegistry
from fairy_core.transports.stdio import build_local_dispatcher, process_stream
from tests.assistant.support import ScriptedProvider
rounds = [(ModelDelta.text(profile_id='scripted', sequence=1, text='Host reply'),
    ModelDelta.done(profile_id='scripted', sequence=2, finish_reason='stop')) for _ in range(2)]
dispatcher = build_local_dispatcher(Path(os.environ['FAIRY_V3_DATA_DIR']),
    provider_registry=ProviderRegistry((ScriptedProvider(rounds),)))
try:
    process_stream(dispatcher, sys.stdin, sys.stdout)
finally:
    dispatcher.close()
"#
            .into(),
        ];
        let bridge = CoreBridge::spawn_verified(launch.clone()).unwrap();
        let core = Arc::new(Mutex::new(Some(bridge.clone())));
        let broker = Arc::new(PetChatBroker::default());
        let (sender, receiver) = mpsc::channel();
        let mut owner = PetChatEvents::start(core.clone(), broker.clone(), move |context| {
            let _ = sender.send(context);
        })
        .unwrap();
        for index in 0..2 {
            let created = bridge
                .call(json!({"id": 1, "method": "assistant.commands.dispatch",
                "params": {"text": "/new", "idempotency_key": format!("event-chat:{index}")}}))
                .unwrap();
            let conversation = &created["result"]["conversation"];
            let id = serde_json::from_value(conversation["id"].clone()).unwrap();
            let input: PetChatBindingInput = serde_json::from_value(json!({
                "expected_revision": broker.context().unwrap().revision, "conversation_id": id,
                "profile_id": "scripted", "model_selection": null,
            }))
            .unwrap();
            let context = broker.bind(input, conversation).unwrap();
            let ticket = broker
                .prepare_submission(context.revision, &format!("event:{index}"), "Hello")
                .unwrap();
            let response = bridge
                .call(json!({"id": 2, "method": "assistant.messages.submit",
                "params": ticket.params}))
                .unwrap();
            broker
                .finish_submission(&ticket, &response["result"])
                .unwrap();
            owner.wake();
            let deadline = Instant::now() + Duration::from_secs(6);
            loop {
                let projected = receiver
                    .recv_timeout(deadline.saturating_duration_since(Instant::now()))
                    .unwrap();
                if projected.conversation_id == Some(id) {
                    if let Some(reply) = projected.reply {
                        assert_eq!(reply.text, "Host reply");
                        assert_eq!(projected.turn.unwrap().status, "completed");
                        assert!(projected.connection_available);
                        break;
                    }
                }
            }
        }
        while receiver.try_recv().is_ok() {}
        *core.lock().unwrap() = None;
        bridge.shutdown();
        owner.wake();
        let deadline = Instant::now() + Duration::from_secs(6);
        loop {
            let projected = receiver
                .recv_timeout(deadline.saturating_duration_since(Instant::now()))
                .unwrap();
            if !projected.connection_available {
                break;
            }
        }
        let restored = CoreBridge::spawn_verified(launch).unwrap();
        *core.lock().unwrap() = Some(restored.clone());
        owner.wake();
        let deadline = Instant::now() + Duration::from_secs(6);
        loop {
            let projected = receiver
                .recv_timeout(deadline.saturating_duration_since(Instant::now()))
                .unwrap();
            if projected.connection_available {
                assert_eq!(
                    projected.conversation_id,
                    broker.context().unwrap().conversation_id
                );
                assert_eq!(projected.reply.unwrap().text, "Host reply");
                break;
            }
        }
        owner.stop();
        let state = restored
            .call(json!({"id": 3, "method": "events.state", "params": {}}))
            .unwrap();
        let cursor = state["result"]["latest_cursor"].as_u64().unwrap();
        let mut watchers = Vec::new();
        // The host slot must have been released in Core as well as in Rust.
        for _ in 0..8 {
            watchers.push(restored.subscribe_events(cursor).unwrap());
        }
        drop(watchers);
    }
}
