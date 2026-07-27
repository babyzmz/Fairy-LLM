use std::collections::{HashSet, VecDeque};

use fairy_realtime_worker::{
    RealtimeActivityProfile, RealtimeDialogueCandidate, RealtimeInteractionIntensity,
};
use sha2::{Digest, Sha256};

const CLASSIFICATION_CONFIDENCE: f64 = 0.72;
const CLASSIFICATION_WINDOW: usize = 5;
const CLASSIFICATION_STREAK: usize = 3;
const EVENT_CACHE_CAPACITY: usize = 32;
const EVENT_CACHE_TTL_MS: u64 = 10 * 60 * 1_000;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ActivityObservation {
    pub effective_activity: RealtimeActivityProfile,
    pub switched: bool,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RealtimeActivityClassifier {
    requested_profile: RealtimeActivityProfile,
    effective_activity: RealtimeActivityProfile,
    last_sequence: u64,
    evidence: VecDeque<RealtimeActivityProfile>,
}

impl RealtimeActivityClassifier {
    pub fn new(requested_profile: RealtimeActivityProfile) -> Self {
        let effective_activity = match requested_profile {
            RealtimeActivityProfile::Auto | RealtimeActivityProfile::Focus => {
                RealtimeActivityProfile::Focus
            }
            RealtimeActivityProfile::Game => RealtimeActivityProfile::Game,
        };
        Self {
            requested_profile,
            effective_activity,
            last_sequence: 0,
            evidence: VecDeque::with_capacity(CLASSIFICATION_WINDOW),
        }
    }

    pub fn observe(
        &mut self,
        sequence: u64,
        candidate: &RealtimeDialogueCandidate,
    ) -> ActivityObservation {
        if self.requested_profile != RealtimeActivityProfile::Auto || sequence <= self.last_sequence
        {
            return self.current(false);
        }
        self.last_sequence = sequence;
        if !qualifying_activity_evidence(candidate) {
            self.evidence.clear();
            return self.current(false);
        }
        if self
            .evidence
            .back()
            .is_some_and(|value| *value != candidate.activity)
        {
            self.evidence.clear();
        }
        self.evidence.push_back(candidate.activity);
        while self.evidence.len() > CLASSIFICATION_WINDOW {
            self.evidence.pop_front();
        }
        let stable = self.evidence.len() >= CLASSIFICATION_STREAK
            && self
                .evidence
                .iter()
                .rev()
                .take(CLASSIFICATION_STREAK)
                .all(|value| *value == candidate.activity);
        if !stable || candidate.activity == self.effective_activity {
            return self.current(false);
        }
        self.effective_activity = candidate.activity;
        self.evidence.clear();
        self.current(true)
    }

    pub const fn effective_activity(&self) -> RealtimeActivityProfile {
        self.effective_activity
    }

    pub const fn requested_profile(&self) -> RealtimeActivityProfile {
        self.requested_profile
    }

    pub fn reset_epoch(&mut self) {
        self.last_sequence = 0;
        self.evidence.clear();
    }

    fn current(&self, switched: bool) -> ActivityObservation {
        ActivityObservation {
            effective_activity: self.effective_activity,
            switched,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ProactivePolicyRejection {
    ActivityMismatch,
    InsufficientValue,
    Cooldown,
    SemanticDuplicate,
}

#[derive(Clone, Debug)]
struct AcceptedEvent {
    accepted_at_ms: u64,
    intent: String,
    grounding_digest: [u8; 32],
    text_tokens: HashSet<String>,
}

#[derive(Clone, Debug)]
pub struct RealtimeInteractionPolicy {
    last_proactive_at_ms: Option<u64>,
    accepted_events: VecDeque<AcceptedEvent>,
}

impl Default for RealtimeInteractionPolicy {
    fn default() -> Self {
        Self {
            last_proactive_at_ms: None,
            accepted_events: VecDeque::with_capacity(EVENT_CACHE_CAPACITY),
        }
    }
}

impl RealtimeInteractionPolicy {
    pub fn evaluate(
        &mut self,
        candidate: &RealtimeDialogueCandidate,
        effective_activity: RealtimeActivityProfile,
        intensity: RealtimeInteractionIntensity,
        now_ms: u64,
    ) -> Result<(), ProactivePolicyRejection> {
        if candidate.response_to_user {
            return Ok(());
        }
        let effective_activity = normalized_effective_activity(effective_activity);
        if candidate.activity != effective_activity {
            return Err(ProactivePolicyRejection::ActivityMismatch);
        }
        if !proactive_value_allowed(candidate, effective_activity, intensity) {
            return Err(ProactivePolicyRejection::InsufficientValue);
        }
        self.evict_expired(now_ms);
        let grounding_digest = grounding_digest(&candidate.grounding);
        let text_tokens = normalized_text_tokens(&candidate.text);
        if self.accepted_events.iter().any(|accepted| {
            accepted.intent == candidate.intent
                && accepted.grounding_digest == grounding_digest
                && near_identical(&accepted.text_tokens, &text_tokens)
        }) {
            return Err(ProactivePolicyRejection::SemanticDuplicate);
        }
        let cooldown_ms = proactive_cooldown_ms(effective_activity, intensity);
        if self
            .last_proactive_at_ms
            .is_some_and(|accepted_at| now_ms.saturating_sub(accepted_at) < cooldown_ms)
        {
            return Err(ProactivePolicyRejection::Cooldown);
        }
        self.last_proactive_at_ms = Some(now_ms);
        self.accepted_events.push_back(AcceptedEvent {
            accepted_at_ms: now_ms,
            intent: candidate.intent.clone(),
            grounding_digest,
            text_tokens,
        });
        while self.accepted_events.len() > EVENT_CACHE_CAPACITY {
            self.accepted_events.pop_front();
        }
        Ok(())
    }

    pub fn reset_epoch(&mut self) {
        self.last_proactive_at_ms = None;
        self.accepted_events.clear();
    }

    #[cfg(test)]
    fn accepted_event_count(&self) -> usize {
        self.accepted_events.len()
    }

    fn evict_expired(&mut self, now_ms: u64) {
        while self
            .accepted_events
            .front()
            .is_some_and(|event| now_ms.saturating_sub(event.accepted_at_ms) >= EVENT_CACHE_TTL_MS)
        {
            self.accepted_events.pop_front();
        }
    }
}

pub const fn proactive_cooldown_ms(
    activity: RealtimeActivityProfile,
    intensity: RealtimeInteractionIntensity,
) -> u64 {
    match (normalized_effective_activity(activity), intensity) {
        (RealtimeActivityProfile::Game, RealtimeInteractionIntensity::Quiet) => 90_000,
        (RealtimeActivityProfile::Game, RealtimeInteractionIntensity::Standard) => 45_000,
        (RealtimeActivityProfile::Game, RealtimeInteractionIntensity::Active) => 20_000,
        (RealtimeActivityProfile::Focus, RealtimeInteractionIntensity::Quiet) => 8 * 60_000,
        (RealtimeActivityProfile::Focus, RealtimeInteractionIntensity::Standard) => 8 * 60_000,
        (RealtimeActivityProfile::Focus, RealtimeInteractionIntensity::Active) => 3 * 60_000,
        (RealtimeActivityProfile::Auto, _) => unreachable!(),
    }
}

const fn normalized_effective_activity(
    activity: RealtimeActivityProfile,
) -> RealtimeActivityProfile {
    match activity {
        RealtimeActivityProfile::Auto => RealtimeActivityProfile::Focus,
        value => value,
    }
}

fn qualifying_activity_evidence(candidate: &RealtimeDialogueCandidate) -> bool {
    matches!(
        candidate.activity,
        RealtimeActivityProfile::Game | RealtimeActivityProfile::Focus
    ) && candidate.confidence >= CLASSIFICATION_CONFIDENCE
        && !candidate.response_to_user
        && candidate
            .grounding
            .iter()
            .any(|item| item.starts_with("current_window:"))
}

fn proactive_value_allowed(
    candidate: &RealtimeDialogueCandidate,
    activity: RealtimeActivityProfile,
    intensity: RealtimeInteractionIntensity,
) -> bool {
    if high_value_intent(&candidate.intent) || candidate.urgency >= 0.85 {
        return true;
    }
    if intensity == RealtimeInteractionIntensity::Quiet {
        return false;
    }
    if activity == RealtimeActivityProfile::Focus {
        return focus_value_intent(&candidate.intent);
    }
    true
}

fn high_value_intent(intent: &str) -> bool {
    matches!(
        intent,
        "warn" | "safety" | "privacy" | "security" | "emergency"
    )
}

fn focus_value_intent(intent: &str) -> bool {
    matches!(
        intent,
        "persistent_error"
            | "repeated_failure"
            | "screen_explanation"
            | "stagnation_reminder"
            | "stage_complete"
            | "goal_reminder"
            | "time_reminder"
            | "privacy"
            | "security"
            | "warn"
    )
}

fn grounding_digest(grounding: &[String]) -> [u8; 32] {
    let mut normalized = grounding
        .iter()
        .map(|item| {
            item.split_whitespace()
                .collect::<Vec<_>>()
                .join(" ")
                .to_lowercase()
        })
        .collect::<Vec<_>>();
    normalized.sort();
    Sha256::digest(normalized.join("\n").as_bytes()).into()
}

fn normalized_text_tokens(text: &str) -> HashSet<String> {
    let mut tokens = HashSet::new();
    let mut latin = String::new();
    for character in text.to_lowercase().chars() {
        if character.is_ascii_alphanumeric() {
            latin.push(character);
        } else {
            if !latin.is_empty() {
                tokens.insert(std::mem::take(&mut latin));
            }
            if is_cjk(character) {
                tokens.insert(character.to_string());
            }
        }
    }
    if !latin.is_empty() {
        tokens.insert(latin);
    }
    tokens
}

fn is_cjk(character: char) -> bool {
    matches!(
        character as u32,
        0x3400..=0x4dbf | 0x4e00..=0x9fff | 0xf900..=0xfaff
    )
}

fn near_identical(left: &HashSet<String>, right: &HashSet<String>) -> bool {
    if left.is_empty() || right.is_empty() {
        return left == right;
    }
    let intersection = left.intersection(right).count();
    let union = left.union(right).count();
    intersection * 100 >= union * 80
}

#[cfg(test)]
mod tests {
    use super::*;
    use fairy_realtime_worker::RealtimeCandidateDecision;

    fn candidate(
        activity: RealtimeActivityProfile,
        confidence: f64,
        intent: &str,
        text: &str,
    ) -> RealtimeDialogueCandidate {
        RealtimeDialogueCandidate {
            decision: RealtimeCandidateDecision::Speak,
            activity,
            confidence,
            intent: intent.to_owned(),
            grounding: vec!["current_window: visible event".to_owned()],
            text: text.to_owned(),
            urgency: 0.4,
            needs_online_assistance: false,
            response_to_user: false,
            stable: true,
            persona_digest: "a".repeat(64),
        }
    }

    #[test]
    fn explicit_profiles_never_classify_and_auto_requires_three_consistent_observations() {
        let game = candidate(RealtimeActivityProfile::Game, 0.9, "comment", "Event");
        let mut explicit = RealtimeActivityClassifier::new(RealtimeActivityProfile::Focus);
        for sequence in 1..=4 {
            assert!(!explicit.observe(sequence, &game).switched);
        }
        assert_eq!(
            explicit.effective_activity(),
            RealtimeActivityProfile::Focus
        );

        let mut automatic = RealtimeActivityClassifier::new(RealtimeActivityProfile::Auto);
        assert!(!automatic.observe(1, &game).switched);
        assert!(!automatic.observe(2, &game).switched);
        let switched = automatic.observe(3, &game);
        assert!(switched.switched);
        assert_eq!(switched.effective_activity, RealtimeActivityProfile::Game);
        assert!(!automatic.observe(3, &game).switched);
    }

    #[test]
    fn low_confidence_ungrounded_and_conflicting_evidence_reset_the_streak() {
        let mut classifier = RealtimeActivityClassifier::new(RealtimeActivityProfile::Auto);
        let game = candidate(RealtimeActivityProfile::Game, 0.9, "comment", "Game");
        let focus = candidate(RealtimeActivityProfile::Focus, 0.9, "comment", "Focus");
        let low = candidate(RealtimeActivityProfile::Game, 0.6, "comment", "Low");
        assert!(!classifier.observe(1, &game).switched);
        assert!(!classifier.observe(2, &focus).switched);
        assert!(!classifier.observe(3, &game).switched);
        assert!(!classifier.observe(4, &low).switched);
        assert!(!classifier.observe(5, &game).switched);
        assert!(!classifier.observe(6, &game).switched);
        assert!(classifier.observe(7, &game).switched);

        classifier.reset_epoch();
        let mut ungrounded = focus.clone();
        ungrounded.grounding = vec!["guess".to_owned()];
        assert!(!classifier.observe(1, &ungrounded).switched);
    }

    #[test]
    fn cooldown_table_and_user_reply_bypass_are_exact() {
        assert_eq!(
            proactive_cooldown_ms(
                RealtimeActivityProfile::Game,
                RealtimeInteractionIntensity::Standard,
            ),
            45_000
        );
        assert_eq!(
            proactive_cooldown_ms(
                RealtimeActivityProfile::Game,
                RealtimeInteractionIntensity::Active,
            ),
            20_000
        );
        assert_eq!(
            proactive_cooldown_ms(
                RealtimeActivityProfile::Focus,
                RealtimeInteractionIntensity::Standard,
            ),
            480_000
        );
        assert_eq!(
            proactive_cooldown_ms(
                RealtimeActivityProfile::Focus,
                RealtimeInteractionIntensity::Active,
            ),
            180_000
        );

        let mut policy = RealtimeInteractionPolicy::default();
        let first = candidate(RealtimeActivityProfile::Game, 0.9, "comment", "First event");
        assert!(policy
            .evaluate(
                &first,
                RealtimeActivityProfile::Game,
                RealtimeInteractionIntensity::Standard,
                1_000,
            )
            .is_ok());
        let mut reply = candidate(
            RealtimeActivityProfile::Focus,
            1.0,
            "answer",
            "Direct answer",
        );
        reply.response_to_user = true;
        reply.grounding = vec!["current_user_utterance".to_owned()];
        assert!(policy
            .evaluate(
                &reply,
                RealtimeActivityProfile::Game,
                RealtimeInteractionIntensity::Quiet,
                1_001,
            )
            .is_ok());
    }

    #[test]
    fn quiet_and_focus_reject_low_value_proactive_comments() {
        let comment = candidate(
            RealtimeActivityProfile::Game,
            0.9,
            "comment",
            "Ordinary event",
        );
        assert_eq!(
            RealtimeInteractionPolicy::default().evaluate(
                &comment,
                RealtimeActivityProfile::Game,
                RealtimeInteractionIntensity::Quiet,
                0,
            ),
            Err(ProactivePolicyRejection::InsufficientValue)
        );
        let focus = candidate(
            RealtimeActivityProfile::Focus,
            0.9,
            "comment",
            "Ordinary work",
        );
        assert_eq!(
            RealtimeInteractionPolicy::default().evaluate(
                &focus,
                RealtimeActivityProfile::Focus,
                RealtimeInteractionIntensity::Standard,
                0,
            ),
            Err(ProactivePolicyRejection::InsufficientValue)
        );
    }

    #[test]
    fn semantic_duplicates_expire_and_rejected_candidates_do_not_consume_cooldown() {
        let mut policy = RealtimeInteractionPolicy::default();
        let event = candidate(
            RealtimeActivityProfile::Game,
            0.9,
            "comment",
            "Health low, step back",
        );
        assert!(policy
            .evaluate(
                &event,
                RealtimeActivityProfile::Game,
                RealtimeInteractionIntensity::Active,
                0,
            )
            .is_ok());
        let near = candidate(
            RealtimeActivityProfile::Game,
            0.9,
            "comment",
            "Health is low; step back.",
        );
        assert_eq!(
            policy.evaluate(
                &near,
                RealtimeActivityProfile::Game,
                RealtimeInteractionIntensity::Active,
                21_000,
            ),
            Err(ProactivePolicyRejection::SemanticDuplicate)
        );
        let fresh = candidate(
            RealtimeActivityProfile::Game,
            0.9,
            "comment",
            "A different event",
        );
        assert!(policy
            .evaluate(
                &fresh,
                RealtimeActivityProfile::Game,
                RealtimeInteractionIntensity::Active,
                EVENT_CACHE_TTL_MS,
            )
            .is_ok());
        assert_eq!(policy.accepted_event_count(), 1);
    }

    #[test]
    fn event_cache_is_bounded() {
        let mut policy = RealtimeInteractionPolicy::default();
        for index in 0..40 {
            let event = candidate(
                RealtimeActivityProfile::Game,
                0.9,
                "comment",
                &format!("Unique event {index}"),
            );
            assert!(policy
                .evaluate(
                    &event,
                    RealtimeActivityProfile::Game,
                    RealtimeInteractionIntensity::Active,
                    index * 20_000,
                )
                .is_ok());
        }
        assert!(policy.accepted_event_count() <= EVENT_CACHE_CAPACITY);
        assert_eq!(policy.accepted_event_count(), 30);
    }
}
