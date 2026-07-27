mod backend;
mod media;
mod persona;
mod protocol;
mod provider;
mod runtime;
mod session;
mod transport;

pub use backend::{
    validate_backend_start, BackendStartRequest, LocalOmniLaunch, RealtimeActivityProfile,
    RealtimeBackendKind, RealtimeCandidateDecision, RealtimeCloudProviderKind,
    RealtimeDialogueCandidate, RealtimeInteractionIntensity, RealtimeVoiceOutput,
};
pub use media::{
    resample_pcm16, AudioPacket, AudioPlayback, MicrophoneCapture, VideoCapture, VideoFrame,
};
pub use persona::{validate_persona_snapshot, PersonaSnapshotError, ValidatedRealtimePersona};
pub use protocol::{
    read_frame, write_frame, HostCommand, SecretString, WorkerEvent, MAX_CONTROL_FRAME_BYTES,
};
pub use provider::{CaptionSpeaker, GeminiProtocol, GlmProtocol, ProviderOutput, RealtimeProtocol};
pub use runtime::{RealtimeRuntime, RuntimeCommand, RuntimeLaunch};
pub use session::{validate_start, StartValidationError};
pub use transport::{ProviderSocket, ProviderTransportError};
