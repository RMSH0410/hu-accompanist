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

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct Notes {
    pub note_id: u64,
    pub pitch_hz: u64,
    pub start_time_seconds: f64,
    pub end_time_seconds: f64,
    pub duration_seconds: f64,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct PieceData {
    pub instrument: Instrument,
    pub curr_phrase: u32,
    pub bpm: u32,
    pub notes: Vec<Notes>
}

pub struct SendStream(pub Stream);

unsafe impl Send for SendStream {}
