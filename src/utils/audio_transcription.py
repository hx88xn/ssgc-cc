import os
import json
from openai import AsyncOpenAI

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _client


async def transcribe_audio(file_path: str) -> str:
    if not os.path.exists(file_path):
        return ""

    try:
        with open(file_path, "rb") as audio_file:
            transcript = await _get_client().audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                response_format="text"
            )
        return transcript
    except Exception as e:
        print(f"Error transcribing audio: {e}")
        return ""


async def analyze_call_with_llm(call_id: str, user_transcript: str, agent_transcript: str) -> dict:
    if not user_transcript and not agent_transcript:
        return {"error": "No transcripts available"}

    analysis_prompt = f"""Analyze this natural gas utility (SSGC) customer service call and provide structured analysis.

CALLER'S STATEMENTS:
{user_transcript if user_transcript else "No caller audio captured"}

AGENT'S RESPONSES:
{agent_transcript if agent_transcript else "No agent audio captured"}

Provide analysis in the following JSON format:
{{
    "call_summary": "Brief 2-3 sentence summary of the call",
    "caller_intent": "What the caller was trying to accomplish",
    "inquiry_type": "Billing / View Bill / Payment / New Connection / Complaint / Safety or Leak / Tariff or Calculator / CNIC Update / General Information / Other",
    "caller_sentiment": "Positive / Neutral / Negative",
    "resolution_status": "Resolved / Partially Resolved / Unresolved / Redirected to Official Channel",
    "key_topics": ["list of main topics discussed"],
    "safety_related": true/false,
    "agent_performance": {{
        "professionalism": "1-5 rating",
        "accuracy": "1-5 rating",
        "empathy": "1-5 rating",
        "overall": "1-5 rating"
    }},
    "follow_up_needed": true/false,
    "follow_up_notes": "Any required follow-up actions",
    "recommendations": "Suggestions for improvement if any"
}}

Return ONLY valid JSON, no additional text."""

    try:
        response = await _get_client().chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a call quality analyst for a natural gas distribution utility "
                        "(Sui Southern Gas Company). Analyze calls professionally and return structured JSON."
                    )
                },
                {"role": "user", "content": analysis_prompt}
            ],
            temperature=0.3,
            response_format={"type": "json_object"}
        )

        analysis = json.loads(response.choices[0].message.content)

        os.makedirs("recordings/analysis", exist_ok=True)
        analysis_path = f"recordings/analysis/{call_id}_analysis.json"
        with open(analysis_path, "w", encoding="utf-8") as f:
            json.dump(analysis, f, ensure_ascii=False, indent=2)

        print(f"✅ Analysis saved to {analysis_path}")
        return analysis

    except Exception as e:
        print(f"Error analyzing call: {e}")
        return {"error": str(e)}
