import numpy as np
import sounddevice as sd
import aubio

# 1. Configure standard audio stream parameters
BUFFER_SIZE = 1024  # The hop_size: how many samples to read at a time
WINDOW_SIZE = 2048  # The buf_size: must be a power of 2 and larger than hop_size
SAMPLE_RATE = 44100  # 44.1 kHz standard rate

# 2. Initialize Aubio's pitch detection engine cleanly with explicit arguments
pitch_detector = aubio.pitch(
    method="yin",
    buf_size=WINDOW_SIZE,
    hop_size=BUFFER_SIZE,
    samplerate=SAMPLE_RATE
)
pitch_detector.set_unit("Hz")
pitch_detector.set_tolerance(0.8)


def audio_callback(indata, frames, time, status):
    """This function is called automatically by sounddevice for every live audio buffer."""
    if status:
        print(status)

    # Convert microphone input into a flat Float32 array
    audio_buffer = indata[:, 0].astype(np.float32)

    # Run aubio's tracking algorithm on the buffer chunk
    pitch = pitch_detector(audio_buffer)[0]

    # Basic energy check to make sure it ignores ambient silence or light breath
    volume = np.sum(audio_buffer ** 2) / len(audio_buffer)

    if pitch > 0 and volume > 0.001:
        print(f"Detected Pitch: {pitch:.2f} Hz")


# 3. Start listening through the hardware mic
print("🎤 Listening... Try whistling or humming a clear tone near your microphone.")
with sd.InputStream(channels=1, callback=audio_callback,
                    blocksize=BUFFER_SIZE, samplerate=SAMPLE_RATE):
    while True:
        sd.sleep(1000)