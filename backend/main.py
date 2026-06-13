import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware  # <-- Added for Chrome security compatibility
from pydantic import BaseModel
from typing import List
from google import genai
from dotenv import load_dotenv

# Automatically look for a .env file in the same directory
load_dotenv()

app = FastAPI(title="Online Accompanist AI Jury API")

# 🛠️ CORS Configuration: Allows your Flutter Chrome browser app to hit this API securely
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows requests from localhost web development servers
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize the Gemini Client
try:
    ai_client = genai.Client()
    print("🤖 Gemini AI Client successfully initialized!")
except Exception as e:
    print(f"⚠️ Warning: Gemini Client failed to initialize. Check your .env file. Error: {e}")
    ai_client = None


# --- DATA MODELS ---
class NotePerformance(BaseModel):
    note: str
    played_time: float
    target_time: float


class PerformanceSession(BaseModel):
    piece_name: str
    log: List[NotePerformance]


# --- AI PEDAGOGY ENGINE ---
def generate_ai_feedback(piece_name: str, score: int, assessment_summary: list) -> str:
    """Sends performance data to Gemini to get tailored practicing advice."""
    if not ai_client:
        return "AI Feedback unavailable: Client not initialized."

    mistakes_string = ", ".join(
        [f"Note {m['note']} was {m['status'].lower()}" for m in assessment_summary if m['status'] != "PERFECT"])

    if not mistakes_string:
        mistakes_string = "No mistakes! Perfect technical execution."

    prompt = f"""
    You are an elite classical music conservatory professor and orchestral conductor. 
    A student just practiced playing "{piece_name}" alongside your virtual orchestra.
    They received a technical accuracy score of {score}/100.

    Here is a summary of their timing errors:
    {mistakes_string}

    Write a highly professional, encouraging, and actionable 3-sentence critique. 
    Address the student directly. Focus on musicality, phrasing, or rhythmic strategies 
    (like practicing with subdivided pulses) to fix their specific rushing or dragging tendencies.
    """

    try:
        print("🧠 Querying Gemini model for pedagogical feedback...")
        response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        return response.text.strip()
    except Exception as e:
        print(f"❌ Gemini generation failed: {e}")
        return f"Could not generate AI feedback at this time. (Error: {e})"


# --- API ENDPOINT ---
@app.post("/api/v1/evaluate")
async def evaluate_performance(session: PerformanceSession):
    # 🎯 Console Print Verification when payload arrives
    print(f"\n📥 Received incoming performance evaluation for: {session.piece_name}")
    print(f"📊 Collected notes array length: {len(session.log)} items")

    if not session.log:
        raise HTTPException(status_code=400, detail="Performance log cannot be empty.")

    score_deductions = 0
    detailed_report = []

    # Calculate performance variances
    for item in session.log:
        variance = item.played_time - item.target_time

        if abs(variance) <= 0.2:
            status = "PERFECT"
        elif variance < -0.2:
            status = "RUSHING"
            score_deductions += 15
        else:
            status = "DRAGGING"
            score_deductions += 15

        detailed_report.append({
            "note": item.note,
            "variance": round(variance, 2),
            "status": status
        })
        print(f"   🎵 Note: {item.note} | Variance: {variance:+.2f}s -> {status}")

    final_score = max(0, 100 - score_deductions)
    print(f"🏆 Calculated Performance Score: {final_score}/100")

    # Trigger the AI Jury Generation
    ai_critique = generate_ai_feedback(session.piece_name, final_score, detailed_report)

    print("📤 Sending response dashboard back to Flutter client successfully.")
    return {
        "status": "success",
        "piece_name": session.piece_name,
        "final_score": final_score,
        "assessment": detailed_report,
        "ai_pedagogy_guide": ai_critique
    }

if __name__ == "__main__":
    import uvicorn
    print("🚀 Starting FastAPI Server on http://127.0.0.1:8000 ...")
    uvicorn.run(app, host="127.0.0.1", port=8000)