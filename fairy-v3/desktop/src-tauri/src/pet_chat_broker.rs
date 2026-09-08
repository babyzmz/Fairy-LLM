use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::sync::Mutex;
use uuid::Uuid;

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PetModelSelection {
    mode: String,
    model_id: Option<String>,
    revision: u64,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PetChatBindingInput {
    pub expected_revision: u64,
    pub conversation_id: Uuid,
    pub profile_id: Option<String>,
    pub model_selection: Option<PetModelSelection>,
}

#[derive(Clone, Debug, Default, Serialize)]
pub struct PetChatContext {
    pub revision: u64,
    pub conversation_id: Option<Uuid>,
    pub turn: Option<PetTurnProjection>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct PetTurnProjection {
    pub id: Uuid,
    pub conversation_id: Uuid,
    pub cancellation_revision: u64,
    pub cancellation_pending: bool,
    pub status: String,
}

#[derive(Clone, Debug)]
pub struct PreparedPetSubmission {
    pub revision: u64,
    pub submission_id: String,
    pub params: Value,
}

#[derive(Default)]
struct BrokerState {
    context: PetChatContext,
    profile_id: Option<String>,
    model_selection: Option<PetModelSelection>,
    pending: Option<PreparedPetSubmission>,
}

#[derive(Default)]
pub struct PetChatBroker {
    state: Mutex<BrokerState>,
}

impl PetChatBroker {
    pub fn context(&self) -> Result<PetChatContext, String> {
        Ok(self
            .state
            .lock()
            .map_err(|_| "PET_CHAT_LOCK")?
            .context
            .clone())
    }

    pub fn bind(
        &self,
        input: PetChatBindingInput,
        conversation: &Value,
    ) -> Result<PetChatContext, String> {
        if conversation.get("id").and_then(Value::as_str)
            != Some(input.conversation_id.to_string().as_str())
            || conversation.get("workspace_type").and_then(Value::as_str) != Some("chat_scratch")
            || conversation.get("project_id") != Some(&Value::Null)
            || ["deleted_at", "deleted_by_project_at", "purged_at"]
                .iter()
                .any(|field| {
                    conversation
                        .get(field)
                        .is_some_and(|value| !value.is_null())
                })
        {
            return Err("PET_CHAT_SCOPE_MISMATCH".into());
        }
        if input.profile_id.is_some() == input.model_selection.is_some()
            || input
                .profile_id
                .as_ref()
                .is_some_and(|id| id.trim().is_empty() || id.len() > 255)
            || input.model_selection.as_ref().is_some_and(|selection| {
                match selection.mode.as_str() {
                    "auto" => selection.model_id.is_some(),
                    "manual" => !selection
                        .model_id
                        .as_ref()
                        .is_some_and(|id| !id.trim().is_empty() && id.len() <= 255),
                    _ => true,
                }
            })
        {
            return Err("PET_CHAT_MODEL_BINDING_INVALID".into());
        }
        let mut state = self.state.lock().map_err(|_| "PET_CHAT_LOCK")?;
        if state.context.revision != input.expected_revision {
            return Err("PET_CHAT_BINDING_CHANGED".into());
        }
        if state.context.conversation_id == Some(input.conversation_id)
            && state.profile_id == input.profile_id
            && state.model_selection == input.model_selection
        {
            return Ok(state.context.clone());
        }
        let revision = state
            .context
            .revision
            .checked_add(1)
            .ok_or("PET_CHAT_REVISION_EXHAUSTED")?;
        if state.context.conversation_id != Some(input.conversation_id) {
            state.context.turn = None;
        }
        state.context.revision = revision;
        state.context.conversation_id = Some(input.conversation_id);
        state.profile_id = input.profile_id;
        state.model_selection = input.model_selection;
        state.pending = None;
        Ok(state.context.clone())
    }

    pub fn prepare_submission(
        &self,
        revision: u64,
        submission_id: &str,
        text: &str,
    ) -> Result<PreparedPetSubmission, String> {
        let text = text.trim();
        if text.is_empty()
            || text.chars().count() > 4_000
            || submission_id.is_empty()
            || submission_id.len() > 128
            || !submission_id
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || b"-_:".contains(&byte))
        {
            return Err("PET_CHAT_INPUT_INVALID".into());
        }
        let mut state = self.state.lock().map_err(|_| "PET_CHAT_LOCK")?;
        if state.context.revision != revision {
            return Err("PET_CHAT_BINDING_CHANGED".into());
        }
        let conversation_id = state.context.conversation_id.ok_or("PET_CHAT_NOT_BOUND")?;
        let params = json!({
            "conversation_id": conversation_id, "content": text, "source": "pet",
            "profile_id": state.profile_id, "model_selection": state.model_selection,
            "idempotency_key": format!("pet:{submission_id}")
        });
        if let Some(pending) = &state.pending {
            return if pending.submission_id == submission_id && pending.params == params {
                Ok(pending.clone())
            } else {
                Err("PET_CHAT_SUBMISSION_BUSY".into())
            };
        }
        let prepared = PreparedPetSubmission {
            revision,
            submission_id: submission_id.into(),
            params,
        };
        state.pending = Some(prepared.clone());
        Ok(prepared)
    }

    pub fn finish_submission(
        &self,
        ticket: &PreparedPetSubmission,
        value: &Value,
    ) -> Result<bool, String> {
        let mut state = self.state.lock().map_err(|_| "PET_CHAT_LOCK")?;
        if state.context.revision != ticket.revision
            || state
                .pending
                .as_ref()
                .is_none_or(|pending| pending.submission_id != ticket.submission_id)
        {
            return Ok(false);
        }
        let turn = scoped_turn(value, state.context.conversation_id)?;
        state.context.turn = Some(turn);
        state.pending = None;
        Ok(true)
    }

    pub fn release_submission(&self, ticket: &PreparedPetSubmission) {
        if let Ok(mut state) = self.state.lock() {
            if state.context.revision == ticket.revision
                && state
                    .pending
                    .as_ref()
                    .is_some_and(|pending| pending.submission_id == ticket.submission_id)
            {
                state.pending = None;
            }
        }
    }

    pub fn accept_turn(&self, revision: u64, value: &Value) -> Result<bool, String> {
        let mut state = self.state.lock().map_err(|_| "PET_CHAT_LOCK")?;
        if state.context.revision != revision {
            return Ok(false);
        }
        let turn = scoped_turn(value, state.context.conversation_id)?;
        let Some(current) = &state.context.turn else {
            return Ok(false);
        };
        if current.id != turn.id || current.cancellation_revision > turn.cancellation_revision {
            return Ok(false);
        }
        if !current.cancellation_pending
            && ["completed", "cancelled", "failed"].contains(&current.status.as_str())
            && (turn.status != current.status || turn.cancellation_pending)
        {
            return Ok(false);
        }
        state.context.turn = Some(turn);
        Ok(true)
    }

    pub fn cancel_request(&self, revision: u64) -> Result<Value, String> {
        let state = self.state.lock().map_err(|_| "PET_CHAT_LOCK")?;
        if state.context.revision != revision {
            return Err("PET_CHAT_BINDING_CHANGED".into());
        }
        let turn = state
            .context
            .turn
            .as_ref()
            .ok_or("PET_CHAT_NO_ACTIVE_TURN")?;
        if turn.cancellation_pending
            || ["completed", "cancelled", "failed"].contains(&turn.status.as_str())
        {
            return Err("PET_CHAT_NO_ACTIVE_TURN".into());
        }
        Ok(
            json!({"turn_id": turn.id, "expected_cancellation_revision": turn.cancellation_revision}),
        )
    }
}

fn scoped_turn(value: &Value, conversation_id: Option<Uuid>) -> Result<PetTurnProjection, String> {
    let turn: PetTurnProjection =
        serde_json::from_value(value.clone()).map_err(|_| "PET_CHAT_RESPONSE_INVALID")?;
    if Some(turn.conversation_id) != conversation_id {
        return Err("PET_CHAT_SCOPE_MISMATCH".into());
    }
    if ![
        "created",
        "running",
        "waiting_for_tool",
        "waiting_for_input",
        "completed",
        "cancelled",
        "failed",
    ]
    .contains(&turn.status.as_str())
    {
        return Err("PET_CHAT_RESPONSE_INVALID".into());
    }
    Ok(turn)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use uuid::Uuid;

    fn input(revision: u64, conversation_id: Uuid) -> PetChatBindingInput {
        PetChatBindingInput {
            expected_revision: revision,
            conversation_id,
            profile_id: Some("configured-profile".into()),
            model_selection: None,
        }
    }

    fn conversation(id: Uuid) -> Value {
        json!({"id": id, "workspace_type": "chat_scratch", "project_id": null})
    }

    #[test]
    fn pet_submission_is_bound_to_host_context_not_caller_supplied_rpc() {
        let broker = PetChatBroker::default();
        let id = Uuid::new_v4();
        let context = broker.bind(input(0, id), &conversation(id)).unwrap();
        let request = broker
            .prepare_submission(context.revision, "submit-once", "  Hello  ")
            .unwrap();
        assert_eq!(
            request.params,
            json!({
                "conversation_id": id, "content": "Hello", "source": "pet",
                "profile_id": "configured-profile", "model_selection": null,
                "idempotency_key": "pet:submit-once"
            })
        );
        assert!(broker.prepare_submission(0, "late", "old scope").is_err());
    }

    #[test]
    fn late_results_and_old_binds_cannot_replace_another_chat() {
        let broker = PetChatBroker::default();
        let first = Uuid::new_v4();
        let second = Uuid::new_v4();
        let old = broker.bind(input(0, first), &conversation(first)).unwrap();
        let old_ticket = broker
            .prepare_submission(old.revision, "first", "Hello")
            .unwrap();
        let current = broker
            .bind(input(old.revision, second), &conversation(second))
            .unwrap();
        assert!(broker
            .bind(input(old.revision, first), &conversation(first))
            .is_err());
        let turn_id = Uuid::new_v4();
        let turn = json!({"id": turn_id, "conversation_id": first,
            "cancellation_revision": 0, "cancellation_pending": false, "status": "running"});
        assert!(!broker.finish_submission(&old_ticket, &turn).unwrap());
        assert!(broker.cancel_request(current.revision).is_err());
        assert!(broker.accept_turn(current.revision, &turn).is_err());
        let valid = json!({"id": turn_id, "conversation_id": second,
            "cancellation_revision": 2, "cancellation_pending": false, "status": "running"});
        let ticket = broker
            .prepare_submission(current.revision, "second", "Hello")
            .unwrap();
        assert!(broker.finish_submission(&ticket, &valid).unwrap());
        assert_eq!(
            broker.cancel_request(current.revision).unwrap(),
            json!({
                "turn_id": turn_id, "expected_cancellation_revision": 2
            })
        );
        assert_eq!(broker.context().unwrap().conversation_id, Some(second));
    }

    #[test]
    fn binding_rejects_projects_wrong_core_identity_and_missing_model_source() {
        let broker = PetChatBroker::default();
        let id = Uuid::new_v4();
        assert!(broker
            .bind(input(0, id), &conversation(Uuid::new_v4()))
            .is_err());
        assert!(broker
            .bind(
                input(0, id),
                &json!({
                    "id": id, "workspace_type": "project_chat", "project_id": Uuid::new_v4()
                })
            )
            .is_err());
        let mut missing = input(0, id);
        missing.profile_id = None;
        assert!(broker.bind(missing, &conversation(id)).is_err());
        assert_eq!(broker.context().unwrap().revision, 0);
        assert!(broker.prepare_submission(0, "no-binding", "hello").is_err());
    }

    #[test]
    fn deleted_chats_cannot_be_rebound_as_pet_input_targets() {
        let broker = PetChatBroker::default();
        let id = Uuid::new_v4();
        for field in ["deleted_at", "deleted_by_project_at", "purged_at"] {
            let mut unavailable = conversation(id);
            unavailable[field] = json!("2026-09-09T00:00:00Z");
            assert!(broker.bind(input(0, id), &unavailable).is_err());
        }
        assert_eq!(broker.context().unwrap().revision, 0);
    }

    #[test]
    fn a_late_stopping_projection_cannot_reopen_a_settled_turn() {
        let broker = PetChatBroker::default();
        let id = Uuid::new_v4();
        let context = broker.bind(input(0, id), &conversation(id)).unwrap();
        let ticket = broker
            .prepare_submission(context.revision, "stop", "Hello")
            .unwrap();
        let turn_id = Uuid::new_v4();
        let mut stopping = json!({"id": turn_id, "conversation_id": id,
            "cancellation_revision": 1, "cancellation_pending": true, "status": "cancelled"});
        broker.finish_submission(&ticket, &stopping).unwrap();
        stopping["cancellation_pending"] = json!(false);
        assert!(broker.accept_turn(context.revision, &stopping).unwrap());
        stopping["cancellation_pending"] = json!(true);
        assert!(!broker.accept_turn(context.revision, &stopping).unwrap());
        assert!(!broker.context().unwrap().turn.unwrap().cancellation_pending);
    }

    #[test]
    fn old_release_cannot_clear_a_new_submission_after_rebinding() {
        let broker = PetChatBroker::default();
        let first = Uuid::new_v4();
        let second = Uuid::new_v4();
        let context = broker.bind(input(0, first), &conversation(first)).unwrap();
        let old = broker
            .prepare_submission(context.revision, "old", "Hello")
            .unwrap();
        let current = broker
            .bind(input(context.revision, second), &conversation(second))
            .unwrap();
        let new = broker
            .prepare_submission(current.revision, "new", "New request")
            .unwrap();
        broker.release_submission(&old);
        assert!(broker
            .prepare_submission(current.revision, "third", "Other request")
            .is_err());
        broker.release_submission(&new);
        assert!(broker
            .prepare_submission(current.revision, "third", "Other request")
            .is_ok());
    }

    #[test]
    fn host_binding_and_submission_round_trip_through_real_core_without_a_frontend_owner() {
        use fairy_core_bridge::{CoreBridge, CoreLaunchSpec};
        use std::path::PathBuf;
        use std::time::{Duration, Instant};

        let core_root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../core");
        let data = tempfile::tempdir().unwrap();
        let mut launch = CoreLaunchSpec::development(&core_root, data.path());
        launch.current_dir = Some(core_root.canonicalize().unwrap());
        launch.args = vec!["-u".into(), "-c".into(), r#"
import os, sys
from pathlib import Path
from fairy_core.providers import ModelDelta, ProviderRegistry
from fairy_core.transports.stdio import build_local_dispatcher, process_stream
from tests.assistant.support import ScriptedProvider
rounds = [(
    ModelDelta.text(profile_id='configured-profile', sequence=1, text='Host-owned reply'),
    ModelDelta.done(profile_id='configured-profile', sequence=2, finish_reason='stop'),
) for _ in range(2)]
dispatcher = build_local_dispatcher(
    Path(os.environ['FAIRY_V3_DATA_DIR']),
    provider_registry=ProviderRegistry((ScriptedProvider(rounds, profile_id='configured-profile'),)),
)
try:
    process_stream(dispatcher, sys.stdin, sys.stdout)
finally:
    dispatcher.close()
"#.into()];
        let bridge = CoreBridge::spawn_verified(launch).unwrap();
        let broker = PetChatBroker::default();
        let mut revision = 0;
        for index in 0..2 {
            let created = bridge
                .call(json!({"id": 1, "method": "conversations.create",
                "params": {"project_id": null, "workspace_type": "chat_scratch"}}))
                .unwrap();
            let conversation = &created["result"];
            let id: Uuid = serde_json::from_value(conversation["id"].clone()).unwrap();
            let context = broker.bind(input(revision, id), conversation).unwrap();
            revision = context.revision;
            // Only a frontend handle disappears: host connection/binding remains owned here.
            let frontend_handle = bridge.clone();
            drop(frontend_handle);
            let ticket = broker
                .prepare_submission(revision, &format!("real-{index}"), "Hello")
                .unwrap();
            let response = bridge
                .call(json!({"id": 2, "method": "assistant.messages.submit",
                "params": ticket.params}))
                .unwrap();
            assert!(
                response.get("error").is_none(),
                "Core rejected the bound submission"
            );
            broker
                .finish_submission(&ticket, &response["result"])
                .unwrap();
            let turn_id = &response["result"]["id"];
            let deadline = Instant::now() + Duration::from_secs(5);
            loop {
                let current = bridge
                    .call(json!({"id": 3, "method": "assistant.turns.get",
                    "params": {"turn_id": turn_id}}))
                    .unwrap();
                broker.accept_turn(revision, &current["result"]).unwrap();
                if current["result"]["status"] == "completed" {
                    break;
                }
                assert!(Instant::now() < deadline, "host Turn did not settle");
                std::thread::sleep(Duration::from_millis(20));
            }
            let transcript = bridge
                .call(json!({"id": 4, "method": "messages.list",
                "params": {"conversation_id": id, "limit": 100}}))
                .unwrap();
            let items = transcript["result"]["items"].as_array().unwrap();
            assert_eq!(items.len(), 2);
            assert_eq!(items[0]["content"], "Hello");
            assert_eq!(items[1]["content"], "Host-owned reply");
            assert_eq!(broker.context().unwrap().conversation_id, Some(id));
        }
    }
}
