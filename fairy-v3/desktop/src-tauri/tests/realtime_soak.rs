use fairy_desktop_v3::realtime_context::RealtimeContextAuthority;
use fairy_desktop_v3::realtime_coordinator::{
    BackendSegmentCreationReason, ContextRotationReason, RealtimeCoordinatorEvent,
    RealtimeCoordinatorStart, RealtimeCoordinatorState,
};
use fairy_desktop_v3::realtime_resource_governor::{
    RealtimeResourceGovernor, RealtimeResourceSample,
};
use fairy_desktop_v3::realtime_sidecar_supervisor::{
    RealtimeSidecarDecision, RealtimeSidecarSupervisor,
};
use fairy_realtime_worker::{
    RealtimeActivityProfile, RealtimeBackendKind, RealtimeInteractionIntensity,
    RealtimeResourceLevel,
};
use serde_json::{json, Value};

const FOUR_HOURS_MS: u64 = 4 * 60 * 60 * 1_000;
const SESSION_ID: &str = "phase-7-soak-session";
const INITIAL_SEGMENT_ID: &str = "phase-7-soak-segment-1";
const RECOVERY_SEGMENT_ID: &str = "phase-7-soak-segment-2";
const PERSONA_DIGEST: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

#[test]
fn four_hour_equivalent_soak_is_bounded_monotonic_and_fail_closed() {
    let mut coordinator = RealtimeCoordinatorState::start(RealtimeCoordinatorStart {
        session_id: SESSION_ID.to_owned(),
        segment_id: INITIAL_SEGMENT_ID.to_owned(),
        persona_digest: PERSONA_DIGEST.to_owned(),
        backend: RealtimeBackendKind::LocalMiniCpmO45,
        cloud_provider: None,
        activity_profile: RealtimeActivityProfile::Auto,
        interaction_intensity: RealtimeInteractionIntensity::Standard,
        presence_max_minutes: 300,
    })
    .expect("start coordinator");
    let mut context = RealtimeContextAuthority::default();
    context
        .attach_session(
            SESSION_ID,
            &json!({
                "persona_digest": PERSONA_DIGEST,
                "short_memory": {
                    "current_goal": "Certify Phase 7",
                    "subject_title": "Realtime soak",
                    "recent_progress": "Long-session verification"
                }
            }),
        )
        .expect("attach context");
    context.observe_worker_event(&json!({
        "type": "public_caption",
        "session_id": SESSION_ID,
        "text": "PRIVATE_INTERIM_SENTINEL",
        "stable": false
    }));
    context.observe_worker_event(&json!({
        "type": "provider_hidden_item",
        "session_id": SESSION_ID,
        "text": "HIDDEN_PROVIDER_SENTINEL"
    }));

    let mut virtual_now_ms = 0_u64;
    let mut rotations = 0_u32;
    let mut pause_resume_cycles = 0_u32;
    let mut window_changes = 0_u32;
    let mut last_epoch = 1_u64;
    let mut last_presence_sequence = coordinator.presence_projection().sequence;

    for index in 0..20_u32 {
        virtual_now_ms += FOUR_HOURS_MS / 20;
        context.observe_worker_event(&json!({
            "type": "public_caption",
            "session_id": SESSION_ID,
            "text": format!("Public stable checkpoint {index}"),
            "stable": true
        }));
        if index < 10 {
            coordinator
                .apply(RealtimeCoordinatorEvent::PausePrivacy)
                .expect("pause privacy");
            coordinator
                .apply(RealtimeCoordinatorEvent::ResumePrivacy)
                .expect("resume privacy");
            pause_resume_cycles += 1;
        } else {
            let reason = if index < 15 {
                window_changes += 1;
                ContextRotationReason::WindowChanged
            } else {
                ContextRotationReason::Manual
            };
            coordinator
                .apply(RealtimeCoordinatorEvent::RotateContext { reason })
                .expect("prepare rotation");
        }

        let pending = coordinator
            .pending_context_rotation()
            .cloned()
            .expect("pending rotation");
        let carryover = context
            .build(
                SESSION_ID,
                &pending.current.segment_id,
                pending.current.epoch,
                &pending.current.segment_id,
                pending.next_epoch,
                coordinator.effective_activity(),
                (index == 7).then_some(("assistance-request", "running")),
            )
            .expect("bounded carryover");
        assert!(carryover.is_valid());
        assert!(carryover.stable_caption_summary.chars().count() <= 2_000);
        assert!(carryover.verified_short_memories.len() <= 8);
        let serialized = serde_json::to_string(&carryover).expect("serialize carryover");
        assert!(!serialized.contains("PRIVATE_INTERIM_SENTINEL"));
        assert!(!serialized.contains("HIDDEN_PROVIDER_SENTINEL"));
        assert!(!serialized.contains("credential"));
        assert!(!serialized.contains("raw_media"));

        coordinator
            .commit_context_rotation_at(pending.next_epoch, virtual_now_ms)
            .expect("acknowledge rotation");
        context.commit_rotation(SESSION_ID);
        rotations += 1;
        let projection = coordinator.presence_projection();
        assert_eq!(projection.session_id, SESSION_ID);
        assert_eq!(projection.segment_id, INITIAL_SEGMENT_ID);
        assert!(projection.context_epoch > last_epoch);
        assert!(projection.sequence >= last_presence_sequence);
        last_epoch = projection.context_epoch;
        last_presence_sequence = projection.sequence;
    }

    assert_eq!(virtual_now_ms, FOUR_HOURS_MS);
    assert_eq!(rotations, 20);
    assert_eq!(pause_resume_cycles, 10);
    assert_eq!(window_changes, 5);
    assert_eq!(coordinator.active_identity().epoch, 21);

    let quarantine_root = tempfile::tempdir().expect("quarantine root");
    let quarantine_path = quarantine_root.path().join("quarantine.json");
    let mut sidecar = RealtimeSidecarSupervisor::load(
        quarantine_path.clone(),
        "model-v1".to_owned(),
        PERSONA_DIGEST.to_owned(),
    );
    sidecar.begin_session();
    assert_eq!(
        sidecar.record_failure("LOCAL_SIDECAR_PROCESS_EXIT", false),
        RealtimeSidecarDecision::RestartOnce
    );
    coordinator
        .apply(RealtimeCoordinatorEvent::BackendFailed)
        .expect("mark failed segment");
    coordinator
        .apply(RealtimeCoordinatorEvent::RecoverLocalSegment {
            segment_id: RECOVERY_SEGMENT_ID.to_owned(),
            persona_digest: PERSONA_DIGEST.to_owned(),
        })
        .expect("recover exactly once");
    assert_eq!(coordinator.active_identity().epoch, 1);
    assert_eq!(coordinator.active_segment().ordinal, 2);
    assert_eq!(
        coordinator.active_segment().creation_reason,
        BackendSegmentCreationReason::AutomaticRecovery
    );
    assert_eq!(coordinator.backend(), RealtimeBackendKind::LocalMiniCpmO45);

    let active_reload_projection =
        serde_json::to_value(coordinator.presence_projection()).expect("reload projection");
    let active_sidecar_projection =
        serde_json::to_value(sidecar.snapshot()).expect("sidecar projection");
    assert_projection_is_public(&active_reload_projection);
    assert_projection_is_public(&active_sidecar_projection);

    assert_eq!(
        sidecar.record_failure("LOCAL_SIDECAR_PROTOCOL_DISCONNECTED", true),
        RealtimeSidecarDecision::Quarantine
    );
    assert!(sidecar.snapshot().context_interrupted);
    coordinator
        .apply(RealtimeCoordinatorEvent::BackendFailed)
        .expect("second failure");
    let restored = RealtimeSidecarSupervisor::load(
        quarantine_path,
        "model-v1".to_owned(),
        PERSONA_DIGEST.to_owned(),
    )
    .snapshot();
    assert!(restored.restart_used);
    assert!(restored.quarantined);
    assert_eq!(restored.failure_count, 2);
    assert_eq!(
        coordinator.backend(),
        RealtimeBackendKind::LocalMiniCpmO45,
        "quarantine must not select CPU or Cloud"
    );

    let resource_levels = drive_resource_governor();
    assert_eq!(
        resource_levels,
        [
            RealtimeResourceLevel::Normal,
            RealtimeResourceLevel::Pressure,
            RealtimeResourceLevel::Normal,
            RealtimeResourceLevel::High,
            RealtimeResourceLevel::Normal,
            RealtimeResourceLevel::Critical,
            RealtimeResourceLevel::Normal,
            RealtimeResourceLevel::DeviceRemoved,
        ]
    );

    coordinator
        .apply(RealtimeCoordinatorEvent::End)
        .expect("terminal Session");
    assert!(!coordinator.media_generation_enabled());
    assert!(!coordinator.accepts_result(SESSION_ID, RECOVERY_SEGMENT_ID, 1));
    context.end_session(SESSION_ID);
    assert!(context
        .build(
            SESSION_ID,
            RECOVERY_SEGMENT_ID,
            1,
            RECOVERY_SEGMENT_ID,
            2,
            RealtimeActivityProfile::Focus,
            None,
        )
        .is_err());
}

