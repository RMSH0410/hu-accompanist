use crate::models::PieceData;
use num_complex::Complex;
use realfft::{RealFftPlanner, RealToComplex};
use std::sync::{Arc, LazyLock, Mutex};
use crate::ACTIVE_PIECE;
use crate::models::Notes;
use std::time::Instant;

const LEN: usize = 2048;
static FFT_PLANNER: LazyLock<Mutex<RealFftPlanner<f32>>> =
    LazyLock::new(|| Mutex::new(RealFftPlanner::new()));

static FFT: LazyLock<Arc<dyn RealToComplex<f32>>> = LazyLock::new(|| {
    let mut planner = FFT_PLANNER.lock().unwrap();
    planner.plan_fft_foward(1024)
});

pub fn get_current_targets(curr_ms: f32, piece_data: &PieceData,) -> Vec<f32> {
    let margin_err = 150.0;
    
    piece_data.notes
        .iter()
        .filter(|&note| {
            let soft_start = note.start_time_ms.saturating_sub(margin_err);
            let soft_end = note.end_time_ms.saturating_add(margin_err);
            
            curr_ms >= soft_start && curr_ms <= soft_end
        })
        .map(|note| note.pitch_hz)
        .collect() 
}

pub fn run_fft(input_data: &mut Vec<f32>, output_spectrum: &mut Vec<Complex<f32>>) {
    FFT.process(input_data, output_spectrum).unwrap();
}

pub fn process_dsp(
    output_spectrum: &Vec<Complex<f32>>,
    user_data: &mut Option<PieceData>,
    target_notes: &Vec<f32>
) -> Result<bool, Box<dyn std::error::Error>> {
    const THRESHOLD: f32 = 5.0;
    let detected_notes: Vec<f32> = Vec::new();
    let mut max_mag = 0.0;
    let mut max_bin_index = 0;
    let mut target_bins: Vec<u32> = Vec::new();

    for &note in target_notes.iter() {
        target_bins.push((note * 1024) / 44100 as u32);
    }

    for &target_bin in target_bins.iter() {
        
        if target_bin == 0 || target_bin >= (output_spectrum.len() - 1) as u32 {
            return Ok(false); 
        }

        // Check a tiny 3-bin neighborhood to account for slight tuning flaws
        let mag_left = output_spectrum[target_bin - 1].norm();
        let mag_center = output_spectrum[target_bin].norm();
        let mag_right = output_spectrum[target_bin + 1].norm();

        // Find the strongest burst of energy in that neighborhood
        let max_neighborhood_energy = mag_left.max(mag_center).max(mag_right);

        // SHORT-CIRCUIT: If even ONE target note is missing, the chord is incomplete!
        if max_neighborhood_energy < THRESHOLD {
            return Ok(false); 
        }         
    }
    Ok(true)
}
