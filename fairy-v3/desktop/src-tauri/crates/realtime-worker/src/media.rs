use std::collections::VecDeque;
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use cpal::{SampleFormat, Stream, StreamConfig};
use image::codecs::jpeg::JpegEncoder;
use image::imageops::FilterType;
use image::{DynamicImage, RgbaImage};
use thiserror::Error;
use zeroize::{Zeroize, Zeroizing};

const AUDIO_QUEUE_DEPTH: usize = 8;
const PROCESS_AUDIO_RING_SAMPLES: usize = 48_000 * 2 * 2;
const MAX_VIDEO_DIMENSION: u32 = 1_280;
const MAX_VIDEO_JPEG_BYTES: usize = 2 * 1024 * 1024;

#[derive(Clone, Debug)]
pub struct AudioPacket {
    pub pcm16: Vec<i16>,
    pub sample_rate: u32,
    pub captured_at: Instant,
}

impl Drop for AudioPacket {
    fn drop(&mut self) {
        self.pcm16.zeroize();
    }
}

pub struct MicrophoneCapture {
    _stream: Stream,
    receiver: mpsc::Receiver<AudioPacket>,
    failed: Arc<AtomicBool>,
    pub sample_rate: u32,
}

#[cfg(target_os = "windows")]
pub struct ProcessLoopbackCapture {
    backend: flexaudio_os_windows::WasapiProcessBackend,
    consumer: flexaudio_core::RawConsumer,
    scratch: Vec<f32>,
}

pub struct AudioPlayback {
    _stream: Stream,
    queue: Arc<Mutex<VecDeque<i16>>>,
    output_rate: u32,
    max_samples: usize,
}

#[derive(Clone, Debug)]
pub struct VideoFrame {
    pub sequence: u64,
    pub jpeg: Vec<u8>,
    pub width: u32,
    pub height: u32,
    pub captured_at: Instant,
}

pub struct VideoCapture {
    latest: Arc<Mutex<Option<Zeroizing<VideoFrameSecret>>>>,
    stopped: Arc<AtomicBool>,
    overwritten_frames: Arc<AtomicU32>,
    capture_failures: Arc<AtomicU32>,
    worker: Option<thread::JoinHandle<()>>,
}

struct VideoFrameSecret(VideoFrame);

impl Zeroize for VideoFrameSecret {
    fn zeroize(&mut self) {
        self.0.jpeg.zeroize();
        self.0.sequence = 0;
        self.0.width = 0;
        self.0.height = 0;
    }
}

#[derive(Debug, Error)]
pub enum MediaError {
    #[error("microphone is unavailable")]
    MicrophoneUnavailable,
    #[error("microphone format is unsupported")]
    AudioFormatUnsupported,
    #[error("selected game window is unavailable")]
    CaptureSourceUnavailable,
    #[error("game window capture failed")]
    CaptureFailed,
    #[error("game process loopback is unavailable")]
    ProcessLoopbackUnavailable,
}

impl MicrophoneCapture {
    pub fn start() -> Result<Self, MediaError> {
        let device = cpal::default_host()
            .default_input_device()
            .ok_or(MediaError::MicrophoneUnavailable)?;
        let supported = device
            .default_input_config()
            .map_err(|_| MediaError::MicrophoneUnavailable)?;
        let sample_format = supported.sample_format();
        let config: StreamConfig = supported.into();
        let sample_rate = config.sample_rate.0;
        let channels = usize::from(config.channels);
        if channels == 0 || !(8_000..=192_000).contains(&sample_rate) {
            return Err(MediaError::AudioFormatUnsupported);
        }
        let (sender, receiver) = mpsc::sync_channel(AUDIO_QUEUE_DEPTH);
        let failed = Arc::new(AtomicBool::new(false));
        let stream_failed = Arc::clone(&failed);
        let stream = match sample_format {
            SampleFormat::I16 => device.build_input_stream(
                &config,
                move |data: &[i16], _| {
                    send_audio_packet(&sender, mono_i16(data, channels), sample_rate)
                },
                move |_| {
                    stream_failed.store(true, Ordering::Release);
                },
                None,
            ),
            SampleFormat::F32 => device.build_input_stream(
                &config,
                move |data: &[f32], _| {
                    let mono = mono_f32(data, channels)
                        .into_iter()
                        .map(|sample| {
                            (sample.clamp(-1.0, 1.0) * f32::from(i16::MAX)).round() as i16
                        })
                        .collect();
                    send_audio_packet(&sender, mono, sample_rate)
                },
                move |_| {
                    stream_failed.store(true, Ordering::Release);
                },
                None,
            ),
            SampleFormat::U16 => device.build_input_stream(
                &config,
                move |data: &[u16], _| {
                    let converted = data
                        .iter()
                        .map(|sample| (*sample as i32 - 32_768) as i16)
                        .collect::<Vec<_>>();
                    send_audio_packet(&sender, mono_i16(&converted, channels), sample_rate)
                },
                move |_| {
                    stream_failed.store(true, Ordering::Release);
                },
                None,
            ),
            _ => return Err(MediaError::AudioFormatUnsupported),
        }
        .map_err(|_| MediaError::MicrophoneUnavailable)?;
        stream
            .play()
            .map_err(|_| MediaError::MicrophoneUnavailable)?;
        Ok(Self {
            _stream: stream,
            receiver,
            failed,
            sample_rate,
        })
    }

