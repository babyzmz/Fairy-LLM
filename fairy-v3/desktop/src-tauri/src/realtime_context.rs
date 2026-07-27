use std::collections::VecDeque;

use fairy_realtime_worker::{RealtimeActivityProfile, RealtimeContextCarryover};
use serde_json::Value;
use thiserror::Error;

const MAX_STABLE_CAPTIONS: usize = 24;
const MAX_STABLE_CAPTION_CHARS: usize = 4_000;
const MAX_STABLE_CAPTION_TOTAL_CHARS: usize = 16_000;
const MAX_SUMMARY_CHARS: usize = 2_000;
const MAX_GOAL_CHARS: usize = 500;
const MAX_MEMORY_CHARS: usize = 300;
const MAX_MEMORIES: usize = 8;

#[derive(Clone, Copy, Debug, Error, Eq, PartialEq)]
pub enum RealtimeContextError {
    #[error("realtime context carryover is invalid")]
    Invalid,
}

#[derive(Clone, Debug, Default)]
pub struct RealtimeContextAuthority {
    session_id: Option<String>,
    persona_digest: Option<String>,
    current_goal: Option<String>,
    verified_short_memories: Vec<String>,
    stable_captions: VecDeque<String>,
    stable_caption_chars: usize,
}

impl RealtimeContextAuthority {
    pub fn attach_session(
        &mut self,
        session_id: &str,
        persona_snapshot: &Value,
    ) -> Result<(), RealtimeContextError> {
        let persona_digest = bounded_required(
            persona_snapshot
                .get("persona_digest")
                .and_then(Value::as_str),
            64,
        )
        .filter(|value| valid_sha256(value))
        .ok_or(RealtimeContextError::Invalid)?;
        let current_goal = bounded_optional(
            persona_snapshot
                .pointer("/short_memory/current_goal")
                .and_then(Value::as_str),
            MAX_GOAL_CHARS,
        );
        let verified_short_memories = [
            persona_snapshot
                .pointer("/short_memory/subject_title")
                .and_then(Value::as_str),
            persona_snapshot
                .pointer("/short_memory/recent_progress")
                .and_then(Value::as_str),
        ]
        .into_iter()
        .filter_map(|value| bounded_truncated_optional(value, MAX_MEMORY_CHARS))
        .take(MAX_MEMORIES)
        .collect();

        if self.session_id.as_deref() != Some(session_id) {
            self.stable_captions.clear();
            self.stable_caption_chars = 0;
        }
        self.session_id = Some(session_id.to_owned());
        self.persona_digest = Some(persona_digest);
        self.current_goal = current_goal;
        self.verified_short_memories = verified_short_memories;
        Ok(())
    }

    pub fn observe_worker_event(&mut self, value: &Value) {
        if value.get("type").and_then(Value::as_str) != Some("public_caption")
            || value.get("stable").and_then(Value::as_bool) != Some(true)
            || value.get("session_id").and_then(Value::as_str) != self.session_id.as_deref()
        {
            return;
        }
        let Some(text) = bounded_optional(
            value.get("text").and_then(Value::as_str),
            MAX_STABLE_CAPTION_CHARS,
        ) else {
            return;
        };
        if self.stable_captions.back() == Some(&text) {
            return;
        }
        self.stable_caption_chars = self
            .stable_caption_chars
            .saturating_add(text.chars().count());
        self.stable_captions.push_back(text);
        while self.stable_captions.len() > MAX_STABLE_CAPTIONS
            || self.stable_caption_chars > MAX_STABLE_CAPTION_TOTAL_CHARS
        {
            if let Some(removed) = self.stable_captions.pop_front() {
                self.stable_caption_chars = self
                    .stable_caption_chars
                    .saturating_sub(removed.chars().count());
            }
        }
    }

    #[allow(clippy::too_many_arguments)]
    pub fn build(
        &self,
        session_id: &str,
        current_segment_id: &str,
        current_context_epoch: u64,
        target_segment_id: &str,
        next_context_epoch: u64,
        activity_profile: RealtimeActivityProfile,
        unfinished_assistance: Option<(&str, &str)>,
    ) -> Result<RealtimeContextCarryover, RealtimeContextError> {
        if self.session_id.as_deref() != Some(session_id) {
            return Err(RealtimeContextError::Invalid);
        }
        let persona_digest = self
            .persona_digest
            .clone()
            .ok_or(RealtimeContextError::Invalid)?;
        let stable_caption_summary = suffix_within_limit(
            self.stable_captions.iter().map(String::as_str),
            MAX_SUMMARY_CHARS,
        );
        let (unfinished_assistance_id, unfinished_assistance_status) = unfinished_assistance
            .map(|(request_id, status)| (Some(request_id.to_owned()), Some(status.to_owned())))
            .unwrap_or((None, None));
        let carryover = RealtimeContextCarryover {
            schema_version: 1,
            session_id: session_id.to_owned(),
            current_segment_id: current_segment_id.to_owned(),
            current_context_epoch,
            target_segment_id: target_segment_id.to_owned(),
            next_context_epoch,
            stable_caption_summary,
            current_goal: self.current_goal.clone(),
            activity_profile,
            verified_short_memories: self.verified_short_memories.clone(),
            unfinished_assistance_id,
            unfinished_assistance_status,
            persona_digest,
        };
        carryover
            .is_valid()
            .then_some(carryover)
            .ok_or(RealtimeContextError::Invalid)
    }

