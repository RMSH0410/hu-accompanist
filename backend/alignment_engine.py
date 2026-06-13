import time

# A hardcoded target sequence representing a perfect performance
# Key = Note Name, Value = The exact second it *should* happen in the score
TARGET_SCORE = [
    {"note": "C4", "target_time": 1.0},
    {"note": "E4", "target_time": 2.0},
    {"note": "G4", "target_time": 3.0},
    {"note": "C5", "target_time": 4.0}
]


def analyze_performance(live_log):
    print("\n--- Performance Review Grid ---")
    score_deductions = 0

    for target, live in zip(TARGET_SCORE, live_log):
        time_variance = live["played_time"] - target["target_time"]

        if abs(time_variance) <= 0.15:
            status = "Perfect Timing (In the pocket)"
        elif time_variance < -0.15:
            status = "RUSHING (You played too early!)"
            score_deductions += 10
        else:
            status = "DRAGGING (You delayed too much)"
            score_deductions += 10

        print(
            f"Note {target['note']}: Expected {target['target_time']}s | Played {live['played_time']:.2f}s -> {status}")

    final_rating = max(0, 100 - score_deductions)
    print(f"\n🏆 Final Session Technical Score: {final_rating}/100")


# Simulation of a user playing the notes slightly imperfectly
simulated_user_performance = [
    {"note": "C4", "played_time": 1.02},  # Great
    {"note": "E4", "played_time": 1.75},  # Rushed early!
    {"note": "G4", "played_time": 3.12},  # Good rubato recovery
    {"note": "C5", "played_time": 4.45}  # Very dragged out
]

analyze_performance(simulated_user_performance)