    pub fn try_recv(&self) -> Option<AudioPacket> {
        self.receiver.try_recv().ok()
    }

    pub fn is_failed(&self) -> bool {
        self.failed.load(Ordering::Acquire)
    }
}

#[cfg(target_os = "windows")]
impl ProcessLoopbackCapture {
    pub fn start(source_id: u64) -> Result<Self, MediaError> {
        Self::start_process_tree(process_id_for_window(source_id)?)
    }

    pub fn start_process_tree(process_id: u32) -> Result<Self, MediaError> {
        use flexaudio_core::types::ProcessMode;
        use flexaudio_core::{raw_ring, CaptureBackend, RawSink};
        use flexaudio_os_windows::WasapiProcessBackend;

        if process_id == 0 {
            return Err(MediaError::ProcessLoopbackUnavailable);
        }
        let (producer, consumer) = raw_ring(PROCESS_AUDIO_RING_SAMPLES);
        let sink = RawSink::new(producer, 48_000, 2);
        let mut backend = WasapiProcessBackend::new(process_id, ProcessMode::Include);
        backend
            .start(sink)
            .map_err(|_| MediaError::ProcessLoopbackUnavailable)?;
        Ok(Self {
            backend,
            consumer,
            scratch: vec![0.0; 48_000 * 2 / 5],
        })
    }

    pub fn try_recv(&mut self) -> Option<AudioPacket> {
        let read = self.consumer.pop_slice(&mut self.scratch);
        let usable = read - (read % 2);
        if usable == 0 {
            return None;
        }
        let pcm16 = mono_f32(&self.scratch[..usable], 2)
            .into_iter()
            .map(|sample| (sample.clamp(-1.0, 1.0) * f32::from(i16::MAX)).round() as i16)
            .collect();
        Some(AudioPacket {
            pcm16,
            sample_rate: 48_000,
            captured_at: Instant::now(),
        })
    }
}

#[cfg(target_os = "windows")]
impl Drop for ProcessLoopbackCapture {
    fn drop(&mut self) {
        use flexaudio_core::CaptureBackend;
        self.backend.stop();
        self.scratch.zeroize();
    }
}

#[cfg(not(target_os = "windows"))]
pub struct ProcessLoopbackCapture;

#[cfg(not(target_os = "windows"))]
impl ProcessLoopbackCapture {
    pub fn start(_source_id: u64) -> Result<Self, MediaError> {
        Err(MediaError::ProcessLoopbackUnavailable)
    }

    pub fn start_process_tree(_process_id: u32) -> Result<Self, MediaError> {
        Err(MediaError::ProcessLoopbackUnavailable)
    }

    pub fn try_recv(&mut self) -> Option<AudioPacket> {
        None
    }
}