#[test]
fn wall_clock_gate_is_strict_and_records_only_sanitized_fields() {
    let harness = include_str!("../../../scripts/test-realtime-companion-soak.ps1");
    let probe = include_str!("../../scripts/probe-realtime-companion-soak.mjs");

    assert!(harness.contains("[ValidateRange(4.0, 168.0)]"));
    assert!(harness.contains("ConfirmDataDirectory"));
    assert!(harness.contains("Stop-ProcessTree"));
    assert!(harness.contains("process_cleanup_confirmed"));
    assert!(harness.contains("$childEnvironment = [ordered]@{"));
    assert!(harness.contains("$previousChildEnvironment = @{}"));
    assert!(harness.contains("[EnvironmentVariableTarget]::Process"));
    assert!(!harness.contains("$startInfo.Environment["));
    assert!(!harness.contains("$startInfo.EnvironmentVariables["));
    assert!(probe.contains("const REPORT_KEYS"));
    assert!(probe.contains("\"observed_duration_seconds\""));
    assert!(probe.contains("\"digest_count\""));
    assert!(probe.contains("\"proposal_counts\""));
    assert!(probe.contains("local_mini_cpm_o45"));
    assert!(probe.contains("realtime_local_readiness_get"));
    assert!(probe.contains("realtime_worker_status"));
    assert!(!probe.contains("stable_caption_summary"));
    assert!(!probe.contains("normalized_text"));
    assert!(!probe.contains("api_key"));
    assert!(!probe.contains("raw_media"));
}

