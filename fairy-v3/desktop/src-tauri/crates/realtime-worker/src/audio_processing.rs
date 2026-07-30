use std::collections::VecDeque;

use zeroize::Zeroize;

pub const AUDIO_PROCESSING_SAMPLE_RATE: u32 = 16_000;
pub const AUDIO_PROCESSING_FRAME_SAMPLES: usize = 160;

const MAX_REFERENCE_SAMPLES: usize = AUDIO_PROCESSING_SAMPLE_RATE as usize / 4;
const MAX_MICROPHONE_SAMPLES: usize = AUDIO_PROCESSING_FRAME_SAMPLES * 4;
const ECHO_FILTER_TAPS: usize = 128;
const DELAY_SEARCH_STEP: usize = AUDIO_PROCESSING_FRAME_SAMPLES;
const MAX_DELAY_SAMPLES: usize = AUDIO_PROCESSING_SAMPLE_RATE as usize * 150 / 1_000;
const MIN_RENDER_RMS: f32 = 80.0;
const MIN_SPEECH_RMS: f32 = 180.0;
const VAD_ONSET_FRAMES: u8 = 2;
const VAD_HANGOVER_FRAMES: u8 = 18;
const TARGET_RMS: f32 = 3_600.0;

#[derive(Clone, Debug, PartialEq)]
pub struct ProcessedAudioFrame {
    pub pcm16: Vec<i16>,
    pub speech_started: bool,
    pub speech_stopped: bool,
    pub speech_active: bool,
}

pub trait RealtimeMicrophoneProcessor {
    fn push_render_reference(&mut self, samples: &[i16]);
    fn process_microphone(&mut self, samples: &[i16]) -> Vec<ProcessedAudioFrame>;
    fn reset(&mut self);
}

#[derive(Debug)]
pub struct RealtimeAudioProcessor {
    microphone: VecDeque<i16>,
    render_reference: VecDeque<i16>,
    echo_weights: Vec<f32>,
    echo_delay_samples: usize,
    noise_rms: f32,
    agc_gain: f32,
    vad_onset: u8,
    vad_hangover: u8,
    speech_active: bool,
    frames_since_delay_search: u8,
}

impl Default for RealtimeAudioProcessor {
    fn default() -> Self {
        Self {
            microphone: VecDeque::with_capacity(MAX_MICROPHONE_SAMPLES),
            render_reference: VecDeque::with_capacity(MAX_REFERENCE_SAMPLES),
            echo_weights: vec![0.0; ECHO_FILTER_TAPS],
            echo_delay_samples: 0,
            noise_rms: MIN_SPEECH_RMS,
            agc_gain: 1.0,
            vad_onset: 0,
            vad_hangover: 0,
            speech_active: false,
            frames_since_delay_search: 0,
        }
    }
}

impl RealtimeMicrophoneProcessor for RealtimeAudioProcessor {
    fn push_render_reference(&mut self, samples: &[i16]) {
        bounded_extend(&mut self.render_reference, samples, MAX_REFERENCE_SAMPLES);
    }

    fn process_microphone(&mut self, samples: &[i16]) -> Vec<ProcessedAudioFrame> {
        bounded_extend(&mut self.microphone, samples, MAX_MICROPHONE_SAMPLES);
        let mut output = Vec::new();
        while self.microphone.len() >= AUDIO_PROCESSING_FRAME_SAMPLES {
            let frame = self
                .microphone
                .drain(..AUDIO_PROCESSING_FRAME_SAMPLES)
                .collect::<Vec<_>>();
            output.push(self.process_frame(&frame));
        }
        output
    }

    fn reset(&mut self) {
        zeroize_queue(&mut self.microphone);
        zeroize_queue(&mut self.render_reference);
        self.echo_weights.zeroize();
        self.echo_delay_samples = 0;
        self.noise_rms = MIN_SPEECH_RMS;
        self.agc_gain = 1.0;
        self.vad_onset = 0;
        self.vad_hangover = 0;
        self.speech_active = false;
        self.frames_since_delay_search = 0;
    }
}

