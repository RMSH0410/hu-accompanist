use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use cpal::Stream;
use std::sync::mpsc::Sender;

fn create_stream(tx : Sender<Vec<f32>>) -> Result<Stream, Box<dyn std::error::Error>> {
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





