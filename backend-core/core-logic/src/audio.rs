use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use cpal::Stream;
use std::sync::mpsc::Receiver;
use std::sync::mpsc::Sender;

pub fn create_stream(tx : Sender<Vec<f32>>) -> Result<Stream, Box<dyn std::error::Error>> {
    let host = cpal::default_host();
    let device = host
        .default_input_device()
        .expect("No Input Devices Found!!");
    let config = device.default_input_config()?;
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
    // A persistent vector to store incoming audio samples over time
    let mut audio_vault: Vec<f32> = Vec::new();

    println!("🎵 Core-Logic: Processing loop started. Waiting for violin audio...");

    // This loop blocks and waits until a new chunk of data slides down the channel
    while let Ok(chunk) = rx.recv() {
        // Append the incoming audio data to our vault
        audio_vault.extend_from_slice(&chunk);

        // For real-time pitch detection, we want to look at chunks of audio.
        // Once we have enough samples (e.g., 2048 samples), we can run our math.
        if audio_vault.len() >= 2048 {
            // Grab the latest 2048 samples for the FFT
            let processing_chunk = &audio_vault[0..2048];

            // -------------------------------------------------------------
            // FUTURE FFT MATH WILL GO HERE
            // This is where we will analyze the pitch for your C Major scale!
            // -------------------------------------------------------------
            
            // For now, let's just calculate a basic volume so we can see it working:
            let volume: f32 = processing_chunk.iter().map(|s| s.abs()).sum::<f32>() / 2048.0;
            if volume > 0.01 {
                println!("🎙️ Audio detected! Signal volume: {:.4}", volume);
            }

            // Slide our data window forward so we don't leak memory
            audio_vault.drain(0..1024); 
        }
    }
}