impl RealtimeAudioProcessor {
    fn process_frame(&mut self, microphone: &[i16]) -> ProcessedAudioFrame {
        let reference = self.aligned_reference(microphone);
        let echo_cancelled = self.cancel_echo(microphone, &reference);
        let echo_rms = rms_f32(&echo_cancelled);
        let speech_evidence = echo_rms > (self.noise_rms * 1.5).max(MIN_SPEECH_RMS);
        if !speech_evidence {
            self.noise_rms = (self.noise_rms * 0.97 + echo_rms * 0.03).max(24.0);
        }
        let suppressed = suppress_noise(&echo_cancelled, self.noise_rms, speech_evidence);
        let speech_rms = rms_f32(&suppressed);
        let (speech_started, speech_stopped) = self.update_vad(
            speech_evidence && speech_rms > (self.noise_rms * 1.35).max(MIN_SPEECH_RMS * 0.7),
        );
        let pcm16 = self.apply_gain(&suppressed, speech_rms);
        ProcessedAudioFrame {
            pcm16,
            speech_started,
            speech_stopped,
            speech_active: self.speech_active,
        }
    }

    fn aligned_reference(&mut self, microphone: &[i16]) -> Vec<f32> {
        let history = self
            .render_reference
            .iter()
            .map(|sample| f32::from(*sample))
            .collect::<Vec<_>>();
        if history.len() < AUDIO_PROCESSING_FRAME_SAMPLES {
            return vec![0.0; AUDIO_PROCESSING_FRAME_SAMPLES + ECHO_FILTER_TAPS];
        }
        self.frames_since_delay_search = self.frames_since_delay_search.saturating_add(1);
        if self.frames_since_delay_search >= 5 {
            self.echo_delay_samples = estimate_delay(&history, microphone);
            self.frames_since_delay_search = 0;
        }
        let required = AUDIO_PROCESSING_FRAME_SAMPLES + ECHO_FILTER_TAPS;
        let frame_end = history.len().saturating_sub(self.echo_delay_samples);
        let frame_start = frame_end.saturating_sub(AUDIO_PROCESSING_FRAME_SAMPLES);
        let history_start = frame_start.saturating_sub(ECHO_FILTER_TAPS);
        let mut aligned = vec![0.0; required];
        let source = &history[history_start..frame_end];
        let destination_start = required.saturating_sub(source.len());
        aligned[destination_start..].copy_from_slice(source);
        aligned
    }

    fn cancel_echo(&mut self, microphone: &[i16], reference: &[f32]) -> Vec<f32> {
        let reference_frame = &reference[reference.len() - AUDIO_PROCESSING_FRAME_SAMPLES..];
        let reference_rms = rms_f32(reference_frame);
        if reference_rms < MIN_RENDER_RMS {
            return microphone.iter().map(|sample| f32::from(*sample)).collect();
        }
        let microphone_rms = rms(microphone);
        let adaptation = if microphone_rms > reference_rms * 3.5 {
            0.015
        } else {
            0.12
        };
        let mut output = Vec::with_capacity(AUDIO_PROCESSING_FRAME_SAMPLES);
        for (index, sample) in microphone.iter().enumerate() {
            let reference_index = ECHO_FILTER_TAPS + index;
            let mut estimate = 0.0;
            let mut energy = 1.0;
            for tap in 0..ECHO_FILTER_TAPS {
                let value = reference[reference_index - tap];
                estimate += self.echo_weights[tap] * value;
                energy += value * value;
            }
            let error = f32::from(*sample) - estimate;
            let step = adaptation * error / energy;
            for tap in 0..ECHO_FILTER_TAPS {
                self.echo_weights[tap] += step * reference[reference_index - tap];
                self.echo_weights[tap] = self.echo_weights[tap].clamp(-4.0, 4.0);
            }
            output.push(error);
        }
        output
    }

