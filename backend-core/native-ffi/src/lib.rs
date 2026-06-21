#[no_mangle]
pub extern "C" fn listen_audio() {
    let (tx, rx) = std::sync::mpsc::channel::<Vec<f32>>();
    std::thread::spawn(move || {
        core_logic::start_processing_loop(rx);
    });
    let _my_live_stream = create_stream(tx).expect("Failed to initialize audio input!");
    std::thread::sleep(std::time::Duration::from_secs(10));
}