    pub fn commit_rotation(&mut self, session_id: &str) {
        if self.session_id.as_deref() == Some(session_id) {
            self.stable_captions.clear();
            self.stable_caption_chars = 0;
        }
    }

    pub fn end_session(&mut self, session_id: &str) {
        if self.session_id.as_deref() == Some(session_id) {
            *self = Self::default();
        }
    }
}

fn bounded_required(value: Option<&str>, maximum: usize) -> Option<String> {
    bounded_optional(value, maximum)
}

fn bounded_optional(value: Option<&str>, maximum: usize) -> Option<String> {
    let normalized = value?.split_whitespace().collect::<Vec<_>>().join(" ");
    (!normalized.is_empty() && normalized.chars().count() <= maximum).then_some(normalized)
}

fn suffix_within_limit<'a>(
    values: impl DoubleEndedIterator<Item = &'a str>,
    maximum: usize,
) -> String {
    let combined = values.collect::<Vec<_>>().join("\n");
    let mut suffix = combined.chars().rev().take(maximum).collect::<Vec<_>>();
    suffix.reverse();
    suffix.into_iter().collect()
}

fn bounded_truncated_optional(value: Option<&str>, maximum: usize) -> Option<String> {
    let normalized = value?.split_whitespace().collect::<Vec<_>>().join(" ");
    if normalized.is_empty() {
        return None;
    }
    Some(normalized.chars().take(maximum).collect())
}

fn valid_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn persona(goal: Option<&str>) -> Value {
        json!({
            "persona_digest": "a".repeat(64),
            "short_memory": {
                "current_goal": goal,
                "subject_title": "Fairy Beta",
                "recent_progress": "Phase 7 is active"
            }
        })
    }

    #[test]
    fn carryover_keeps_only_stable_public_captions_and_core_short_memory() {
        let mut authority = RealtimeContextAuthority::default();
        authority
            .attach_session("session-a", &persona(Some("Finish Phase 7")))
            .unwrap();
        authority.observe_worker_event(&json!({
            "type": "public_caption",
            "session_id": "session-a",
            "text": "interim secret",
            "stable": false
        }));
        authority.observe_worker_event(&json!({
            "type": "public_caption",
            "session_id": "session-a",
            "text": "stable public line",
            "stable": true
        }));
        let carryover = authority
            .build(
                "session-a",
                "segment-a",
                1,
                "segment-a",
                2,
                RealtimeActivityProfile::Focus,
                Some(("request-a", "running")),
            )
            .unwrap();
        assert_eq!(carryover.stable_caption_summary, "stable public line");
        assert_eq!(carryover.current_goal.as_deref(), Some("Finish Phase 7"));
        assert_eq!(
            carryover.verified_short_memories,
            ["Fairy Beta", "Phase 7 is active"]
        );
        assert_eq!(
            carryover.unfinished_assistance_id.as_deref(),
            Some("request-a")
        );
    }

    #[test]
    fn rotation_and_session_change_clear_public_caption_buffer() {
        let mut authority = RealtimeContextAuthority::default();
        authority
            .attach_session("session-a", &persona(None))
            .unwrap();
        authority.observe_worker_event(&json!({
            "type": "public_caption",
            "session_id": "session-a",
            "text": "old line",
            "stable": true
        }));
        authority.commit_rotation("session-a");
        let rotated = authority
            .build(
                "session-a",
                "segment-a",
                2,
                "segment-a",
                3,
                RealtimeActivityProfile::Focus,
                None,
            )
            .unwrap();
        assert!(rotated.stable_caption_summary.is_empty());

        authority.observe_worker_event(&json!({
            "type": "public_caption",
            "session_id": "session-a",
            "text": "session-a line",
            "stable": true
        }));
        authority
            .attach_session("session-b", &persona(None))
            .unwrap();
        let next = authority
            .build(
                "session-b",
                "segment-b",
                1,
                "segment-b",
                2,
                RealtimeActivityProfile::Focus,
                None,
            )
            .unwrap();
        assert!(next.stable_caption_summary.is_empty());
        authority.end_session("session-b");
        assert!(authority
            .build(
                "session-b",
                "segment-b",
                1,
                "segment-b",
                2,
                RealtimeActivityProfile::Focus,
                None,
            )
            .is_err());
    }
}
