import numpy as np
import sounddevice as sd
import aubio
import time

# --- CONFIGURATION ---
BUFFER_SIZE = 2048
SAMPLE_RATE = 44100

# The target piece we want the user to play (Chopin-style target tracking)
TARGET_PIECE = ["C4", "E4", "G4", "C5"]
# The exact target intervals (seconds relative to the start)
TARGET_TIMESTAMPS = [1.0, 2.0, 3.0, 4.0]

# Standard musical notes tracking array
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# --- STATE VARIABLES ---
performance_log = []
current_target_index = 0
start_time = None
is_listening = True

# Initialize pitch detector
pitch_detector = aubio.pitch("pitchyin", BUFFER_SIZE * 2, BUFFER_SIZE, SAMPLE_RATE)
pitch_detector.set_unit("Hz")
pitch_detector.set_tolerance(0.8)


def hz_to_note_name(hz):
    """Converts a frequency in Hz to a standard musical note name (e.g., 261.63 -> C4)."""
    if hz <= 0:
        return None
    # Calculate MIDI note number based on standard logarithmic tuning (A4 = 440Hz = MIDI 69)
    midi_note = int(round(12 * np.log2(hz / 440.0) + 69))

    # Extract the note identity and the octave number
    note_index = midi_note % 12
    octave = (midi_note // 12) - 1
    return f"{NOTE_NAMES[note_index]}{octave}"


def audio_callback(indata, frames, time_info, status):
    global current_target_index, start_time, is_listening

    if not is_listening:
        return

    audio_buffer = indata[:, 0].astype(np.float32)
    pitch = pitch_detector(audio_buffer)[0]
    volume = np.sum(audio_buffer ** 2) / len(audio_buffer)

    # Check if a clear note is being held down
    if pitch > 0 and volume > 0.002:
        note_name = hz_to_note_name(pitch)
        target_note = TARGET_PIECE[current_target_index]

        # If the detected note matches the current note we are waiting for
        if note_name == target_note:
            now = time.time()

            # Start the session timer on the very first note played
            if start_time is None:
                start_time = now
                print("⏱️ Performance started! Timer ticking...")

            elapsed_time = now - start_time

            print(f"🎯 MATCHED: You played {note_name} at {elapsed_time:.2f} seconds!")

            # Log the performance metrics
            performance_log.append({
                "note": note_name,
                "played_time": elapsed_time,
                "target_time": TARGET_TIMESTAMPS[current_target_index]
            })

            # Advance to tracking the next note in the piece
            current_target_index += 1

            # Check if the piece is fully completed
            if current_target_index >= len(TARGET_PIECE):
                print("\n🎉 Piece Completed! Processing data...")
                is_listening = False


def run_grading_jury():
    """Compiles the final scoring dashboard once performance finishes."""
    print("\n==============================================")
    print("📋 SMART PERFORMANCE PRACTICE REPORT")
    print("==============================================")
    score_deductions = 0

    for item in performance_log:
        variance = item["played_time"] - item["target_time"]

        # If variance is tight, it's perfect. If negative, rushing. If positive, dragging.
        if abs(variance) <= 0.2:
            status = "PERFECT"
        elif variance < -0.2:
            status = "RUSHING (Too fast)"
            score_deductions += 15
        else:
            status = "DRAGGING (Too slow)"
            score_deductions += 15

        print(f"Note {item['note']}: Target {item['target_time']}s | Actual {item['played_time']:.2f}s -> {status}")

    final_score = max(0, 100 - score_deductions)
    print("----------------------------------------------")
    print(f"🏆 Final Execution Rating: {final_score}/100")
    print("==============================================")


# --- RUN ENGINE ---
print("🎹 WAITING FOR YOU. Play this sequence: C4 -> E4 -> G4 -> C5")
with sd.InputStream(channels=1, callback=audio_callback, blocksize=BUFFER_SIZE, samplerate=SAMPLE_RATE):
    while is_listening:
        sd.sleep(100)

# Once the stream finishes because all notes were matched, show the jury dashboard
run_grading_jury()