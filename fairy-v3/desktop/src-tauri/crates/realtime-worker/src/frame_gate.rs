use image::imageops::FilterType;
use image::DynamicImage;
use thiserror::Error;

use crate::protocol::{RealtimeResourceLevel, RealtimeResourcePolicy};

const SIGNATURE_WIDTH: u32 = 16;
const SIGNATURE_HEIGHT: u32 = 16;
const BASELINE_INTERVAL_MS: u64 = 1_000;
const BOOST_INTERVAL_MS: u64 = 500;
const BOOST_DURATION_MS: u64 = 5_000;
const HEARTBEAT_INTERVAL_MS: u64 = 5 * 60 * 1_000;
const STATIC_HAMMING_THRESHOLD: u32 = 5;
const STATIC_REGION_THRESHOLD: u8 = 4;
const MATERIAL_HAMMING_THRESHOLD: u32 = 24;
const MATERIAL_REGION_THRESHOLD: u8 = 20;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum FrameGateDecision {
    Send { boosted: bool, heartbeat: bool },
    SuppressStatic,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Error)]
pub enum FrameGateError {
    #[error("the realtime frame could not be decoded")]
    Decode,
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct PerceptualSignature {
    average_hash: [u64; 4],
    region_luma: [u8; 4],
}

#[derive(Clone, Debug)]
pub struct FrameGate {
    context_epoch: u64,
    last_inspected_at_ms: Option<u64>,
    last_sent_at_ms: Option<u64>,
    last_sent_signature: Option<PerceptualSignature>,
    boost_until_ms: u64,
    user_visual_until_ms: u64,
    resource_policy: RealtimeResourcePolicy,
}

impl FrameGate {
    pub fn new(context_epoch: u64) -> Self {
        Self {
            context_epoch,
            last_inspected_at_ms: None,
            last_sent_at_ms: None,
            last_sent_signature: None,
            boost_until_ms: 0,
            user_visual_until_ms: 0,
            resource_policy: RealtimeResourcePolicy::for_level(RealtimeResourceLevel::Normal),
        }
    }

    pub fn should_inspect(&self, now_ms: u64) -> bool {
        if self.resource_policy.media_paused
            || (self.resource_policy.user_initiated_only && now_ms >= self.user_visual_until_ms)
        {
            return false;
        }
        self.last_inspected_at_ms
            .is_none_or(|last| now_ms.saturating_sub(last) >= self.current_interval_ms(now_ms))
    }

    pub fn inspect(
        &mut self,
        jpeg: &[u8],
        now_ms: u64,
    ) -> Result<FrameGateDecision, FrameGateError> {
        let signature = perceptual_signature(jpeg)?;
        self.last_inspected_at_ms = Some(now_ms);
        let Some(previous) = self.last_sent_signature.as_ref() else {
            self.accept(signature, now_ms);
            return Ok(FrameGateDecision::Send {
                boosted: false,
                heartbeat: false,
            });
        };
        let hamming = signature_hamming(previous, &signature);
        let region_delta = maximum_region_delta(previous, &signature);
        let static_frame =
            hamming <= STATIC_HAMMING_THRESHOLD && region_delta <= STATIC_REGION_THRESHOLD;
        let heartbeat = self
            .last_sent_at_ms
            .is_some_and(|last| now_ms.saturating_sub(last) >= HEARTBEAT_INTERVAL_MS);
        if static_frame && !heartbeat {
            return Ok(FrameGateDecision::SuppressStatic);
        }
        let material =
            hamming >= MATERIAL_HAMMING_THRESHOLD || region_delta >= MATERIAL_REGION_THRESHOLD;
        if material {
            self.note_material_event(now_ms);
        }
        let boosted = self.is_boosted(now_ms);
        self.accept(signature, now_ms);
        Ok(FrameGateDecision::Send { boosted, heartbeat })
    }

    pub fn note_audio_activity(&mut self, now_ms: u64) {
        self.note_material_event(now_ms);
    }

    pub fn note_user_question(&mut self, now_ms: u64) {
        self.note_material_event(now_ms);
        self.user_visual_until_ms = now_ms.saturating_add(BOOST_DURATION_MS);
    }

    pub fn apply_resource_policy(&mut self, policy: RealtimeResourcePolicy) -> bool {
        if !policy.is_valid() || self.resource_policy == policy {
            return false;
        }
        self.resource_policy = policy;
        self.last_inspected_at_ms = None;
        if policy.level != RealtimeResourceLevel::Normal {
            self.boost_until_ms = 0;
        }
        if policy.media_paused {
            self.user_visual_until_ms = 0;
        }
        true
    }