    fn update_vad(&mut self, evidence: bool) -> (bool, bool) {
        if evidence {
            self.vad_onset = self.vad_onset.saturating_add(1).min(VAD_ONSET_FRAMES);
            self.vad_hangover = VAD_HANGOVER_FRAMES;
        } else {
            self.vad_onset = 0;
            self.vad_hangover = self.vad_hangover.saturating_sub(1);
        }
        let mut started = false;
        let mut stopped = false;
        if !self.speech_active && self.vad_onset >= VAD_ONSET_FRAMES {
            self.speech_active = true;
            started = true;
        } else if self.speech_active && self.vad_hangover == 0 {
            self.speech_active = false;
            stopped = true;
        }
        (started, stopped)
    }

    fn apply_gain(&mut self, samples: &[f32], current_rms: f32) -> Vec<i16> {
        let desired = if current_rms > 1.0 {
            (TARGET_RMS / current_rms).clamp(0.5, 4.0)
        } else {
            1.0
        };
        let rate = if desired < self.agc_gain { 0.18 } else { 0.025 };
        self.agc_gain += (desired - self.agc_gain) * rate;
        let peak = samples
            .iter()
            .map(|sample| sample.abs())
            .fold(0.0_f32, f32::max);
        if peak > 1.0 {
            self.agc_gain = self.agc_gain.min(f32::from(i16::MAX) * 0.98 / peak);
        }
        samples
            .iter()
            .map(|sample| {
                (*sample * self.agc_gain)
                    .round()
                    .clamp(-f32::from(i16::MAX), f32::from(i16::MAX)) as i16
            })
            .collect()
    }
}

impl Drop for RealtimeAudioProcessor {
    fn drop(&mut self) {
        self.reset();
    }
}

fn estimate_delay(reference: &[f32], microphone: &[i16]) -> usize {
    let microphone_rms = rms(microphone);
    if microphone_rms < MIN_RENDER_RMS || reference.len() < AUDIO_PROCESSING_FRAME_SAMPLES {
        return 0;
    }
    let mut best_delay = 0;
    let mut best_score = 0.0_f32;
    let max_delay = MAX_DELAY_SAMPLES.min(
        reference
            .len()
            .saturating_sub(AUDIO_PROCESSING_FRAME_SAMPLES),
    );
    for delay in (0..=max_delay).step_by(DELAY_SEARCH_STEP) {
        let end = reference.len() - delay;
        let start = end - AUDIO_PROCESSING_FRAME_SAMPLES;
        let candidate = &reference[start..end];
        let candidate_rms = rms_f32(candidate);
        if candidate_rms < MIN_RENDER_RMS {
            continue;
        }
        let correlation = candidate
            .iter()
            .zip(microphone)
            .map(|(left, right)| *left * f32::from(*right))
            .sum::<f32>()
            .abs();
        let score =
            correlation / (candidate_rms * microphone_rms * AUDIO_PROCESSING_FRAME_SAMPLES as f32);
        if score > best_score {
            best_score = score;
            best_delay = delay;
        }
    }
    best_delay
}

fn suppress_noise(samples: &[f32], noise_rms: f32, speech: bool) -> Vec<f32> {
    if speech {
        return samples.to_vec();
    }
    let current = rms_f32(samples);
    let ratio = if current <= noise_rms {
        0.2
    } else {
        ((current - noise_rms) / noise_rms.max(1.0)).clamp(0.2, 1.0)
    };
    samples.iter().map(|sample| *sample * ratio).collect()
}

fn bounded_extend(queue: &mut VecDeque<i16>, samples: &[i16], maximum: usize) {
    let overflow = queue
        .len()
        .saturating_add(samples.len())
        .saturating_sub(maximum);
    for _ in 0..overflow.min(queue.len()) {
        if let Some(mut sample) = queue.pop_front() {
            sample.zeroize();
        }
    }
    let start = samples.len().saturating_sub(maximum);
    queue.extend(samples[start..].iter().copied());
}

fn zeroize_queue(queue: &mut VecDeque<i16>) {
    for sample in queue.iter_mut() {
        sample.zeroize();
    }
    queue.clear();
}

fn rms(samples: &[i16]) -> f32 {
    if samples.is_empty() {
        return 0.0;
    }
    let energy = samples
        .iter()
        .map(|sample| {
            let value = f64::from(*sample);
            value * value
        })
        .sum::<f64>();
    (energy / samples.len() as f64).sqrt() as f32
}

