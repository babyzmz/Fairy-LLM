use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::VecDeque;
use std::sync::Mutex;
use uuid::Uuid;

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PetModelSelection {
    mode: String,
    model_id: Option<String>,
    revision: u64,
}

impl PetModelSelection {
    fn valid(&self) -> bool {
        match self.mode.as_str() {
            "auto" => self.model_id.is_none(),
            "manual" => self
                .model_id
                .as_ref()
                .is_some_and(|id| !id.trim().is_empty() && id.len() <= 255),
            _ => false,
        }
    }
}

pub struct PreparedPetChat {
    revision: u64,
    model_selection: PetModelSelection,
    pub params: Value,
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
    pub projection_revision: u64,
    pub conversation_id: Option<Uuid>,
    pub turn: Option<PetTurnProjection>,
    pub reply: Option<PetReplyProjection>,
    pub connection_available: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PetReplyProjection {
    pub id: Uuid,
    pub text: String,
}

#[derive(Clone, Debug)]
pub struct PetSnapshotTicket {
    pub revision: u64,
    pub conversation_id: Uuid,
    sequence: u64,
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
    snapshot_sequence: u64,
    applied_snapshot: u64,
    active_submission: Option<String>,
    owned_submissions: VecDeque<OwnedPetSubmission>,
}

struct OwnedPetSubmission {
    id: String,
    revision: u64,
    conversation_id: Option<Uuid>,
}

#[derive(Default)]
pub struct PetChatBroker {
    state: Mutex<BrokerState>,
}

impl PetChatBroker {
    // Register before the first Core await, including creation of an unbound chat.
    // This is a bounded host identity cache; the cancellation fact belongs to Core.
    pub fn begin_submission(&self, revision: u64, id: &str, text: &str) -> Result<(), String> {
        if !valid_request_id(id) || text.trim().is_empty() || text.trim().chars().count() > 4_000 {
            return Err("PET_CHAT_INPUT_INVALID".into());
        }
        let mut state = self.state.lock().map_err(|_| "PET_CHAT_LOCK")?;
        if state.context.revision != revision {
            return Err("PET_CHAT_BINDING_CHANGED".into());
        }
        if state.active_submission.is_some() {
            return Err("PET_CHAT_SUBMISSION_BUSY".into());
        }
        if state.owned_submissions.iter().any(|owned| owned.id == id) {
            return Err("PET_CHAT_SUBMISSION_ID_REUSED".into());
        }
        let conversation_id = state.context.conversation_id;
        if state.owned_submissions.len() == 32 {
            state.owned_submissions.pop_front();
        }
        state.owned_submissions.push_back(OwnedPetSubmission {
            id: id.into(),
            revision,
            conversation_id,
        });
        state.active_submission = Some(id.into());
        Ok(())
    }

    pub fn end_submission(&self, id: &str) {
        if let Ok(mut state) = self.state.lock() {
            if state.active_submission.as_deref() == Some(id) {
                state.active_submission = None;
            }
        }
    }

    pub fn submission_cancel_request(&self, revision: u64, id: &str) -> Result<Value, String> {
        let state = self.state.lock().map_err(|_| "PET_CHAT_LOCK")?;
        let owned = state
            .owned_submissions
            .iter()
            .find(|owned| owned.id == id && owned.revision == revision)
            .ok_or("PET_CHAT_SUBMISSION_NOT_OWNED")?;
        Ok(json!({"idempotency_key": format!("pet:{}", owned.id),
            "conversation_id": owned.conversation_id}))
    }

    pub fn prepare_new_chat(
        &self,
        revision: u64,
        request_id: &str,
        preference: &Value,
    ) -> Result<PreparedPetChat, String> {
        if !valid_request_id(request_id) {
            return Err("PET_CHAT_INPUT_INVALID".into());
        }
        let selection: PetModelSelection = serde_json::from_value(json!({
            "mode": preference["mode"], "model_id": preference["model_id"], "revision": preference["revision"],
        })).map_err(|_| "PET_CHAT_MODEL_BINDING_INVALID")?;
        if !selection.valid() {
            return Err("PET_CHAT_MODEL_BINDING_INVALID".into());
        }
        if self.context()?.revision != revision {
            return Err("PET_CHAT_BINDING_CHANGED".into());
        }
        Ok(PreparedPetChat {
            revision,
            model_selection: selection,
            params: json!({"text": "/new", "idempotency_key": format!("pet-new:{request_id}")}),
        })
    }

