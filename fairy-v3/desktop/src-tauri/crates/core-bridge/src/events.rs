use super::{CoreBridge, CoreBridgeError};
use serde_json::Value;
use std::collections::BTreeMap;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::time::Duration;

pub(super) struct EventSink {
    sender: mpsc::SyncSender<Value>,
    overflow: Arc<AtomicBool>,
}

pub(super) type EventSinks = Arc<Mutex<BTreeMap<String, EventSink>>>;

pub struct CoreEventSubscription {
    bridge: CoreBridge,
    key: String,
    receiver: mpsc::Receiver<Value>,
    overflow: Arc<AtomicBool>,
}

#[derive(Clone)]
pub struct CoreEventCancellation {
    bridge: CoreBridge,
    key: String,
}

impl CoreEventCancellation {
    pub fn close(&self) {
        self.close_with_timeout(Duration::from_secs(30));
    }

    pub fn close_with_timeout(&self, timeout: Duration) {
        let removed = self
            .bridge
            .process
            .event_sinks
            .lock()
            .map(|mut sinks| sinks.remove(&self.key).is_some())
            .unwrap_or(false);
        if removed {
            let _ = self.bridge.call_with_timeout(serde_json::json!({"id": 0, "method": "events.unwatch", "params": {"subscription_id": self.key}}), timeout);
        }
    }
}

impl CoreEventSubscription {
    pub fn cancellation(&self) -> CoreEventCancellation {
        CoreEventCancellation {
            bridge: self.bridge.clone(),
            key: self.key.clone(),
        }
    }
    pub fn recv_timeout(&self, timeout: Duration) -> Result<Option<Value>, CoreBridgeError> {
        if self.overflow.load(Ordering::Acquire) {
            return Err(CoreBridgeError::EventResyncRequired);
        }
        match self.receiver.recv_timeout(timeout) {
            Ok(value) if value.get("resync_required").and_then(Value::as_bool) == Some(true) => {
                self.overflow.store(true, Ordering::Release);
                Err(CoreBridgeError::EventResyncRequired)
            }
            Ok(value) => Ok(Some(value)),
            Err(mpsc::RecvTimeoutError::Timeout) => Ok(None),
            Err(mpsc::RecvTimeoutError::Disconnected) => Err(CoreBridgeError::WorkerInterrupted),
        }
    }
}

impl Drop for CoreEventSubscription {
    fn drop(&mut self) {
        self.cancellation().close();
    }
}

impl CoreBridge {
    pub fn subscribe_events(&self, cursor: u64) -> Result<CoreEventSubscription, CoreBridgeError> {
        self.subscribe_events_with_timeout(cursor, Duration::from_secs(30))
    }

    pub fn subscribe_events_with_timeout(
        &self,
        cursor: u64,
        timeout: Duration,
    ) -> Result<CoreEventSubscription, CoreBridgeError> {
        if !self.process.event_notifications.load(Ordering::Acquire) {
            return Err(CoreBridgeError::EventsUnavailable);
        }
        let key = format!(
            "watch:{}:{}",
            self.process.generation,
            self.process.sequence.fetch_add(1, Ordering::Relaxed)
        );
        let (sender, receiver) = mpsc::sync_channel(8);
        let overflow = Arc::new(AtomicBool::new(false));
        {
            let mut sinks = self
                .process
                .event_sinks
                .lock()
                .map_err(|_| CoreBridgeError::LockPoisoned)?;
            if sinks.len() >= 8 {
                return Err(CoreBridgeError::CapacityExceeded);
            }
            sinks.insert(
                key.clone(),
                EventSink {
                    sender,
                    overflow: Arc::clone(&overflow),
                },
            );
        }
        let subscription = CoreEventSubscription {
            bridge: self.clone(),
            key: key.clone(),
            receiver,
            overflow,
        };
        let response = match self.call_with_timeout(serde_json::json!({"id": 0, "method": "events.watch", "params": {"subscription_id": key, "cursor": cursor}}), timeout) {
            Ok(response) => response,
            Err(error) => {
                subscription.cancellation().close_with_timeout(timeout);
                return Err(error);
            }
        };
        if response.get("error").is_some() {
            subscription.cancellation().close_with_timeout(timeout);
            if response
                .pointer("/error/data/error_code")
                .and_then(Value::as_str)
                == Some("RPC_EVENT_RESYNC_REQUIRED")
            {
                return Err(CoreBridgeError::EventResyncRequired);
            }
            return Err(CoreBridgeError::EventsUnavailable);
        }
        Ok(subscription)
    }
}

pub(super) fn dispatch(sinks: &EventSinks, response: &Value) -> bool {
    if response.get("method").and_then(Value::as_str) != Some("events.changed")
        || response.get("id").is_some()
    {
        return false;
    }
    if let Some(params) = response.get("params") {
        if let Some(key) = params.get("subscription_id").and_then(Value::as_str) {
            if let Ok(mut sinks) = sinks.lock() {
                if let Some(sink) = sinks.get(key) {
                    match sink.sender.try_send(params.clone()) {
                        Err(mpsc::TrySendError::Full(_)) => {
                            sink.overflow.store(true, Ordering::Release)
                        }
                        Err(mpsc::TrySendError::Disconnected(_)) => {
                            sinks.remove(key);
                        }
                        Ok(()) => {}
                    }
                }
            }
        }
    }
    true
}