impl AudioPlayback {
    pub fn start() -> Result<Self, MediaError> {
        let device = cpal::default_host()
            .default_output_device()
            .ok_or(MediaError::MicrophoneUnavailable)?;
        let supported = device
            .default_output_config()
            .map_err(|_| MediaError::AudioFormatUnsupported)?;
        let sample_format = supported.sample_format();
        let config: StreamConfig = supported.into();
        let output_rate = config.sample_rate.0;
        let channels = usize::from(config.channels);
        let queue = Arc::new(Mutex::new(VecDeque::new()));
        let callback_queue = Arc::clone(&queue);
        let stream = match sample_format {
            SampleFormat::I16 => device.build_output_stream(
                &config,
                move |data: &mut [i16], _| fill_i16(data, channels, &callback_queue),
                |_| {},
                None,
            ),
            SampleFormat::F32 => device.build_output_stream(
                &config,
                move |data: &mut [f32], _| fill_f32(data, channels, &callback_queue),
                |_| {},
                None,
            ),
            SampleFormat::U16 => device.build_output_stream(
                &config,
                move |data: &mut [u16], _| fill_u16(data, channels, &callback_queue),
                |_| {},
                None,
            ),
            _ => return Err(MediaError::AudioFormatUnsupported),
        }
        .map_err(|_| MediaError::AudioFormatUnsupported)?;
        stream
            .play()
            .map_err(|_| MediaError::AudioFormatUnsupported)?;
        Ok(Self {
            _stream: stream,
            queue,
            output_rate,
            max_samples: output_rate as usize * 5,
        })
    }

    pub fn enqueue_pcm16(&self, pcm: &[i16], input_rate: u32) {
        let samples = resample_pcm16(pcm, input_rate, self.output_rate);
        if let Ok(mut queue) = self.queue.lock() {
            let overflow = queue
                .len()
                .saturating_add(samples.len())
                .saturating_sub(self.max_samples);
            let remove = overflow.min(queue.len());
            queue.drain(..remove);
            queue.extend(samples);
        }
    }

    pub fn clear(&self) {
        if let Ok(mut queue) = self.queue.lock() {
            for sample in queue.iter_mut() {
                *sample = 0;
            }
            queue.clear();
        }
    }
}

impl Drop for AudioPlayback {
    fn drop(&mut self) {
        self.clear();
    }
}

impl VideoCapture {
    pub fn start(source_id: u64, capture_fps: u16) -> Result<Self, MediaError> {
        if !(1..=30).contains(&capture_fps) {
            return Err(MediaError::CaptureFailed);
        }
        ensure_window_exists(source_id)?;
        let latest: Arc<Mutex<Option<Zeroizing<VideoFrameSecret>>>> = Arc::new(Mutex::new(None));
        let stopped = Arc::new(AtomicBool::new(false));
        let overwritten_frames = Arc::new(AtomicU32::new(0));
        let capture_failures = Arc::new(AtomicU32::new(0));
        let thread_latest = Arc::clone(&latest);
        let thread_stopped = Arc::clone(&stopped);
        let thread_overwritten_frames = Arc::clone(&overwritten_frames);
        let thread_capture_failures = Arc::clone(&capture_failures);
        let worker = thread::Builder::new()
            .name("fairy-realtime-wgc".to_owned())
            .spawn(move || {
                let interval = Duration::from_millis(1_000 / u64::from(capture_fps));
                let mut sequence = 0_u64;
                while !thread_stopped.load(Ordering::Acquire) {
                    let started = Instant::now();
                    if let Ok(frame) = capture_window(source_id, sequence + 1) {
                        sequence += 1;
                        if let Ok(mut slot) = thread_latest.lock() {
                            if let Some(mut previous) = slot.take() {
                                previous.zeroize();
                                saturating_increment(&thread_overwritten_frames);
                            }
                            *slot = Some(Zeroizing::new(VideoFrameSecret(frame)));
                        }
                    } else {
                        saturating_increment(&thread_capture_failures);
                    }
                    if let Some(remaining) = interval.checked_sub(started.elapsed()) {
                        thread::sleep(remaining);
                    }
                }
            })
            .map_err(|_| MediaError::CaptureFailed)?;
        Ok(Self {
            latest,
            stopped,
            overwritten_frames,
            capture_failures,
            worker: Some(worker),
        })
    }

    pub fn take_latest(&self) -> Option<VideoFrame> {
        self.latest
            .lock()
            .ok()
            .and_then(|mut slot| slot.take())
            .map(|secret| secret.0.clone())
    }

