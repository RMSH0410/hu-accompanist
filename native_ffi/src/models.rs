use cpal::Stream;
use serde::{Deserialize, Serialize};
use std::ops::Range;

#[derive(Debug, Serialize, Deserialize, Clone, PartialEq)]
pub enum Instrument {
    Violin,
    Viola,
    Cello,
    Piano,
    Guitar,
    Trumpet,
    FrenchHorn,
    Clarinet
}

#[derive(Debug, Serialize, Deserialize, Clone, PartialEq)]
pub struct InstrumentProfile {
    pub instrument_type : Instrument,
    pub freq_rng : Range<f32>,
    pub chords : bool
}

#[derive(Debug, Clone, Deserialize)]
pub struct TimingSpecs {
    pub bpm: f32,
    pub beat_unit: u32 
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct Notes {
    pub note_id: u64,
    pub pitch_hz: f64,
    pub start_time_ms: f64,
    pub end_time_ms: f64,
    pub duration_ms: f64,

    // Optional Members
    pub vibrato_depth: Option<f32>,
    pub pedal_action: Option<String>,
    pub has_accent: Option<bool>,
    pub markings: Option<String>
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct PieceData {
    pub piece_name: String,
    pub curr_phase: u8,
    pub instrument: Option<Instrument>,
    pub curr_music_phrase: u32,
    pub timing: TimingSpecs,
    pub notes: Vec<Notes>
}

pub struct SendStream(pub Stream);

unsafe impl Send for SendStream {}
