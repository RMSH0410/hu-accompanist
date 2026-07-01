use crate::ACTIVE_PIECE;
use crate::models::PieceData;
use cpal::Stream;
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use std::mem::MaybeUninit;
use std::ops::Range;
use std::sync::mpsc::Receiver;
use std::sync::mpsc::Sender;
use std::time::Instant;

pub fn create_stream(tx: Sender<Vec<f32>>) -> Result<Stream, Box<dyn std::error::Error>> {
    let host = cpal::default_host();
    let device = host
        .default_input_device()
        .expect("No Input Devices Found!!");
    let config = device.default_input_config()?;
    let sample_rate = config.sample_rate().0;
    let err_fn = |err| eprintln!("An error occurred on the audio stream: {}", err);
    let sample_format = config.sample_format();
    let config: cpal::StreamConfig = config.into();
    let stream = match sample_format {
        cpal::SampleFormat::F32 => device.build_input_stream(
            &config,
            move |data: &[f32], _: &cpal::InputCallbackInfo| {
                let _ = tx.send(data.to_vec());
            },
            err_fn,
            None,
        )?,
        _ => panic!("Unsupported sample format! (Expected f32)"),
    };
    stream.play()?;
    Ok(stream)
}

pub fn start_processing_loop(rx: Receiver<Vec<f32>>) {

    let mut audio_vault: Vec<f32> = Vec::new();
    let mut input_data_buffer = vec![0.0f32; 1024];
    let mut output_spectrum = crate::dsp::FFT.make_output_vec();
    let user_data: Option<PieceData> = None;
    let piece_data = ACTIVE_PIECE.lock().unwrap();
    let start_time = Instant::now();

    // This loop runs when data is recieved from rx
    while let Ok(chunk) = rx.recv() {
        audio_vault.extend_from_slice(&chunk);

        while audio_vault.len() >= 1024 {
            let current_ms = start_time.elapsed().as_millis() as u32;
            let processing_data = &audio_vault[0..1024];
            let rms: f32 = (processing_data
            .iter()
            .map(|&x| x * x)    // 1. Square every sample
            .sum::<f32>()       // 2. Add them all together
            / 1024.0) // 3. Divide by 1024 (Mean)
                .sqrt();
            if rms <= 0.005 {
                audio_vault.drain(0..128);
                continue;
            } 
            
            input_data_buffer.copy_from_slice(&audio_vault[0..1024]);
            crate::dsp::run_fft(&mut input_data_buffer, &mut output_spectrum);

            if let Some(ref piece) = *piece_data {
                match &piece.curr_phase {
                    1 => {
                        let target_notes = crate::dsp::get_current_targets(current_ms, piece);
                        let is_perfect = crate::dsp::process_dsp(&output_spectrum, &mut user_data, &target_notes);
                    }
                    2 | 3 => {}
                }
            }
            audio_vault.drain(0..128);
        }
    }
}
