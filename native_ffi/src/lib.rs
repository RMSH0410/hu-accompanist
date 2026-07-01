// Modules
pub mod audio;
pub mod dsp;
pub mod run_onnx;
pub mod models;

// Crates
use once_cell::sync::Lazy;
use serde_json;
use std::ffi::CStr;
use std::os::raw::c_char;
use std::sync::{Mutex, LazyLock};

// Custom Defined types
use models::SendStream;
use models::PieceData;

static ACTIVE_STREAM: Lazy<Mutex<Option<SendStream>>> = Lazy::new(|| Mutex::new(None));
pub static ACTIVE_PIECE: LazyLock<Mutex<Option<PieceData>>> = LazyLock::new(|| {
    Mutex::new(None)
});

#[unsafe(no_mangle)]
pub extern "C" fn listen_audio() {
    let mut stream_guard = ACTIVE_STREAM.lock().unwrap();
    if stream_guard.is_some() {
        return;
    }
    let (tx, rx) = std::sync::mpsc::channel::<Vec<f32>>();
    let _my_live_stream = audio::create_stream(tx);
    std::thread::spawn(move || {
        audio::start_processing_loop(rx);
    });
    if let Ok(stream) = _my_live_stream {
        *stream_guard = Some(SendStream(stream));
    }
}

#[unsafe(no_mangle)]
pub extern "C" fn stop_audio() {
    let mut stream_guard = ACTIVE_STREAM.lock().unwrap();
    if let Some(stream) = stream_guard.take() {
        // Dropping the stream object turns off the microphone hardware
        std::mem::drop(stream);
    }
}

#[unsafe(no_mangle)]
pub extern "C" fn init_session(json_data: *const c_char) {
    let c_str = unsafe { CStr::from_ptr(json_data) };
    let json = c_str.to_str().expect("Invalid String from Flutter!");
    let piece_data: PieceData = serde_json::from_str(json).unwrap();
    
    let mut active_slot = ACTIVE_PIECE.lock().unwrap();
    *active_slot = Some(piece_data);
}
