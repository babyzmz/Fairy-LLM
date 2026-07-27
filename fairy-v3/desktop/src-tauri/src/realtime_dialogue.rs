use std::time::Instant;

use fairy_realtime_worker::{
    RealtimeActivityProfile, RealtimeCandidateDecision, RealtimeDialogueCandidate,
    RealtimeInteractionIntensity, RealtimeVoiceOutput,
};
use serde::Serialize;
use sha2::{Digest, Sha256};

use crate::realtime_activity::{ProactivePolicyRejection, RealtimeInteractionPolicy};

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct RealtimeEnvironmentGates {
    pub locked: bool,
    pub do_not_disturb: bool,
    pub sensitive_window: bool,
    pub privacy_paused: bool,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct RealtimeDialogueProjection {
    pub session_id: String,
    pub segment_id: String,
    pub context_epoch: u64,
    pub sequence: u64,
    pub text: String,
    pub stable: bool,
    pub activity: RealtimeActivityProfile,
    pub intent: String,
    pub response_to_user: bool,
    pub speech_output: RealtimeVoiceOutput,
    pub speech_generation: u64,
    pub persona_digest: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct RealtimeAssistanceCandidate {
    pub session_id: String,
    pub segment_id: String,
    pub context_epoch: u64,
    pub sequence: u64,
    pub public_question: String,
    pub intent: String,
    pub activity: RealtimeActivityProfile,
    pub needs_online_assistance: bool,
    pub persona_digest: String,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RealtimeDialogueRejection {
    StaleIdentity,
    StaleSequence,
    PersonaMismatch,
    InvalidCandidate,
    Ungrounded,
    PrivacyPaused,
    Locked,
    DoNotDisturb,
    SensitiveWindow,
    UserSpeaking,
    FairySpeaking,
    ImmediateDuplicate,
    ActivityMismatch,
    InsufficientProactiveValue,
    ProactiveCooldown,
    SemanticDuplicate,
    HighRiskHumour,
    UnobservedActionClaim,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum RealtimeDialogueDecision {
    Listen,
    Speak(RealtimeDialogueProjection),
    RequestAssistance(RealtimeAssistanceCandidate),
    Suppress(RealtimeDialogueRejection),
}

#[derive(Clone, Debug)]
pub struct RealtimeDialogueDirector {
    session_id: String,
    segment_id: String,
    context_epoch: u64,
    persona_digest: String,
    voice_output: RealtimeVoiceOutput,
    last_sequence: u64,
    speech_generation: u64,
    last_stable_digest: Option<[u8; 32]>,
    environment: RealtimeEnvironmentGates,
    user_speaking: bool,
    fairy_speaking: bool,
    interaction_policy: RealtimeInteractionPolicy,
    started_at: Instant,
}

impl RealtimeDialogueDirector {
    pub fn new(
        session_id: String,
        segment_id: String,
        context_epoch: u64,
        persona_digest: String,
        voice_output: RealtimeVoiceOutput,
    ) -> Option<Self> {
        if session_id.trim().is_empty()
            || segment_id.trim().is_empty()
            || context_epoch == 0
            || !valid_digest(&persona_digest)
        {
            return None;
        }
        Some(Self {
            session_id,
            segment_id,
            context_epoch,
            persona_digest,
            voice_output,
            last_sequence: 0,
            speech_generation: 1,
            last_stable_digest: None,
            environment: RealtimeEnvironmentGates::default(),
            user_speaking: false,
            fairy_speaking: false,
            interaction_policy: RealtimeInteractionPolicy::default(),
            started_at: Instant::now(),
        })
    }

    pub fn evaluate(
        &mut self,
        session_id: &str,
        segment_id: &str,
        context_epoch: u64,
        sequence: u64,
        candidate: RealtimeDialogueCandidate,
    ) -> RealtimeDialogueDecision {
        let effective_activity = match candidate.activity {
            RealtimeActivityProfile::Auto => RealtimeActivityProfile::Focus,
            activity => activity,
        };
        let now_ms = self
            .started_at
            .elapsed()
            .as_millis()
            .min(u128::from(u64::MAX)) as u64;
        self.evaluate_with_policy(
            session_id,
            segment_id,
            context_epoch,
            sequence,
            candidate,
            effective_activity,
            RealtimeInteractionIntensity::Standard,
            now_ms,
        )
    }

    #[allow(clippy::too_many_arguments)]
    pub fn evaluate_with_activity(
        &mut self,
        session_id: &str,
        segment_id: &str,
        context_epoch: u64,
        sequence: u64,
        candidate: RealtimeDialogueCandidate,
        effective_activity: RealtimeActivityProfile,
        interaction_intensity: RealtimeInteractionIntensity,
    ) -> RealtimeDialogueDecision {
        let now_ms = self
            .started_at
            .elapsed()
            .as_millis()
            .min(u128::from(u64::MAX)) as u64;
        self.evaluate_with_policy(
            session_id,
            segment_id,
            context_epoch,
            sequence,
            candidate,
            effective_activity,
            interaction_intensity,
            now_ms,
        )
    }

    #[allow(clippy::too_many_arguments)]
    pub fn evaluate_with_policy(
        &mut self,
        session_id: &str,
        segment_id: &str,
        context_epoch: u64,
        sequence: u64,
        candidate: RealtimeDialogueCandidate,
        effective_activity: RealtimeActivityProfile,
        interaction_intensity: RealtimeInteractionIntensity,
        now_ms: u64,
    ) -> RealtimeDialogueDecision {
        if session_id != self.session_id
            || segment_id != self.segment_id
            || context_epoch != self.context_epoch
        {
            return RealtimeDialogueDecision::Suppress(RealtimeDialogueRejection::StaleIdentity);
        }
        if sequence <= self.last_sequence {
            return RealtimeDialogueDecision::Suppress(RealtimeDialogueRejection::StaleSequence);
        }
        self.last_sequence = sequence;
        if candidate.persona_digest != self.persona_digest {
            return RealtimeDialogueDecision::Suppress(RealtimeDialogueRejection::PersonaMismatch);
        }
        if !candidate.is_valid() {
            return RealtimeDialogueDecision::Suppress(RealtimeDialogueRejection::InvalidCandidate);
        }
        if candidate.decision == RealtimeCandidateDecision::Listen {
            return RealtimeDialogueDecision::Listen;
        }
        if candidate.grounding.is_empty()
            || (candidate.response_to_user
                && !candidate
                    .grounding
                    .iter()
                    .any(|item| item == "current_user_utterance"))
        {
            return RealtimeDialogueDecision::Suppress(RealtimeDialogueRejection::Ungrounded);
        }
        if let Some(rejection) = self.environment_rejection() {
            return RealtimeDialogueDecision::Suppress(rejection);
        }
        if self.user_speaking {
            return RealtimeDialogueDecision::Suppress(RealtimeDialogueRejection::UserSpeaking);
        }
        if self.fairy_speaking {
            return RealtimeDialogueDecision::Suppress(RealtimeDialogueRejection::FairySpeaking);
        }
        if high_risk(&candidate) && humour_like(&candidate.text) {
            return RealtimeDialogueDecision::Suppress(RealtimeDialogueRejection::HighRiskHumour);
        }
        if unobserved_action_claim(&candidate) {
            return RealtimeDialogueDecision::Suppress(
                RealtimeDialogueRejection::UnobservedActionClaim,
            );
        }
        let stable_digest = candidate.stable.then(|| text_digest(&candidate.text));
        if stable_digest.is_some() && stable_digest == self.last_stable_digest {
            return RealtimeDialogueDecision::Suppress(
                RealtimeDialogueRejection::ImmediateDuplicate,
            );
        }
        if let Err(rejection) = self.interaction_policy.evaluate(
            &candidate,
            effective_activity,
            interaction_intensity,
            now_ms,
        ) {
            return RealtimeDialogueDecision::Suppress(match rejection {
                ProactivePolicyRejection::ActivityMismatch => {
                    RealtimeDialogueRejection::ActivityMismatch
                }
                ProactivePolicyRejection::InsufficientValue => {
                    RealtimeDialogueRejection::InsufficientProactiveValue
                }
                ProactivePolicyRejection::Cooldown => RealtimeDialogueRejection::ProactiveCooldown,
                ProactivePolicyRejection::SemanticDuplicate => {
                    RealtimeDialogueRejection::SemanticDuplicate
                }
            });
        }
        if let Some(digest) = stable_digest {
            self.last_stable_digest = Some(digest);
        }

        if candidate.decision == RealtimeCandidateDecision::RequestAssistance {
            return RealtimeDialogueDecision::RequestAssistance(RealtimeAssistanceCandidate {
                session_id: self.session_id.clone(),
                segment_id: self.segment_id.clone(),
                context_epoch: self.context_epoch,
                sequence,
                public_question: candidate.text,
                intent: candidate.intent,
                activity: candidate.activity,
                needs_online_assistance: candidate.needs_online_assistance,
                persona_digest: self.persona_digest.clone(),
            });
        }
        RealtimeDialogueDecision::Speak(RealtimeDialogueProjection {
            session_id: self.session_id.clone(),
            segment_id: self.segment_id.clone(),
            context_epoch: self.context_epoch,
            sequence,
            text: candidate.text,
            stable: candidate.stable,
            activity: candidate.activity,
            intent: candidate.intent,
            response_to_user: candidate.response_to_user,
            speech_output: self.voice_output,
            speech_generation: self.speech_generation,
            persona_digest: self.persona_digest.clone(),
        })
    }

    pub fn set_environment(&mut self, environment: RealtimeEnvironmentGates) {
        self.environment = environment;
        if environment.privacy_paused {
            self.invalidate_speech();
        }
    }

    pub fn barge_in(&mut self) -> u64 {
        self.user_speaking = true;
        self.fairy_speaking = false;
        self.invalidate_speech();
        self.speech_generation
    }

    pub fn user_speech_stopped(&mut self) {
        self.user_speaking = false;
    }

    pub fn set_fairy_speaking(&mut self, speaking: bool) {
        self.fairy_speaking = speaking;
    }

    pub fn invalidate_speech(&mut self) -> u64 {
        self.speech_generation = self.speech_generation.saturating_add(1);
        self.fairy_speaking = false;
        self.speech_generation
    }

    pub fn reset_epoch_policy(&mut self) {
        self.last_sequence = 0;
        self.last_stable_digest = None;
        self.interaction_policy.reset_epoch();
        self.invalidate_speech();
    }

    pub const fn speech_generation(&self) -> u64 {
        self.speech_generation
    }

    fn environment_rejection(&self) -> Option<RealtimeDialogueRejection> {
        if self.environment.privacy_paused {
            Some(RealtimeDialogueRejection::PrivacyPaused)
        } else if self.environment.locked {
            Some(RealtimeDialogueRejection::Locked)
        } else if self.environment.do_not_disturb {
            Some(RealtimeDialogueRejection::DoNotDisturb)
        } else if self.environment.sensitive_window {
            Some(RealtimeDialogueRejection::SensitiveWindow)
        } else {
            None
        }
    }
}

fn text_digest(text: &str) -> [u8; 32] {
    Sha256::digest(
        text.split_whitespace()
            .collect::<Vec<_>>()
            .join(" ")
            .as_bytes(),
    )
    .into()
}

fn high_risk(candidate: &RealtimeDialogueCandidate) -> bool {
    let intent = candidate.intent.as_str();
    if matches!(
        intent,
        "medical" | "legal" | "financial" | "security" | "privacy" | "approval" | "emergency"
    ) {
        return true;
    }
    contains_any(
        &candidate.text,
        &[
            "diagnosis",
            "prescription",
            "lawyer",
            "legal advice",
            "bank transfer",
            "password",
            "credential",
            "emergency",
            "诊断",
            "处方",
            "法律意见",
            "转账",
            "密码",
            "凭据",
            "紧急情况",
        ],
    )
}

fn humour_like(text: &str) -> bool {
    contains_any(
        text,
        &[
            "haha",
            "lol",
            "joke",
            "funny",
            "brave choice",
            "bold choice",
            "哈哈",
            "笑死",
            "开个玩笑",
            "真勇敢",
        ],
    )
}

fn unobserved_action_claim(candidate: &RealtimeDialogueCandidate) -> bool {
    if candidate
        .grounding
        .iter()
        .any(|item| item.starts_with("tool_result:"))
    {
        return false;
    }
    contains_any(
        &candidate.text,
        &[
            "i opened",
            "i have opened",
            "i sent",
            "i have sent",
            "i changed",
            "i deleted",
            "i completed",
            "i purchased",
            "我已经打开",
            "我已打开",
            "我已经发送",
            "我已发送",
            "我已经修改",
            "我已修改",
            "我已经删除",
            "我已删除",
            "我已经完成",
            "我已完成",
            "我已经购买",
        ],
    )
}

fn contains_any(text: &str, needles: &[&str]) -> bool {
    let normalized = text.to_lowercase();
    needles.iter().any(|needle| normalized.contains(needle))
}

fn valid_digest(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn director() -> RealtimeDialogueDirector {
        RealtimeDialogueDirector::new(
            "session-1".to_owned(),
            "segment-1".to_owned(),
            1,
            "a".repeat(64),
            RealtimeVoiceOutput::FairyVoice,
        )
        .expect("director")
    }

    fn candidate(text: &str) -> RealtimeDialogueCandidate {
        RealtimeDialogueCandidate {
            decision: RealtimeCandidateDecision::Speak,
            activity: RealtimeActivityProfile::Game,
            confidence: 0.9,
            intent: "answer".to_owned(),
            grounding: vec!["current_user_utterance".to_owned()],
            text: text.to_owned(),
            urgency: 0.2,
            needs_online_assistance: false,
            response_to_user: true,
            stable: true,
            persona_digest: "a".repeat(64),
        }
    }

    fn proactive(text: &str, intent: &str) -> RealtimeDialogueCandidate {
        let mut candidate = candidate(text);
        candidate.response_to_user = false;
        candidate.grounding = vec!["current_window: grounded event".to_owned()];
        candidate.intent = intent.to_owned();
        candidate
    }

    #[test]
    fn accepts_current_grounded_fairy_candidate() {
        let mut director = director();
        let result = director.evaluate("session-1", "segment-1", 1, 1, candidate("Move back."));
        let RealtimeDialogueDecision::Speak(projection) = result else {
            panic!("expected speech")
        };
        assert_eq!(projection.speech_generation, 1);
        assert_eq!(projection.speech_output, RealtimeVoiceOutput::FairyVoice);
        assert_eq!(projection.persona_digest, "a".repeat(64));
    }

    #[test]
    fn stale_identity_sequence_and_persona_drift_fail_closed() {
        let mut director = director();
        assert_eq!(
            director.evaluate("session-1", "old-segment", 1, 1, candidate("Old."),),
            RealtimeDialogueDecision::Suppress(RealtimeDialogueRejection::StaleIdentity)
        );
        assert!(matches!(
            director.evaluate("session-1", "segment-1", 1, 2, candidate("Current.")),
            RealtimeDialogueDecision::Speak(_)
        ));
        assert_eq!(
            director.evaluate("session-1", "segment-1", 1, 2, candidate("Replay.")),
            RealtimeDialogueDecision::Suppress(RealtimeDialogueRejection::StaleSequence)
        );
        let mut drift = candidate("Drift.");
        drift.persona_digest = "b".repeat(64);
        assert_eq!(
            director.evaluate("session-1", "segment-1", 1, 3, drift),
            RealtimeDialogueDecision::Suppress(RealtimeDialogueRejection::PersonaMismatch)
        );
    }

    #[test]
    fn ungrounded_and_immediate_duplicate_candidates_are_suppressed() {
        let mut director = director();
        let mut ungrounded = candidate("Guess.");
        ungrounded.response_to_user = false;
        ungrounded.grounding.clear();
        assert_eq!(
            director.evaluate("session-1", "segment-1", 1, 1, ungrounded),
            RealtimeDialogueDecision::Suppress(RealtimeDialogueRejection::Ungrounded)
        );
        assert!(matches!(
            director.evaluate("session-1", "segment-1", 1, 2, candidate("Same answer.")),
            RealtimeDialogueDecision::Speak(_)
        ));
        assert_eq!(
            director.evaluate("session-1", "segment-1", 1, 3, candidate("Same   answer."),),
            RealtimeDialogueDecision::Suppress(RealtimeDialogueRejection::ImmediateDuplicate)
        );
    }

    #[test]
    fn environment_and_speech_ownership_suppress_output() {
        let gates = [
            (
                RealtimeEnvironmentGates {
                    privacy_paused: true,
                    ..Default::default()
                },
                RealtimeDialogueRejection::PrivacyPaused,
            ),
            (
                RealtimeEnvironmentGates {
                    locked: true,
                    ..Default::default()
                },
                RealtimeDialogueRejection::Locked,
            ),
            (
                RealtimeEnvironmentGates {
                    do_not_disturb: true,
                    ..Default::default()
                },
                RealtimeDialogueRejection::DoNotDisturb,
            ),
            (
                RealtimeEnvironmentGates {
                    sensitive_window: true,
                    ..Default::default()
                },
                RealtimeDialogueRejection::SensitiveWindow,
            ),
        ];
        for (environment, expected) in gates {
            let mut director = director();
            director.set_environment(environment);
            assert_eq!(
                director.evaluate("session-1", "segment-1", 1, 1, candidate("Blocked.")),
                RealtimeDialogueDecision::Suppress(expected)
            );
        }

        let mut director = director();
        director.barge_in();
        assert_eq!(
            director.evaluate("session-1", "segment-1", 1, 1, candidate("Wait.")),
            RealtimeDialogueDecision::Suppress(RealtimeDialogueRejection::UserSpeaking)
        );
        director.user_speech_stopped();
        director.set_fairy_speaking(true);
        assert_eq!(
            director.evaluate("session-1", "segment-1", 1, 2, candidate("Overlap.")),
            RealtimeDialogueDecision::Suppress(RealtimeDialogueRejection::FairySpeaking)
        );
    }

    #[test]
    fn high_risk_humour_and_unobserved_actions_are_suppressed() {
        let mut director = director();
        let mut risky = candidate("Your password is wrong, lol.");
        risky.intent = "security".to_owned();
        assert_eq!(
            director.evaluate("session-1", "segment-1", 1, 1, risky),
            RealtimeDialogueDecision::Suppress(RealtimeDialogueRejection::HighRiskHumour)
        );
        let mut claim = candidate("I opened the settings.");
        claim.response_to_user = false;
        claim.grounding = vec!["current_window: settings are visible".to_owned()];
        assert_eq!(
            director.evaluate("session-1", "segment-1", 1, 2, claim),
            RealtimeDialogueDecision::Suppress(RealtimeDialogueRejection::UnobservedActionClaim)
        );
    }

    #[test]
    fn text_only_and_assistance_preserve_public_projection_without_execution() {
        let mut director = RealtimeDialogueDirector::new(
            "session-1".to_owned(),
            "segment-1".to_owned(),
            1,
            "a".repeat(64),
            RealtimeVoiceOutput::TextOnly,
        )
        .expect("director");
        let RealtimeDialogueDecision::Speak(projection) =
            director.evaluate("session-1", "segment-1", 1, 1, candidate("Text only."))
        else {
            panic!("text projection")
        };
        assert_eq!(projection.speech_output, RealtimeVoiceOutput::TextOnly);

        let mut assistance = candidate("Find the current guide.");
        assistance.decision = RealtimeCandidateDecision::RequestAssistance;
        assistance.intent = "assist".to_owned();
        assistance.needs_online_assistance = true;
        assert!(matches!(
            director.evaluate("session-1", "segment-1", 1, 2, assistance),
            RealtimeDialogueDecision::RequestAssistance(_)
        ));
    }

    #[test]
    fn proactive_profile_cooldown_and_semantic_dedup_are_native_policy() {
        let mut director = director();
        assert!(matches!(
            director.evaluate_with_policy(
                "session-1",
                "segment-1",
                1,
                1,
                proactive("Health low, step back.", "comment"),
                RealtimeActivityProfile::Game,
                RealtimeInteractionIntensity::Standard,
                1_000,
            ),
            RealtimeDialogueDecision::Speak(_)
        ));
        assert_eq!(
            director.evaluate_with_policy(
                "session-1",
                "segment-1",
                1,
                2,
                proactive("A different event.", "comment"),
                RealtimeActivityProfile::Game,
                RealtimeInteractionIntensity::Standard,
                20_000,
            ),
            RealtimeDialogueDecision::Suppress(RealtimeDialogueRejection::ProactiveCooldown)
        );
        assert_eq!(
            director.evaluate_with_policy(
                "session-1",
                "segment-1",
                1,
                3,
                proactive("Health is low; step back.", "comment"),
                RealtimeActivityProfile::Game,
                RealtimeInteractionIntensity::Standard,
                50_000,
            ),
            RealtimeDialogueDecision::Suppress(RealtimeDialogueRejection::SemanticDuplicate)
        );
    }

    #[test]
    fn focus_and_quiet_proactive_value_gates_do_not_block_user_answers() {
        let mut director = director();
        let mut focus_comment = proactive("Ordinary work changed.", "comment");
        focus_comment.activity = RealtimeActivityProfile::Focus;
        assert_eq!(
            director.evaluate_with_policy(
                "session-1",
                "segment-1",
                1,
                1,
                focus_comment,
                RealtimeActivityProfile::Focus,
                RealtimeInteractionIntensity::Standard,
                0,
            ),
            RealtimeDialogueDecision::Suppress(
                RealtimeDialogueRejection::InsufficientProactiveValue
            )
        );
        assert!(matches!(
            director.evaluate_with_policy(
                "session-1",
                "segment-1",
                1,
                2,
                candidate("Direct answer."),
                RealtimeActivityProfile::Focus,
                RealtimeInteractionIntensity::Quiet,
                1,
            ),
            RealtimeDialogueDecision::Speak(_)
        ));
    }

    #[test]
    fn barge_in_invalidates_every_prior_speech_generation_within_budget() {
        let mut director = director();
        let started = std::time::Instant::now();
        assert_eq!(director.barge_in(), 2);
        assert!(started.elapsed() < std::time::Duration::from_millis(120));
        assert_eq!(director.speech_generation(), 2);
    }
}