    pub fn take_health_sample(&self) -> (u16, u8) {
        let backlog = self.overwritten_frames.swap(0, Ordering::AcqRel);
        let failures = self.capture_failures.swap(0, Ordering::AcqRel);
        (
            u16::try_from(backlog).unwrap_or(u16::MAX),
            u8::try_from(failures).unwrap_or(u8::MAX),
        )
    }
}

impl Drop for VideoCapture {
    fn drop(&mut self) {
        self.stopped.store(true, Ordering::Release);
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
        if let Ok(mut slot) = self.latest.lock() {
            if let Some(mut frame) = slot.take() {
                frame.zeroize();
            }
        }
    }
}

fn send_audio_packet(sender: &mpsc::SyncSender<AudioPacket>, pcm16: Vec<i16>, sample_rate: u32) {
    if pcm16.is_empty() {
        return;
    }
    let _ = sender.try_send(AudioPacket {
        pcm16,
        sample_rate,
        captured_at: Instant::now(),
    });
}

fn saturating_increment(counter: &AtomicU32) {
    let _ = counter.fetch_update(Ordering::AcqRel, Ordering::Acquire, |value| {
        Some(value.saturating_add(1))
    });
}

fn mono_i16(data: &[i16], channels: usize) -> Vec<i16> {
    data.chunks_exact(channels)
        .map(|frame| {
            let sum = frame.iter().map(|sample| i64::from(*sample)).sum::<i64>();
            (sum / channels as i64) as i16
        })
        .collect()
}

fn mono_f32(data: &[f32], channels: usize) -> Vec<f32> {
    data.chunks_exact(channels)
        .map(|frame| frame.iter().sum::<f32>() / channels as f32)
        .collect()
}

pub fn resample_pcm16(input: &[i16], input_rate: u32, output_rate: u32) -> Vec<i16> {
    if input.is_empty() || input_rate == 0 || output_rate == 0 {
        return Vec::new();
    }
    if input_rate == output_rate {
        return input.to_vec();
    }
    let output_len =
        ((input.len() as u64 * u64::from(output_rate)) / u64::from(input_rate)).max(1) as usize;
    let ratio = input_rate as f64 / output_rate as f64;
    (0..output_len)
        .map(|index| {
            let position = index as f64 * ratio;
            let left = position.floor() as usize;
            let right = (left + 1).min(input.len() - 1);
            let fraction = position - left as f64;
            let value =
                f64::from(input[left]) * (1.0 - fraction) + f64::from(input[right]) * fraction;
            value
                .round()
                .clamp(f64::from(i16::MIN), f64::from(i16::MAX)) as i16
        })
        .collect()
}

fn next_sample(queue: &Arc<Mutex<VecDeque<i16>>>) -> i16 {
    queue
        .lock()
        .ok()
        .and_then(|mut samples| samples.pop_front())
        .unwrap_or(0)
}

fn fill_i16(data: &mut [i16], channels: usize, queue: &Arc<Mutex<VecDeque<i16>>>) {
    for frame in data.chunks_mut(channels) {
        let sample = next_sample(queue);
        frame.fill(sample);
    }
}

fn fill_f32(data: &mut [f32], channels: usize, queue: &Arc<Mutex<VecDeque<i16>>>) {
    for frame in data.chunks_mut(channels) {
        let sample = f32::from(next_sample(queue)) / f32::from(i16::MAX);
        frame.fill(sample);
    }
}

fn fill_u16(data: &mut [u16], channels: usize, queue: &Arc<Mutex<VecDeque<i16>>>) {
    for frame in data.chunks_mut(channels) {
        let sample = (i32::from(next_sample(queue)) + 32_768) as u16;
        frame.fill(sample);
    }
}

#[cfg(target_os = "windows")]
fn ensure_window_exists(source_id: u64) -> Result<(), MediaError> {
    use xcap::Window;

    let source_id = u32::try_from(source_id).map_err(|_| MediaError::CaptureSourceUnavailable)?;
    Window::all()
        .map_err(|_| MediaError::CaptureSourceUnavailable)?
        .into_iter()
        .any(|window| window.id().ok() == Some(source_id))
        .then_some(())
        .ok_or(MediaError::CaptureSourceUnavailable)
}