#[test]
fn native_presence_soak_avoids_case_colliding_process_environment_maps() {
    let harness = include_str!("../../../scripts/test-presence-soak.ps1");

    assert!(harness.contains("$childEnvironment = [ordered]@{"));
    assert!(harness.contains("$previousChildEnvironment = @{}"));
    assert!(harness.contains("[EnvironmentVariableTarget]::Process"));
    assert!(!harness.contains("$startInfo.Environment["));
    assert!(!harness.contains("$startInfo.EnvironmentVariables["));
}

fn drive_resource_governor() -> Vec<RealtimeResourceLevel> {
    let mut governor = RealtimeResourceGovernor::new(0);
    let mut levels = vec![governor.snapshot().policy.level];
    observe_level(&mut governor, sample(80), 2_000, &mut levels);
    recover(&mut governor, 7_000, &mut levels);
    observe_level(&mut governor, sample(90), 13_000, &mut levels);
    recover(&mut governor, 18_000, &mut levels);
    observe_level(&mut governor, sample(96), 22_001, &mut levels);
    recover(&mut governor, 27_001, &mut levels);
    let mut removed = sample(20);
    removed.device_removed = true;
    observe_level(&mut governor, removed, 31_002, &mut levels);
    assert!(governor.snapshot().policy.media_paused);
    assert!(governor
        .observe(sample(10), 40_000)
        .expect("terminal observe")
        .is_none());
    levels
}

fn recover(
    governor: &mut RealtimeResourceGovernor,
    started_at_ms: u64,
    levels: &mut Vec<RealtimeResourceLevel>,
) {
    for offset in 0..5 {
        observe_level(governor, sample(20), started_at_ms + offset * 1_000, levels);
    }
}

fn observe_level(
    governor: &mut RealtimeResourceGovernor,
    sample: RealtimeResourceSample,
    now_ms: u64,
    levels: &mut Vec<RealtimeResourceLevel>,
) {
    if let Some(policy) = governor.observe(sample, now_ms).expect("resource sample") {
        levels.push(policy.level);
    }
}

fn sample(percent: u64) -> RealtimeResourceSample {
    RealtimeResourceSample {
        budget_bytes: Some(100),
        current_usage_bytes: Some(percent),
        allocation_failure_count: 0,
        inference_latency_ms: 10,
        capture_frame_backlog: 0,
        device_removed: false,
        renderer_healthy: true,
        target_changed: false,
    }
}

fn assert_projection_is_public(value: &Value) {
    let serialized = serde_json::to_string(value).expect("serialize projection");
    for forbidden in [
        "credential",
        "api_key",
        "raw_media",
        "transcript",
        "provider_hidden",
        "reasoning",
        "application_path",
    ] {
        assert!(!serialized.contains(forbidden), "{forbidden}");
    }
    assert!(serialized.len() < 4_096);
}
