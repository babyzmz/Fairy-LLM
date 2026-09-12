//! Process-local startup reservations; OS/CUDA remains the memory authority.
use serde::Serialize;
use std::sync::{Arc, Mutex};

#[derive(Clone, Default, Serialize)]
pub struct ModelReservationSnapshot {
    pub generation: u64,
    pub loading_model: Option<&'static str>,
    pub requested_bytes: Option<u64>,
    pub rejected_starts: u64,
}

#[derive(Default)]
pub struct ModelResources {
    state: Mutex<ModelReservationSnapshot>,
}

impl ModelResources {
    pub fn snapshot(&self) -> ModelReservationSnapshot {
        self.state
            .lock()
            .map(|state| state.clone())
            .unwrap_or_default()
    }

    pub fn reserve(
        self: &Arc<Self>,
        model: &'static str,
        memory: Option<(u64, u64)>,
    ) -> Result<ModelReservation, &'static str> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| "MODEL_RESOURCES_UNAVAILABLE")?;
        if state.loading_model.is_some() {
            state.rejected_starts = state.rejected_starts.saturating_add(1);
            return Err("MODEL_START_CAPACITY_EXCEEDED");
        }
        if memory.is_some_and(|(available, required)| required > available) {
            state.rejected_starts = state.rejected_starts.saturating_add(1);
            return Err("MODEL_INSUFFICIENT_VRAM");
        }
        state.generation = state.generation.saturating_add(1);
        state.loading_model = Some(model);
        state.requested_bytes = memory.map(|(_, required)| required);
        Ok(ModelReservation {
            manager: Arc::clone(self),
            generation: state.generation,
        })
    }
}

pub struct ModelReservation {
    manager: Arc<ModelResources>,
    generation: u64,
}

impl Drop for ModelReservation {
    fn drop(&mut self) {
        let Ok(mut state) = self.manager.state.lock() else {
            return;
        };
        if state.generation != self.generation {
            return;
        }
        state.loading_model = None;
        state.requested_bytes = None;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn admission_is_bounded_and_failed_memory_requests_do_not_reserve() {
        let manager = Arc::new(ModelResources::default());
        assert!(manager.reserve("omni", Some((1, 2))).is_err());
        assert!(manager.snapshot().loading_model.is_none());
        let reservation = manager.reserve("voice", None).unwrap();
        assert!(manager.reserve("omni", None).is_err());
        drop(reservation);
        assert!(manager.reserve("omni", Some((3, 2))).is_ok());
    }
}
