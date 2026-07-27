use thiserror::Error;

pub const MEDIA_HEADER_BYTES: usize = 36;
pub const MEDIA_PROTOCOL_VERSION: u16 = 1;
pub const MICROPHONE_PCM16_PACKET_BYTES: u32 = 640;
pub const MAX_APPLICATION_PCM16_BYTES: u32 = 32_000;
pub const MAX_JPEG_BYTES: u32 = 8 * 1024 * 1024;
pub const MAX_BGRA_BYTES: u32 = 32 * 1024 * 1024;
pub const MAX_MEDIA_BUFFER_BYTES: usize = 48 * 1024 * 1024;
const MEDIA_MAGIC: [u8; 4] = *b"FOMI";
const MICROPHONE_CADENCE_US: u64 = 20_000;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[repr(u16)]
pub enum MediaKind {
    MicrophonePcm16 = 1,
    ApplicationPcm16 = 2,
    Jpeg = 3,
    Bgra = 4,
}

impl TryFrom<u16> for MediaKind {
    type Error = MediaProtocolError;

    fn try_from(value: u16) -> Result<Self, Self::Error> {
        match value {
            1 => Ok(Self::MicrophonePcm16),
            2 => Ok(Self::ApplicationPcm16),
            3 => Ok(Self::Jpeg),
            4 => Ok(Self::Bgra),
            _ => Err(MediaProtocolError::UnknownKind),
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct MediaHeader {
    pub version: u16,
    pub kind: MediaKind,
    pub session_epoch: u64,
    pub sequence: u64,
    pub timestamp_us: u64,
    pub payload_length: u32,
}

impl MediaHeader {
    pub fn encode(self) -> [u8; MEDIA_HEADER_BYTES] {
        let mut output = [0_u8; MEDIA_HEADER_BYTES];
        output[0..4].copy_from_slice(&MEDIA_MAGIC);
        output[4..6].copy_from_slice(&self.version.to_le_bytes());
        output[6..8].copy_from_slice(&(self.kind as u16).to_le_bytes());
        output[8..16].copy_from_slice(&self.session_epoch.to_le_bytes());
        output[16..24].copy_from_slice(&self.sequence.to_le_bytes());
        output[24..32].copy_from_slice(&self.timestamp_us.to_le_bytes());
        output[32..36].copy_from_slice(&self.payload_length.to_le_bytes());
        output
    }

    pub fn decode(bytes: &[u8]) -> Result<Self, MediaProtocolError> {
        if bytes.len() != MEDIA_HEADER_BYTES || bytes[0..4] != MEDIA_MAGIC {
            return Err(MediaProtocolError::InvalidHeader);
        }
        let version = u16::from_le_bytes([bytes[4], bytes[5]]);
        if version != MEDIA_PROTOCOL_VERSION {
            return Err(MediaProtocolError::UnsupportedVersion);
        }
        Ok(Self {
            version,
            kind: MediaKind::try_from(u16::from_le_bytes([bytes[6], bytes[7]]))?,
            session_epoch: u64::from_le_bytes(
                bytes[8..16]
                    .try_into()
                    .map_err(|_| MediaProtocolError::InvalidHeader)?,
            ),
            sequence: u64::from_le_bytes(
                bytes[16..24]
                    .try_into()
                    .map_err(|_| MediaProtocolError::InvalidHeader)?,
            ),
            timestamp_us: u64::from_le_bytes(
                bytes[24..32]
                    .try_into()
                    .map_err(|_| MediaProtocolError::InvalidHeader)?,
            ),
            payload_length: u32::from_le_bytes(
                bytes[32..36]
                    .try_into()
                    .map_err(|_| MediaProtocolError::InvalidHeader)?,
            ),
        })
    }
}

#[derive(Debug, Error)]
pub enum MediaProtocolError {
    #[error("invalid media header")]
    InvalidHeader,
    #[error("unsupported media protocol version")]
    UnsupportedVersion,
    #[error("unknown media kind")]
    UnknownKind,
    #[error("stale media epoch")]
    StaleEpoch,
    #[error("stale media sequence")]
    StaleSequence,
    #[error("stale media timestamp")]
    StaleTimestamp,
    #[error("invalid microphone cadence")]
    InvalidMicrophoneCadence,
    #[error("media payload is outside its allowed bound")]
    PayloadBound,
    #[error("BGRA dimensions are not valid for the active context")]
    BgraDimensions,
    #[error("media payload length changed after validation")]
    PayloadLengthMismatch,
    #[error("invalid local media pipe name")]
    InvalidPipeName,
    #[error("local media pipe operation failed: {0}")]
    Pipe(#[source] std::io::Error),
    #[error("connected media client does not match the managed child")]
    ClientIdentityMismatch,
}

impl PartialEq for MediaProtocolError {
    fn eq(&self, other: &Self) -> bool {
        use MediaProtocolError::{
            BgraDimensions, ClientIdentityMismatch, InvalidHeader, InvalidMicrophoneCadence,
            InvalidPipeName, PayloadBound, PayloadLengthMismatch, Pipe, StaleEpoch, StaleSequence,
            StaleTimestamp, UnknownKind, UnsupportedVersion,
        };
        match (self, other) {
            (InvalidHeader, InvalidHeader)
            | (UnsupportedVersion, UnsupportedVersion)
            | (UnknownKind, UnknownKind)
            | (StaleEpoch, StaleEpoch)
            | (StaleSequence, StaleSequence)
            | (StaleTimestamp, StaleTimestamp)
            | (InvalidMicrophoneCadence, InvalidMicrophoneCadence)
            | (PayloadBound, PayloadBound)
            | (BgraDimensions, BgraDimensions)
            | (PayloadLengthMismatch, PayloadLengthMismatch)
            | (InvalidPipeName, InvalidPipeName)
            | (ClientIdentityMismatch, ClientIdentityMismatch) => true,
            (Pipe(left), Pipe(right)) => left.raw_os_error() == right.raw_os_error(),
            _ => false,
        }
    }
}

impl Eq for MediaProtocolError {}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct MediaValidationState {
    session_epoch: u64,
    last_sequence: u64,
    last_timestamp_us: u64,
    last_microphone_timestamp_us: u64,
    video_width: u32,
    video_height: u32,
}

impl MediaValidationState {
    pub fn rotate_epoch(&mut self, session_epoch: u64) -> Result<(), MediaProtocolError> {
        if session_epoch == 0 {
            return Err(MediaProtocolError::StaleEpoch);
        }
        self.session_epoch = session_epoch;
        self.last_sequence = 0;
        self.last_timestamp_us = 0;
        self.last_microphone_timestamp_us = 0;
        Ok(())
    }

    pub fn configure_video(&mut self, width: u32, height: u32) -> Result<(), MediaProtocolError> {
        let bytes = u64::from(width)
            .checked_mul(u64::from(height))
            .and_then(|pixels| pixels.checked_mul(4));
        if (width == 0) != (height == 0)
            || bytes.is_none()
            || bytes.is_some_and(|value| value > u64::from(MAX_BGRA_BYTES))
        {
            return Err(MediaProtocolError::BgraDimensions);
        }
        self.video_width = width;
        self.video_height = height;
        Ok(())
    }

    pub fn validate_before_allocation(
        &self,
        header: MediaHeader,
    ) -> Result<(), MediaProtocolError> {
        if self.session_epoch == 0 || header.session_epoch != self.session_epoch {
            return Err(MediaProtocolError::StaleEpoch);
        }
        if header.sequence == 0 || header.sequence <= self.last_sequence {
            return Err(MediaProtocolError::StaleSequence);
        }
        if header.timestamp_us == 0 || header.timestamp_us < self.last_timestamp_us {
            return Err(MediaProtocolError::StaleTimestamp);
        }
        if header.kind == MediaKind::MicrophonePcm16
            && self.last_microphone_timestamp_us != 0
            && self
                .last_microphone_timestamp_us
                .checked_add(MICROPHONE_CADENCE_US)
                != Some(header.timestamp_us)
        {
            return Err(MediaProtocolError::InvalidMicrophoneCadence);
        }
        match header.kind {
            MediaKind::MicrophonePcm16
                if header.payload_length != MICROPHONE_PCM16_PACKET_BYTES =>
            {
                Err(MediaProtocolError::PayloadBound)
            }
            MediaKind::ApplicationPcm16
                if header.payload_length == 0
                    || header.payload_length > MAX_APPLICATION_PCM16_BYTES
                    || !header.payload_length.is_multiple_of(2) =>
            {
                Err(MediaProtocolError::PayloadBound)
            }
            MediaKind::Jpeg
                if header.payload_length == 0 || header.payload_length > MAX_JPEG_BYTES =>
            {
                Err(MediaProtocolError::PayloadBound)
            }
            MediaKind::Bgra => {
                let expected = u64::from(self.video_width)
                    .checked_mul(u64::from(self.video_height))
                    .and_then(|pixels| pixels.checked_mul(4))
                    .filter(|bytes| *bytes > 0 && *bytes <= u64::from(MAX_BGRA_BYTES))
                    .ok_or(MediaProtocolError::BgraDimensions)?;
                if u64::from(header.payload_length) != expected {
                    return Err(MediaProtocolError::BgraDimensions);
                }
                Ok(())
            }
            _ => Ok(()),
        }
    }

    pub fn accept(
        &mut self,
        header: MediaHeader,
        payload_length: usize,
    ) -> Result<(), MediaProtocolError> {
        self.validate_before_allocation(header)?;
        if payload_length != header.payload_length as usize {
            return Err(MediaProtocolError::PayloadLengthMismatch);
        }
        self.last_sequence = header.sequence;
        self.last_timestamp_us = header.timestamp_us;
        if header.kind == MediaKind::MicrophonePcm16 {
            self.last_microphone_timestamp_us = header.timestamp_us;
        }
        Ok(())
    }
}

pub fn validate_pipe_token(pipe_name: &str) -> Result<(), MediaProtocolError> {
    if pipe_name.is_empty()
        || pipe_name.len() > 128
        || !pipe_name
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.'))
    {
        return Err(MediaProtocolError::InvalidPipeName);
    }
    Ok(())
}

#[cfg(target_os = "windows")]
mod windows_pipe {
    use super::{validate_pipe_token, MediaProtocolError};
    use std::ffi::OsStr;
    use std::os::windows::ffi::OsStrExt;
    use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle, RawHandle};
    use std::ptr;
    use windows_sys::Win32::Foundation::{
        GetLastError, ERROR_PIPE_CONNECTED, INVALID_HANDLE_VALUE,
    };
    use windows_sys::Win32::Storage::FileSystem::{
        FILE_FLAG_FIRST_PIPE_INSTANCE, PIPE_ACCESS_OUTBOUND,
    };
    use windows_sys::Win32::System::Pipes::{
        ConnectNamedPipe, CreateNamedPipeW, GetNamedPipeClientProcessId, PIPE_READMODE_BYTE,
        PIPE_REJECT_REMOTE_CLIENTS, PIPE_TYPE_BYTE, PIPE_WAIT,
    };

    #[derive(Debug)]
    pub struct HostMediaPipe {
        name: String,
        handle: OwnedHandle,
    }

    impl HostMediaPipe {
        pub fn create(name: &str) -> Result<Self, MediaProtocolError> {
            validate_pipe_token(name)?;
            let path = format!(r"\\.\pipe\{name}");
            let mut wide = OsStr::new(&path).encode_wide().collect::<Vec<_>>();
            wide.push(0);
            let handle = unsafe {
                CreateNamedPipeW(
                    wide.as_ptr(),
                    PIPE_ACCESS_OUTBOUND | FILE_FLAG_FIRST_PIPE_INSTANCE,
                    PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
                    1,
                    1024 * 1024,
                    0,
                    5_000,
                    ptr::null(),
                )
            };
            if handle == INVALID_HANDLE_VALUE {
                return Err(MediaProtocolError::Pipe(std::io::Error::last_os_error()));
            }
            let handle = unsafe { OwnedHandle::from_raw_handle(handle as RawHandle) };
            Ok(Self {
                name: name.to_owned(),
                handle,
            })
        }

        pub fn name(&self) -> &str {
            &self.name
        }

        pub fn connect_and_verify(&self, expected_pid: u32) -> Result<(), MediaProtocolError> {
            let handle = self.handle.as_raw_handle() as _;
            let connected = unsafe { ConnectNamedPipe(handle, ptr::null_mut()) };
            if connected == 0 && unsafe { GetLastError() } != ERROR_PIPE_CONNECTED {
                return Err(MediaProtocolError::Pipe(std::io::Error::last_os_error()));
            }
            let mut client_pid = 0_u32;
            if unsafe { GetNamedPipeClientProcessId(handle, &mut client_pid) } == 0 {
                return Err(MediaProtocolError::Pipe(std::io::Error::last_os_error()));
            }
            if client_pid != expected_pid {
                return Err(MediaProtocolError::ClientIdentityMismatch);
            }
            Ok(())
        }

        pub fn as_raw_handle(&self) -> RawHandle {
            self.handle.as_raw_handle()
        }
    }

    #[cfg(test)]
    mod tests {
        use super::HostMediaPipe;
        use std::fs::File;
        use std::thread;

        #[test]
        fn host_pipe_is_first_instance_and_rejects_duplicates() {
            let name = format!("fairy-omni-pipe-test-{}", std::process::id());
            let first = HostMediaPipe::create(&name).expect("create first local media pipe");
            assert_eq!(first.name(), name);
            assert!(HostMediaPipe::create(&name).is_err());
        }

        #[test]
        fn host_pipe_verifies_the_connected_process_id() {
            let name = format!("fairy-omni-peer-test-{}", std::process::id());
            let pipe = HostMediaPipe::create(&name).expect("create peer test pipe");
            let path = format!(r"\\.\pipe\{name}");
            let client = thread::spawn(move || File::open(path).expect("connect pipe client"));
            pipe.connect_and_verify(std::process::id())
                .expect("verify connected client process");
            drop(client.join().expect("join pipe client"));
        }
    }
}

#[cfg(target_os = "windows")]
pub use windows_pipe::HostMediaPipe;

#[cfg(test)]
mod tests {
    use super::*;

    fn microphone(sequence: u64, timestamp_us: u64, epoch: u64) -> MediaHeader {
        MediaHeader {
            version: MEDIA_PROTOCOL_VERSION,
            kind: MediaKind::MicrophonePcm16,
            session_epoch: epoch,
            sequence,
            timestamp_us,
            payload_length: MICROPHONE_PCM16_PACKET_BYTES,
        }
    }

    #[test]
    fn media_header_round_trips_exactly() {
        let expected = microphone(4, 80_000, 2);
        assert_eq!(
            MediaHeader::decode(&expected.encode()).expect("decode media header"),
            expected
        );
    }

    #[test]
    fn media_header_rejects_magic_version_and_kind() {
        let mut bytes = microphone(1, 20_000, 1).encode();
        bytes[0] = b'X';
        assert_eq!(
            MediaHeader::decode(&bytes),
            Err(MediaProtocolError::InvalidHeader)
        );

        let mut bytes = microphone(1, 20_000, 1).encode();
        bytes[4..6].copy_from_slice(&2_u16.to_le_bytes());
        assert_eq!(
            MediaHeader::decode(&bytes),
            Err(MediaProtocolError::UnsupportedVersion)
        );

        let mut bytes = microphone(1, 20_000, 1).encode();
        bytes[6..8].copy_from_slice(&9_u16.to_le_bytes());
        assert_eq!(
            MediaHeader::decode(&bytes),
            Err(MediaProtocolError::UnknownKind)
        );
    }

    #[test]
    fn validator_checks_bounds_before_accepting_payload() {
        let mut state = MediaValidationState::default();
        state.rotate_epoch(3).expect("rotate epoch");
        state.configure_video(640, 360).expect("configure video");

        let first = microphone(1, 20_000, 3);
        state
            .validate_before_allocation(first)
            .expect("validate first microphone frame");
        state
            .accept(first, MICROPHONE_PCM16_PACKET_BYTES as usize)
            .expect("accept first microphone frame");

        assert_eq!(
            state.validate_before_allocation(microphone(1, 40_000, 3)),
            Err(MediaProtocolError::StaleSequence)
        );
        assert_eq!(
            state.validate_before_allocation(microphone(2, 41_000, 3)),
            Err(MediaProtocolError::InvalidMicrophoneCadence)
        );
        assert_eq!(
            state.validate_before_allocation(microphone(2, 40_000, 2)),
            Err(MediaProtocolError::StaleEpoch)
        );

        let oversized = MediaHeader {
            kind: MediaKind::Jpeg,
            sequence: 2,
            timestamp_us: 40_000,
            payload_length: MAX_JPEG_BYTES + 1,
            ..first
        };
        assert_eq!(
            state.validate_before_allocation(oversized),
            Err(MediaProtocolError::PayloadBound)
        );
    }

    #[test]
    fn rotation_resets_sequence_without_carrying_media_identity() {
        let mut state = MediaValidationState::default();
        state.rotate_epoch(1).expect("first epoch");
        let first = microphone(9, 180_000, 1);
        state
            .accept(first, MICROPHONE_PCM16_PACKET_BYTES as usize)
            .expect("accept old epoch");
        state.rotate_epoch(2).expect("second epoch");
        state
            .accept(
                microphone(1, 20_000, 2),
                MICROPHONE_PCM16_PACKET_BYTES as usize,
            )
            .expect("new epoch starts at sequence one");
    }

    #[test]
    fn pipe_tokens_cannot_escape_the_local_namespace() {
        assert!(validate_pipe_token("fairy-safe_1.test").is_ok());
        assert_eq!(
            validate_pipe_token(r"..\other"),
            Err(MediaProtocolError::InvalidPipeName)
        );
        assert_eq!(
            validate_pipe_token(r"\\server\pipe\x"),
            Err(MediaProtocolError::InvalidPipeName)
        );
    }
}