    pub fn reset(&mut self, context_epoch: u64) {
        self.context_epoch = context_epoch;
        self.last_inspected_at_ms = None;
        self.last_sent_at_ms = None;
        self.last_sent_signature = None;
        self.boost_until_ms = 0;
        self.user_visual_until_ms = 0;
    }

    fn current_interval_ms(&self, now_ms: u64) -> u64 {
        if self.resource_policy.level == RealtimeResourceLevel::Normal && self.is_boosted(now_ms) {
            BOOST_INTERVAL_MS
        } else {
            self.resource_policy
                .video_interval_ms
                .max(BASELINE_INTERVAL_MS)
        }
    }

    fn is_boosted(&self, now_ms: u64) -> bool {
        now_ms < self.boost_until_ms
    }

    fn note_material_event(&mut self, now_ms: u64) {
        self.boost_until_ms = self
            .boost_until_ms
            .max(now_ms.saturating_add(BOOST_DURATION_MS));
    }

    fn accept(&mut self, signature: PerceptualSignature, now_ms: u64) {
        self.last_sent_signature = Some(signature);
        self.last_sent_at_ms = Some(now_ms);
    }
}

fn perceptual_signature(jpeg: &[u8]) -> Result<PerceptualSignature, FrameGateError> {
    let image = image::load_from_memory(jpeg).map_err(|_| FrameGateError::Decode)?;
    signature_from_image(image)
}

fn signature_from_image(image: DynamicImage) -> Result<PerceptualSignature, FrameGateError> {
    if image.width() == 0 || image.height() == 0 {
        return Err(FrameGateError::Decode);
    }
    let thumbnail = image
        .resize_exact(SIGNATURE_WIDTH, SIGNATURE_HEIGHT, FilterType::Triangle)
        .to_luma8();
    let pixels = thumbnail.as_raw();
    let average = pixels.iter().map(|value| u64::from(*value)).sum::<u64>()
        / u64::try_from(pixels.len()).map_err(|_| FrameGateError::Decode)?;
    let mut average_hash = [0_u64; 4];
    for (index, value) in pixels.iter().enumerate() {
        if u64::from(*value) >= average {
            average_hash[index / 64] |= 1_u64 << (index % 64);
        }
    }
    let mut region_sum = [0_u64; 4];
    let mut region_count = [0_u64; 4];
    for y in 0..SIGNATURE_HEIGHT {
        for x in 0..SIGNATURE_WIDTH {
            let region =
                usize::from(y >= SIGNATURE_HEIGHT / 2) * 2 + usize::from(x >= SIGNATURE_WIDTH / 2);
            let index = (y * SIGNATURE_WIDTH + x) as usize;
            region_sum[region] += u64::from(pixels[index]);
            region_count[region] += 1;
        }
    }
    let mut region_luma = [0_u8; 4];
    for index in 0..4 {
        region_luma[index] = (region_sum[index] / region_count[index]) as u8;
    }
    Ok(PerceptualSignature {
        average_hash,
        region_luma,
    })
}

fn signature_hamming(left: &PerceptualSignature, right: &PerceptualSignature) -> u32 {
    left.average_hash
        .iter()
        .zip(right.average_hash)
        .map(|(left, right)| (left ^ right).count_ones())
        .sum()
}

fn maximum_region_delta(left: &PerceptualSignature, right: &PerceptualSignature) -> u8 {
    left.region_luma
        .iter()
        .zip(right.region_luma)
        .map(|(left, right)| left.abs_diff(right))
        .max()
        .unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use std::io::Cursor;

    use image::codecs::jpeg::JpegEncoder;
    use image::{ImageBuffer, Rgb};

    use super::*;

    fn jpeg(image: &ImageBuffer<Rgb<u8>, Vec<u8>>, quality: u8) -> Vec<u8> {
        let mut output = Cursor::new(Vec::new());
        JpegEncoder::new_with_quality(&mut output, quality)
            .encode_image(image)
            .expect("encode");
        output.into_inner()
    }

    fn solid(value: u8, quality: u8) -> Vec<u8> {
        jpeg(&ImageBuffer::from_pixel(64, 64, Rgb([value; 3])), quality)
    }

    #[test]
    fn identical_pixels_with_different_jpeg_bytes_are_static() {
        let mut gate = FrameGate::new(1);
        let first = solid(80, 55);
        let second = solid(80, 95);
        assert_ne!(first, second);
        assert_eq!(
            gate.inspect(&first, 0),
            Ok(FrameGateDecision::Send {
                boosted: false,
                heartbeat: false,
            })
        );
        assert_eq!(
            gate.inspect(&second, 1_000),
            Ok(FrameGateDecision::SuppressStatic)
        );
    }

    #[test]
    fn material_region_change_starts_only_a_five_second_two_fps_boost() {
        let mut gate = FrameGate::new(1);
        let base = ImageBuffer::from_pixel(64, 64, Rgb([40; 3]));
        let mut changed = base.clone();
        for y in 0..32 {
            for x in 0..32 {
                changed.put_pixel(x, y, Rgb([220; 3]));
            }
        }
        gate.inspect(&jpeg(&base, 80), 0).expect("first");
        assert_eq!(
            gate.inspect(&jpeg(&changed, 80), 1_000),
            Ok(FrameGateDecision::Send {
                boosted: true,
                heartbeat: false,
            })
        );
        assert!(!gate.should_inspect(1_499));
        assert!(gate.should_inspect(1_500));
        assert_eq!(
            gate.inspect(&jpeg(&changed, 80), 1_500),
            Ok(FrameGateDecision::SuppressStatic)
        );
        assert!(!gate.should_inspect(1_999));
        assert!(gate.should_inspect(6_000));
    }

    #[test]
    fn cursor_scale_change_is_suppressed_and_static_heartbeat_is_bounded() {
        let mut gate = FrameGate::new(1);
        let base = ImageBuffer::from_pixel(64, 64, Rgb([100; 3]));
        let mut cursor = base.clone();
        cursor.put_pixel(2, 2, Rgb([110; 3]));
        gate.inspect(&jpeg(&base, 80), 0).expect("first");
        assert_eq!(
            gate.inspect(&jpeg(&cursor, 80), 1_000),
            Ok(FrameGateDecision::SuppressStatic)
        );
        assert_eq!(
            gate.inspect(&jpeg(&base, 80), 300_000),
            Ok(FrameGateDecision::Send {
                boosted: false,
                heartbeat: true,
            })
        );
    }

    #[test]
    fn triggers_and_epoch_reset_are_deterministic() {
        let mut gate = FrameGate::new(1);
        gate.note_audio_activity(100);
        assert!(gate.should_inspect(0));
        gate.inspect(&solid(50, 80), 100).expect("first");
        assert!(!gate.should_inspect(599));
        assert!(gate.should_inspect(600));
        gate.reset(2);
        assert_eq!(gate.context_epoch, 2);
        assert!(gate.should_inspect(601));
        assert_eq!(
            gate.inspect(&solid(50, 80), 601),
            Ok(FrameGateDecision::Send {
                boosted: false,
                heartbeat: false,
            })
        );
    }

    #[test]
    fn invalid_image_bytes_fail_without_state_or_payload_projection() {
        let mut gate = FrameGate::new(1);
        assert_eq!(
            gate.inspect(b"not an image", 0),
            Err(FrameGateError::Decode)
        );
        assert!(gate.should_inspect(0));
    }

    #[test]
    fn resource_policy_changes_interval_and_disables_pressure_boosts() {
        let mut gate = FrameGate::new(1);
        let pressure = RealtimeResourcePolicy::for_level(RealtimeResourceLevel::Pressure);
        assert!(gate.apply_resource_policy(pressure));
        gate.inspect(&solid(50, 80), 0).expect("first");
        gate.note_audio_activity(100);
        assert!(!gate.should_inspect(1_999));
        assert!(gate.should_inspect(2_000));
    }

    #[test]
    fn high_requires_a_recent_user_question_and_critical_pauses_frames() {
        let mut gate = FrameGate::new(1);
        let high = RealtimeResourcePolicy::for_level(RealtimeResourceLevel::High);
        assert!(gate.apply_resource_policy(high));
        assert!(!gate.should_inspect(0));
        gate.note_user_question(100);
        assert!(gate.should_inspect(100));
        gate.inspect(&solid(50, 80), 100).expect("first");
        assert!(!gate.should_inspect(4_099));
        assert!(gate.should_inspect(4_100));

        assert!(
            gate.apply_resource_policy(RealtimeResourcePolicy::for_level(
                RealtimeResourceLevel::Critical
            ))
        );
        assert!(!gate.should_inspect(10_000));
    }
}
