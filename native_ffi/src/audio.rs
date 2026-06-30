use cpal::Stream;
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use std::sync::mpsc::Receiver;
use std::sync::mpsc::Sender;
use std::ops::Range;

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

/// This function runs on a background thread. It listens to the channel receiver
/// and collects the raw audio chunks sent over by the microphone.
pub fn start_processing_loop(rx: Receiver<Vec<f32>>) {
   
    // Buffer to store incoming audio data
    let mut audio_vault: Vec<f32> = Vec::new();

    // This loop runs when data is recieved from rx
    while let Ok(chunk) = rx.recv() {
       audio_vault.extend_from_slice(&chunk);

       while audio_vault.len() >= 1024 {
           let processing_data = &audio_vault[0..1024];
           let rms: f32 = (processing_data
            .iter()
            .map(|&x| x * x)         // 1. Square every sample
            .sum::<f32>()           // 2. Add them all together
            / 1024.0)               // 3. Divide by 1024 (Mean)
            .sqrt();
           if rms <= 0.005 {
                continue;
           }

           audio_vault.drain(0..128);
       }
    }
}
