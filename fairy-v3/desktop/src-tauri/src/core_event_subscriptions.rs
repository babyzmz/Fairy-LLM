use fairy_core_bridge::{CoreEventCancellation, CoreEventSubscription};
use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};

struct Slot {
    id: String,
    subscription: Option<Arc<Mutex<CoreEventSubscription>>>,
    cancellation: Option<CoreEventCancellation>,
}

#[derive(Default)]
pub struct CoreEventSubscriptions {
    slots: Mutex<BTreeMap<String, Slot>>,
}

impl CoreEventSubscriptions {
    pub fn reserve(&self, owner: &str, id: &str) -> Result<(), String> {
        if id.is_empty()
            || id.len() > 128
            || !id
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || b"-_:".contains(&byte))
        {
            return Err("RPC_EVENT_SUBSCRIPTION_INVALID".to_owned());
        }
        let previous = {
            let mut slots = self.slots.lock().map_err(|_| "CORE_EVENT_LOCK")?;
            if !slots.contains_key(owner) && slots.len() >= 8 {
                return Err("RPC_CAPACITY_EXCEEDED".to_owned());
            }
            slots.insert(
                owner.to_owned(),
                Slot {
                    id: id.to_owned(),
                    subscription: None,
                    cancellation: None,
                },
            )
        };
        if let Some(cancel) = previous
            .as_ref()
            .and_then(|slot| slot.cancellation.as_ref())
        {
            cancel.close();
        }
        drop(previous);
        Ok(())
    }

    pub fn bind(
        &self,
        owner: &str,
        id: &str,
        subscription: CoreEventSubscription,
    ) -> Result<(), String> {
        let mut slots = self.slots.lock().map_err(|_| "CORE_EVENT_LOCK")?;
        let slot = slots
            .get_mut(owner)
            .filter(|slot| slot.id == id)
            .ok_or("RPC_EVENT_SUBSCRIPTION_CLOSED")?;
        slot.cancellation = Some(subscription.cancellation());
        slot.subscription = Some(Arc::new(Mutex::new(subscription)));
        Ok(())
    }

    pub fn get(&self, owner: &str, id: &str) -> Option<Arc<Mutex<CoreEventSubscription>>> {
        self.slots
            .lock()
            .ok()?
            .get(owner)
            .filter(|slot| slot.id == id)?
            .subscription
            .clone()
    }

    pub fn current_id(&self, owner: &str) -> Option<String> {
        self.slots
            .lock()
            .ok()?
            .get(owner)
            .map(|slot| slot.id.clone())
    }

    pub fn close(&self, owner: &str, id: &str) {
        let removed = if let Ok(mut slots) = self.slots.lock() {
            if slots.get(owner).is_some_and(|slot| slot.id == id) {
                slots.remove(owner)
            } else {
                None
            }
        } else {
            None
        };
        if let Some(cancel) = removed.as_ref().and_then(|slot| slot.cancellation.as_ref()) {
            cancel.close();
        }
        drop(removed);
    }
}

#[cfg(test)]
mod tests {
    use super::CoreEventSubscriptions;

    #[test]
    fn stale_close_and_other_window_cannot_remove_current_subscription() {
        let hub = CoreEventSubscriptions::default();
        hub.reserve("main", "old").unwrap();
        hub.reserve("second", "other").unwrap();
        hub.reserve("main", "new").unwrap();
        hub.close("main", "old");
        hub.close("second", "new");
        assert_eq!(hub.current_id("main").as_deref(), Some("new"));
        assert_eq!(hub.current_id("second").as_deref(), Some("other"));
        hub.close("main", "new");
        assert!(hub.current_id("main").is_none());
        assert!(hub.current_id("second").is_some());
    }
}
