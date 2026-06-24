use std::sync::Mutex;
use once_cell::sync::Lazy;
use cpal::Stream;

pub struct SendStream(pub Stream);

unsafe impl Send for SendStream {}

static ACTIVE_STREAM: Lazy<Mutex<Option<SendStream>>> = Lazy::new(|| Mutex::new(None));

#[unsafe(no_mangle)]
pub extern "C" fn listen_audio() {
    let mut stream_guard = ACTIVE_STREAM.lock().unwrap();
    if stream_guard.is_some() {
        return;
    }
    let (tx, rx) = std::sync::mpsc::channel::<Vec<f32>>();
    let _my_live_stream  : Result<Stream, Box<dyn std::error::Error>> = core_logic::audio::create_stream(tx);
    std::thread::spawn(move || {
        core_logic::audio::start_processing_loop(rx);
    });
    if let Ok(stream) = _my_live_stream {
        *stream_guard = Some(SendStream(stream));
    } 
    std::thread::sleep(std::time::Duration::from_secs(10));
}

#[unsafe(no_mangle)]
pub extern "C" fn stop_audio() {
    let mut stream_guard = ACTIVE_STREAM.lock().unwrap();
    if let Some(stream) = stream_guard.take() {
        // Dropping the stream object automatically turns off the microphone hardware
        std::mem::drop(stream);
    }
}