fn rms_f32(samples: &[f32]) -> f32 {
    if samples.is_empty() {
        return 0.0;
    }
    let energy = samples
        .iter()
        .map(|sample| {
            let value = f64::from(*sample);
            value * value
        })
        .sum::<f64>();
    (energy / samples.len() as f64).sqrt() as f32
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn processor_emits_only_complete_bounded_frames() {
        let mut processor = RealtimeAudioProcessor::default();
        assert!(processor.process_microphone(&[100; 159]).is_empty());
        let frames = processor.process_microphone(&[100; 321]);
        assert_eq!(frames.len(), 3);
        assert!(frames
            .iter()
            .all(|frame| frame.pcm16.len() == AUDIO_PROCESSING_FRAME_SAMPLES));
        assert!(processor.microphone.is_empty());
    }

    #[test]
    fn vad_has_bounded_onset_hysteresis_and_hangover() {
        let mut processor = RealtimeAudioProcessor::default();
        let speech = alternating(5_000);
        let first = processor.process_microphone(&speech);
        assert!(!first[0].speech_started);
        let second = processor.process_microphone(&speech);
        assert!(second[0].speech_started);
        assert!(second[0].speech_active);
        let silence = vec![0; AUDIO_PROCESSING_FRAME_SAMPLES];
        let mut stopped = false;
        for _ in 0..=VAD_HANGOVER_FRAMES {
            stopped |= processor.process_microphone(&silence)[0].speech_stopped;
        }
        assert!(stopped);
    }

    #[test]
    fn agc_is_bounded_and_never_clips() {
        let mut processor = RealtimeAudioProcessor::default();
        let quiet = alternating(300);
        let mut peak = 0_i16;
        for _ in 0..80 {
            let frame = processor.process_microphone(&quiet).remove(0);
            peak = peak.max(frame.pcm16.into_iter().map(i16::abs).max().unwrap_or(0));
        }
        assert!(peak > 300);
        assert!(peak <= 1_200);

        let loud = alternating(30_000);
        let frame = processor.process_microphone(&loud).remove(0);
        assert!(frame.pcm16.into_iter().all(|sample| sample != i16::MIN));
    }

    #[test]
    fn adaptive_filter_reduces_repeated_fairy_echo() {
        let mut processor = RealtimeAudioProcessor::default();
        let render = pseudo_noise(4_000);
        let mut before = 0.0;
        let mut after = 0.0;
        for chunk in render.chunks_exact(AUDIO_PROCESSING_FRAME_SAMPLES) {
            processor.push_render_reference(chunk);
            let microphone = chunk
                .iter()
                .map(|sample| (i32::from(*sample) / 2) as i16)
                .collect::<Vec<_>>();
            before += rms(&microphone);
            after += rms(&processor.process_microphone(&microphone)[0].pcm16);
        }
        assert!(after < before * 0.8);
    }

    #[test]
    fn reset_clears_reference_vad_gain_and_filter_state() {
        let mut processor = RealtimeAudioProcessor::default();
        processor.push_render_reference(&[2_000; 400]);
        let _ = processor.process_microphone(&alternating(5_000));
        let _ = processor.process_microphone(&alternating(5_000));
        processor.reset();
        assert!(processor.microphone.is_empty());
        assert!(processor.render_reference.is_empty());
        assert!(processor.echo_weights.iter().all(|weight| *weight == 0.0));
        assert!(!processor.speech_active);
        assert_eq!(processor.agc_gain, 1.0);
    }

    fn alternating(amplitude: i16) -> Vec<i16> {
        (0..AUDIO_PROCESSING_FRAME_SAMPLES)
            .map(|index| {
                if index % 2 == 0 {
                    amplitude
                } else {
                    -amplitude
                }
            })
            .collect()
    }

    fn pseudo_noise(length: usize) -> Vec<i16> {
        let mut state = 0x1234_5678_u32;
        (0..length)
            .map(|_| {
                state = state.wrapping_mul(1_664_525).wrapping_add(1_013_904_223);
                ((state >> 17) as i16).saturating_sub(16_000)
            })
            .collect()
    }
}
