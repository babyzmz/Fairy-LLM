//! Host-owned audio arbitration. A WebView cannot grant itself realtime priority.
use std::sync::{Arc, Mutex};

use serde::Serialize;
use tauri::{AppHandle, Emitter};

pub const AUDIO_FOCUS_EVENT: &str = "fairy-audio-focus";

#[derive(Clone, Copy, Default, Serialize)]
pub struct AudioFocusSnapshot {
    pub sequence: u64,
    pub realtime_active: bool,
}

#[derive(Default)]
pub struct AudioFocus {
    pub model_resources: Arc<crate::model_resources::ModelResources>,
    state: Mutex<AudioFocusSnapshot>,
    app: Option<AppHandle>,
}

impl AudioFocus {
    pub fn with_app(app: AppHandle) -> Self {
        Self {
            app: Some(app),
            ..Self::default()
        }
    }

    pub fn snapshot(&self) -> AudioFocusSnapshot {
        self.state
            .lock()
            .map(|state| *state)
            .unwrap_or(AudioFocusSnapshot {
                sequence: u64::MAX,
                realtime_active: true,
            })
    }

    pub fn reserve_realtime(self: &Arc<Self>) -> Result<RealtimeAudioLease, &'static str> {
        let snapshot = {
            let mut state = self.state.lock().map_err(|_| "AUDIO_FOCUS_UNAVAILABLE")?;
            if state.realtime_active {
                return Err("AUDIO_FOCUS_BUSY");
            }
            state.sequence = state.sequence.saturating_add(1);
            state.realtime_active = true;
            *state
        };
        self.emit(snapshot);
        Ok(RealtimeAudioLease {
            focus: Arc::clone(self),
            generation: snapshot.sequence,
        })
    }

    pub fn admit(&self, realtime: bool) -> Result<u64, &'static str> {
        let state = self.state.lock().map_err(|_| "AUDIO_FOCUS_UNAVAILABLE")?;
        if state.realtime_active != realtime {
            return Err("AUDIO_FOCUS_UNAVAILABLE");
        }
        Ok(state.sequence)
    }

    pub fn is_current(&self, generation: u64, realtime: bool) -> bool {
        self.state
            .lock()
            .is_ok_and(|state| state.sequence == generation && state.realtime_active == realtime)
    }

    fn emit(&self, snapshot: AudioFocusSnapshot) {
        if let Some(app) = &self.app {
            let _ = app.emit(AUDIO_FOCUS_EVENT, snapshot);
        }
    }
}

pub struct RealtimeAudioLease {
    focus: Arc<AudioFocus>,
    generation: u64,
}

impl Drop for RealtimeAudioLease {
    fn drop(&mut self) {
        let snapshot = {
            let Ok(mut state) = self.focus.state.lock() else {
                return;
            };
            if state.sequence != self.generation || !state.realtime_active {
                return;
            }
            state.sequence = state.sequence.saturating_add(1);
            state.realtime_active = false;
            *state
        };
        self.focus.emit(snapshot);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn realtime_preempts_old_audio_and_old_generations_never_resume() {
        let focus = Arc::new(AudioFocus::default());
        let old = focus.admit(false).unwrap();
        let lease = focus.reserve_realtime().unwrap();
        assert!(!focus.is_current(old, false));
        assert!(focus.admit(false).is_err());
        assert!(focus.reserve_realtime().is_err());
        let realtime = focus.admit(true).unwrap();
        let other_host = Arc::new(AudioFocus::default());
        assert!(other_host.admit(false).is_ok());
        drop(lease);
        assert!(!focus.is_current(realtime, true));
        assert!(!focus.is_current(old, false));
        assert!(focus.admit(false).is_ok());
    }
}
