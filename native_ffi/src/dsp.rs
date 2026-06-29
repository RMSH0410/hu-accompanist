use realfft::RealFftPlanner;
use std::sync::{OnceLock, Arc};

const LEN: usize = 2048;
static FFT_PLANNER: OnceLock<Arc<dyn realfft::Fft>> = OnceLock::new();

pub fn get_fft_instance() -> Arc<dyn realfft::Fft> {
    FFT_PLANNER
        .get_or_init(|| {
            let mut planner = RealFftPlanner::new();
            // Pre-configure the planner for your exact window size
            planner.plan_fft_forward(LEN)
        })
        .clone()
}

pub fn process_dsp() -> Result<(), Box<dyn std::error::Error>> {
    let mut planner = get_fft_instance();
    Ok(())
}