    pub fn finish_new_chat(
        &self,
        ticket: &PreparedPetChat,
        conversation: &Value,
    ) -> Result<PetChatContext, String> {
        let conversation_id = serde_json::from_value(conversation["id"].clone())
            .map_err(|_| "PET_CHAT_RESPONSE_INVALID")?;
        let current = self.context()?;
        if current.conversation_id == Some(conversation_id) {
            return Ok(current);
        }
        self.bind(
            PetChatBindingInput {
                expected_revision: ticket.revision,
                conversation_id,
                profile_id: None,
                model_selection: Some(ticket.model_selection.clone()),
            },
            conversation,
        )
    }

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
            || input
                .model_selection
                .as_ref()
                .is_some_and(|selection| !selection.valid())
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
        advance_projection(&mut state.context)?;
        if state.context.conversation_id != Some(input.conversation_id) {
            state.context.turn = None;
            state.context.reply = None;
            state.context.connection_available = false;
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
        if text.is_empty() || text.chars().count() > 4_000 || !valid_request_id(submission_id) {
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
        advance_projection(&mut state.context)?;
        state.applied_snapshot = state.snapshot_sequence;
        state.context.reply = None;
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
        advance_projection(&mut state.context)?;
        state.context.turn = Some(turn);
        state.pending = None;
        state.context.connection_available = true;
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
        advance_projection(&mut state.context)?;
        state.context.turn = Some(turn);
        state.applied_snapshot = state.snapshot_sequence;
        state.context.connection_available = true;
        Ok(true)
    }

    pub fn prepare_snapshot(&self) -> Result<Option<PetSnapshotTicket>, String> {
        let mut state = self.state.lock().map_err(|_| "PET_CHAT_LOCK")?;
        let Some(conversation_id) = state.context.conversation_id else {
            return Ok(None);
        };
        if state.pending.is_some() {
            return Ok(None);
        }
        state.snapshot_sequence = state
            .snapshot_sequence
            .checked_add(1)
            .ok_or("PET_CHAT_REVISION_EXHAUSTED")?;
        Ok(Some(PetSnapshotTicket {
            revision: state.context.revision,
            conversation_id,
            sequence: state.snapshot_sequence,
        }))
    }

    pub fn accept_snapshot(
        &self,
        ticket: &PetSnapshotTicket,
        value: &Value,
    ) -> Result<bool, String> {
        let mut state = self.state.lock().map_err(|_| "PET_CHAT_LOCK")?;
        if state.context.revision != ticket.revision
            || state.pending.is_some()
            || ticket.sequence <= state.applied_snapshot
        {
            return Ok(false);
        }
        let conversation_id: Uuid = serde_json::from_value(value["conversation_id"].clone())
            .map_err(|_| "PET_CHAT_RESPONSE_INVALID")?;
        if conversation_id != ticket.conversation_id {
            return Err("PET_CHAT_SCOPE_MISMATCH".into());
        }
        let turn = if value["turn"].is_null() {
            None
        } else {
            Some(scoped_turn(&value["turn"], Some(conversation_id))?)
        };
        let reply: Option<PetReplyProjection> = serde_json::from_value(value["reply"].clone())
            .map_err(|_| "PET_CHAT_RESPONSE_INVALID")?;
        if reply
            .as_ref()
            .is_some_and(|reply| reply.text.trim().is_empty() || reply.text.chars().count() > 1200)
            || (reply.is_some() && turn.is_none())
        {
            return Err("PET_CHAT_RESPONSE_INVALID".into());
        }
        if let Some(current) = &state.context.turn {
            let Some(next) = &turn else {
                return Ok(false);
            };
            if current.id == next.id
                && (current.cancellation_revision > next.cancellation_revision
                    || (!current.cancellation_pending
                        && ["completed", "cancelled", "failed"].contains(&current.status.as_str())
                        && (next.status != current.status || next.cancellation_pending)))
            {
                return Ok(false);
            }
        }
        advance_projection(&mut state.context)?;
        state.applied_snapshot = ticket.sequence;
        state.context.turn = turn;
        state.context.reply = reply;
        state.context.connection_available = true;
        Ok(true)
    }

    pub fn reject_snapshot(&self, ticket: &PetSnapshotTicket) -> Result<bool, String> {
        let mut state = self.state.lock().map_err(|_| "PET_CHAT_LOCK")?;
        if state.context.revision != ticket.revision
            || state.pending.is_some()
            || ticket.sequence <= state.applied_snapshot
        {
            return Ok(false);
        }
        advance_projection(&mut state.context)?;
        state.applied_snapshot = ticket.sequence;
        state.context.connection_available = false;
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

fn advance_projection(context: &mut PetChatContext) -> Result<(), String> {
    context.projection_revision = context
        .projection_revision
        .checked_add(1)
        .ok_or("PET_CHAT_REVISION_EXHAUSTED")?;
    Ok(())
}

fn valid_request_id(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= 128
        && id
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"-_:".contains(&byte))
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
    fn submission_cancellation_owns_the_original_request_across_binding_changes() {
        let broker = PetChatBroker::default();
        assert!(broker.begin_submission(0, "invalid", " ").is_err());
        assert!(broker.submission_cancel_request(0, "invented").is_err());
        broker.begin_submission(0, "before-chat", "Hello").unwrap();
        assert_eq!(
            broker.submission_cancel_request(0, "before-chat").unwrap(),
            json!({"idempotency_key": "pet:before-chat", "conversation_id": null})
        );
        let first = Uuid::new_v4();
        let second = Uuid::new_v4();
        let bound = broker.bind(input(0, first), &conversation(first)).unwrap();
        assert!(broker
            .begin_submission(bound.revision, "competing", "Hello")
            .is_err());
        assert!(broker
            .submission_cancel_request(bound.revision, "before-chat")
            .is_err());
        broker.end_submission("before-chat");
        broker
            .begin_submission(bound.revision, "bound-send", "Hello")
            .unwrap();
        let next = broker
            .bind(input(bound.revision, second), &conversation(second))
            .unwrap();
        assert_eq!(
            broker
                .submission_cancel_request(bound.revision, "bound-send")
                .unwrap(),
            json!({"idempotency_key": "pet:bound-send", "conversation_id": first})
        );
        broker.end_submission("bound-send");
        assert!(broker
            .begin_submission(next.revision, "bound-send", "Changed")
            .is_err());
        broker
            .begin_submission(next.revision, "new-send", "New")
            .unwrap();
        broker.end_submission("bound-send");
        assert!(broker
            .begin_submission(next.revision, "third", "Other")
            .is_err());
        assert_eq!(broker.context().unwrap().conversation_id, Some(second));
    }

    #[test]
    fn recent_submission_ownership_is_bounded_without_evicting_active_requests() {
        let broker = PetChatBroker::default();
        for index in 0..40 {
            let id = format!("owned-{index}");
            broker.begin_submission(0, &id, "Hello").unwrap();
            if index < 39 {
                broker.end_submission(&id);
            }
        }
        assert!(broker.submission_cancel_request(0, "owned-0").is_err());
        assert!(broker.submission_cancel_request(0, "owned-39").is_ok());
        assert_eq!(broker.state.lock().unwrap().owned_submissions.len(), 32);
        assert!(broker.begin_submission(0, "other", "Hello").is_err());
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
    fn snapshot_results_are_fenced_by_binding_submission_and_request_order() {
        let broker = PetChatBroker::default();
        let first = Uuid::new_v4();
        let second = Uuid::new_v4();
        let context = broker.bind(input(0, first), &conversation(first)).unwrap();
        let old = broker.prepare_snapshot().unwrap().unwrap();
        let ticket = broker
            .prepare_submission(context.revision, "snapshot-race", "Hello")
            .unwrap();
        assert!(broker.prepare_snapshot().unwrap().is_none());
        let turn = json!({"id": Uuid::new_v4(), "conversation_id": first,
            "status": "running", "cancellation_revision": 0, "cancellation_pending": false});
        broker.finish_submission(&ticket, &turn).unwrap();
        let empty = json!({"conversation_id": first, "turn": null, "reply": null});
        assert!(!broker.accept_snapshot(&old, &empty).unwrap());
        let earlier = broker.prepare_snapshot().unwrap().unwrap();
        let latest = broker.prepare_snapshot().unwrap().unwrap();
        let mut done = turn.clone();
        done["status"] = json!("completed");
        let snapshot = json!({"conversation_id": first, "turn": done,
            "reply": {"id": Uuid::new_v4(), "text": "Bound reply"}});
        assert!(broker.accept_snapshot(&latest, &snapshot).unwrap());
        assert!(broker.context().unwrap().projection_revision > context.projection_revision);
        assert!(!broker.accept_snapshot(&earlier, &empty).unwrap());
        assert_eq!(broker.context().unwrap().reply.unwrap().text, "Bound reply");
        broker
            .bind(input(context.revision, second), &conversation(second))
            .unwrap();
        assert!(!broker.accept_snapshot(&latest, &snapshot).unwrap());
        assert!(broker.context().unwrap().reply.is_none());
        let current = broker.prepare_snapshot().unwrap().unwrap();
        assert!(broker.accept_snapshot(&current, &snapshot).is_err());
    }

    #[test]
    fn a_late_connection_failure_cannot_hide_a_recovered_snapshot() {
        let broker = PetChatBroker::default();
        let id = Uuid::new_v4();
        broker.bind(input(0, id), &conversation(id)).unwrap();
        let earlier = broker.prepare_snapshot().unwrap().unwrap();
        let current = broker.prepare_snapshot().unwrap().unwrap();
        assert!(broker
            .accept_snapshot(
                &current,
                &json!({"conversation_id": id, "turn": null, "reply": null})
            )
            .unwrap());
        assert!(broker.context().unwrap().connection_available);
        assert!(!broker.reject_snapshot(&earlier).unwrap());
        assert!(broker.context().unwrap().connection_available);
        let failing = broker.prepare_snapshot().unwrap().unwrap();
        assert!(broker.reject_snapshot(&failing).unwrap());
        assert!(!broker.context().unwrap().connection_available);
    }

    #[test]
    fn new_chat_uses_core_model_preference_and_cannot_overwrite_a_later_binding() {
        let broker = PetChatBroker::default();
        let preference = json!({"mode": "manual", "model_id": "configured-model", "revision": 7,
            "allow_free_fallback": false, "zero_data_retention": true});
        let ticket = broker.prepare_new_chat(0, "new-once", &preference).unwrap();
        assert_eq!(
            ticket.params,
            json!({"text": "/new", "idempotency_key": "pet-new:new-once"})
        );
        let first = Uuid::new_v4();
        let context = broker
            .finish_new_chat(&ticket, &conversation(first))
            .unwrap();
        assert_eq!(
            broker
                .finish_new_chat(&ticket, &conversation(first))
                .unwrap()
                .revision,
            context.revision
        );
        let message = broker
            .prepare_submission(context.revision, "new-send", "Hello")
            .unwrap();
        assert_eq!(message.params["profile_id"], Value::Null);
        assert_eq!(
            message.params["model_selection"],
            json!({
                "mode": "manual", "model_id": "configured-model", "revision": 7,
            })
        );
        broker.release_submission(&message);
        assert!(broker.prepare_new_chat(0, "late", &preference).is_err());
        let late = broker
            .prepare_new_chat(context.revision, "second", &preference)
            .unwrap();
        let second = Uuid::new_v4();
        broker
            .bind(input(context.revision, second), &conversation(second))
            .unwrap();
        assert!(broker
            .finish_new_chat(&late, &conversation(Uuid::new_v4()))
            .is_err());
        assert_eq!(broker.context().unwrap().conversation_id, Some(second));
        let empty = PetChatBroker::default();
        assert_eq!(
            empty.prepare_submission(0, "bad-input", " ").unwrap_err(),
            "PET_CHAT_INPUT_INVALID"
        );
        assert_eq!(
            empty
                .prepare_submission(0, "valid-input", "Hello")
                .unwrap_err(),
            "PET_CHAT_NOT_BOUND"
        );
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
        let unbound = PetChatBroker::default();
        unbound
            .begin_submission(0, "cancel-before-bind", "Must not run")
            .unwrap();
        let cancelled = bridge
            .call(json!({"id": 5, "method": "assistant.messages.cancel",
            "params": unbound.submission_cancel_request(0, "cancel-before-bind").unwrap()}))
            .unwrap();
        assert_eq!(cancelled["result"]["accepted"], true);
        assert_eq!(cancelled["result"]["turn"], Value::Null);
        let created = bridge
            .call(json!({"id": 6, "method": "conversations.create",
            "params": {"project_id": null, "workspace_type": "chat_scratch"}}))
            .unwrap();
        let conversation = &created["result"];
        let id = serde_json::from_value(conversation["id"].clone()).unwrap();
        let context = unbound.bind(input(0, id), conversation).unwrap();
        let ticket = unbound
            .prepare_submission(context.revision, "cancel-before-bind", "Must not run")
            .unwrap();
        let rejected = bridge
            .call(json!({"id": 7, "method": "assistant.messages.submit", "params": ticket.params}))
            .unwrap();
        assert!(
            rejected.get("error").is_some(),
            "Core must honor the earlier durable stop"
        );
        let transcript = bridge
            .call(json!({"id": 8, "method": "messages.list",
            "params": {"conversation_id": id, "limit": 100}}))
            .unwrap();
        assert!(transcript["result"]["items"].as_array().unwrap().is_empty());
        unbound.release_submission(&ticket);
        unbound.end_submission("cancel-before-bind");
    }
}