#[cfg(target_os = "windows")]
fn process_id_for_window(source_id: u64) -> Result<u32, MediaError> {
    use xcap::Window;

    let source_id = u32::try_from(source_id).map_err(|_| MediaError::CaptureSourceUnavailable)?;
    Window::all()
        .map_err(|_| MediaError::CaptureSourceUnavailable)?
        .into_iter()
        .find(|window| window.id().ok() == Some(source_id))
        .and_then(|window| window.pid().ok())
        .filter(|process_id| *process_id != 0)
        .ok_or(MediaError::CaptureSourceUnavailable)
}

#[cfg(not(target_os = "windows"))]
fn ensure_window_exists(_source_id: u64) -> Result<(), MediaError> {
    Err(MediaError::CaptureSourceUnavailable)
}

#[cfg(target_os = "windows")]
fn capture_window(source_id: u64, sequence: u64) -> Result<VideoFrame, MediaError> {
    use xcap::Window;

    let source_id = u32::try_from(source_id).map_err(|_| MediaError::CaptureSourceUnavailable)?;
    let window = Window::all()
        .map_err(|_| MediaError::CaptureFailed)?
        .into_iter()
        .find(|window| window.id().ok() == Some(source_id))
        .ok_or(MediaError::CaptureSourceUnavailable)?;
    let image = window
        .capture_image()
        .map_err(|_| MediaError::CaptureFailed)?;
    encode_frame(image, sequence)
}

#[cfg(not(target_os = "windows"))]
fn capture_window(_source_id: u64, _sequence: u64) -> Result<VideoFrame, MediaError> {
    Err(MediaError::CaptureSourceUnavailable)
}

fn encode_frame(mut image: RgbaImage, sequence: u64) -> Result<VideoFrame, MediaError> {
    let (source_width, source_height) = image.dimensions();
    if source_width == 0 || source_height == 0 {
        return Err(MediaError::CaptureFailed);
    }
    let scale = (MAX_VIDEO_DIMENSION as f64 / f64::from(source_width.max(source_height))).min(1.0);
    let width = (f64::from(source_width) * scale).round().max(1.0) as u32;
    let height = (f64::from(source_height) * scale).round().max(1.0) as u32;
    let resized = if (width, height) == (source_width, source_height) {
        image
    } else {
        let resized = image::imageops::resize(&image, width, height, FilterType::Triangle);
        image.as_mut().zeroize();
        resized
    };
    let mut jpeg = Vec::new();
    let mut dynamic = DynamicImage::ImageRgba8(resized);
    let encoded = JpegEncoder::new_with_quality(&mut jpeg, 75).encode_image(&dynamic);
    if let Some(pixels) = dynamic.as_mut_rgba8() {
        pixels.as_mut().zeroize();
    }
    encoded.map_err(|_| MediaError::CaptureFailed)?;
    if jpeg.len() > MAX_VIDEO_JPEG_BYTES {
        jpeg.zeroize();
        return Err(MediaError::CaptureFailed);
    }
    Ok(VideoFrame {
        sequence,
        jpeg,
        width,
        height,
        captured_at: Instant::now(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stereo_audio_is_mixed_to_mono_without_overflow() {
        assert_eq!(
            mono_i16(&[i16::MAX, i16::MAX, i16::MIN, i16::MIN], 2),
            [i16::MAX, i16::MIN]
        );
    }

    #[test]
    fn video_frames_are_bounded_and_jpeg_encoded() {
        let image = RgbaImage::from_pixel(2_000, 1_000, image::Rgba([20, 40, 60, 255]));
        let frame = encode_frame(image, 1).expect("encode frame");
        assert_eq!((frame.width, frame.height), (1_280, 640));
        assert_eq!(&frame.jpeg[..2], &[0xff, 0xd8]);
        assert!(frame.jpeg.len() <= MAX_VIDEO_JPEG_BYTES);
    }

    #[test]
    fn pcm_resampling_preserves_duration_and_endpoints() {
        let input = [i16::MIN, 0, i16::MAX];
        let output = resample_pcm16(&input, 3, 6);
        assert_eq!(output.len(), 6);
        assert_eq!(output[0], i16::MIN);
        assert!(output[4] > 30_000);
    }
}
