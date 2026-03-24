import os
import json
import base64
import asyncio
from websockets.asyncio.client import connect as ws_connect
import time
import io
import wave
import audioop
from fastapi import FastAPI, WebSocket, Request, HTTPException, Body
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from datetime import datetime as dt, timedelta, timezone
import jwt
from dotenv import load_dotenv
from contextlib import suppress

from prompts import function_call_tools, build_system_message
from call_log_apis import register_call, update_call_status
from src.utils.audio_transcription import transcribe_audio, analyze_call_with_llm

load_dotenv(override=True)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PORT = int(os.getenv("PORT", 7005))

LOG_EVENT_TYPES = [
    'error', 'response.content.done', 'rate_limits.updated',
    'input_audio_buffer.committed', 'session.created'
]

call_recordings = {}
call_metadata = {}

app = FastAPI(
    title="SSGC Voice Agent",
    description="AI-powered voice call center demo for Sui Southern Gas Company Limited",
    version="1.0.0"
)

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "ssgc-voice-agent-secret-2025")
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_HOURS = 2

USERS_DB = {
    "admin": {
        "username": "admin",
        "password": "admin123",
        "full_name": "Administrator"
    },
    "demo": {
        "username": "demo",
        "password": "demo123",
        "full_name": "Demo User"
    }
}

app.mount("/client", StaticFiles(directory="static", html=True), name="client")

AVAILABLE_VOICES = {
    'cedar': {
        'name': 'Faisal',
        'age': 'Young Male',
        'personality': 'Professional and Clear'
    },
    'echo': {
        'name': 'Ahmed',
        'age': 'Young Male',
        'personality': 'Warm and Engaging'
    },
    'shimmer': {
        'name': 'Ayesha',
        'age': 'Young Female',
        'personality': 'Lively and Dynamic'
    },
    'ash': {
        'name': 'Omar',
        'age': 'Young Male',
        'personality': 'Energetic and Helpful'
    },
    'coral': {
        'name': 'Fatima',
        'age': 'Young Female',
        'personality': 'Friendly and Caring'
    },
    'sage': {
        'name': 'Sara',
        'age': 'Young Female',
        'personality': 'Calm and Knowledgeable'
    }
}

USER_AUDIO_DIR = "recordings/user"
AGENT_AUDIO_DIR = "recordings/agent"
os.makedirs(USER_AUDIO_DIR, exist_ok=True)
os.makedirs(AGENT_AUDIO_DIR, exist_ok=True)
os.makedirs("recordings/analysis", exist_ok=True)


@app.get("/", response_class=HTMLResponse)
async def index_page():
    with open("static/voice-client.html", "r", encoding="utf-8") as f:
        html_content = f.read()
    return html_content


def create_jwt_token(username: str, full_name: str) -> str:
    now = dt.now(timezone.utc)
    payload = {
        "username": username,
        "full_name": full_name,
        "exp": now + timedelta(hours=JWT_EXPIRATION_HOURS),
        "iat": now
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def verify_jwt_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def get_token_from_request(request: Request) -> str:
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid authorization header")
    return auth_header.replace("Bearer ", "")


@app.post("/auth/login")
async def login(credentials: dict = Body(...)):
    username = credentials.get("username", "").strip()
    password = credentials.get("password", "")

    if username in USERS_DB:
        user = USERS_DB[username]
        if user["password"] == password:
            token = create_jwt_token(username, user["full_name"])
            return {
                "success": True,
                "message": "Login successful",
                "token": token,
                "user": {
                    "username": username,
                    "full_name": user["full_name"]
                }
            }

    raise HTTPException(status_code=401, detail="Invalid username or password")


@app.get("/available-voices")
async def get_available_voices(request: Request):
    token = get_token_from_request(request)
    verify_jwt_token(token)
    return {"voices": AVAILABLE_VOICES}


@app.post("/start-browser-call")
async def start_browser_call(request: Request, payload: dict = Body(...)):
    token = get_token_from_request(request)
    verify_jwt_token(token)

    phone = payload.get("phone", "webclient")
    voice = payload.get("voice", "sage")
    temperature = payload.get("temperature", 0.8)
    speed = payload.get("speed", 1.05)

    if voice not in AVAILABLE_VOICES:
        voice = "sage"

    temperature = max(0.0, min(1.2, float(temperature)))
    speed = max(0.5, min(2.0, float(speed)))

    print(f"🎙️ Voice selected: {voice} ({AVAILABLE_VOICES[voice]['name']})")
    print(f"🌡️ Temperature: {temperature}")
    print(f"⚡ Speed: {speed}x")

    call_id = await register_call(phone)
    call_id = str(call_id)

    call_recordings[call_id] = {"incoming": [], "outgoing": [], "start_time": time.time()}
    call_metadata[call_id] = {
        "phone": phone,
        "voice": voice,
        "temperature": temperature,
        "speed": speed
    }

    await update_call_status(int(call_id), "pick")

    return {
        "call_id": call_id,
        "voice": voice,
        "temperature": temperature,
        "speed": speed
    }


@app.get("/call-analysis/{call_id}")
async def get_call_analysis(call_id: str, request: Request):
    token = get_token_from_request(request)
    verify_jwt_token(token)

    analysis_file_path = f"recordings/analysis/{call_id}_analysis.json"

    if not os.path.exists(analysis_file_path):
        raise HTTPException(status_code=404, detail=f"Analysis not found for call_id: {call_id}")

    try:
        with open(analysis_file_path, "r", encoding="utf-8") as f:
            analysis_data = json.load(f)
        return analysis_data
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Error reading analysis file")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error retrieving analysis: {str(e)}")


@app.websocket("/media-stream-browser")
async def media_stream_browser(websocket: WebSocket):
    await websocket.accept()

    openai_url = 'wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2025-06-03'
    headers = [
        ("Authorization", f"Bearer {OPENAI_API_KEY}"),
        ("OpenAI-Beta", "realtime=v1")
    ]

    async with ws_connect(openai_url, additional_headers=headers) as openai_ws:
        session_initialized = False
        call_id = None

        user_pcm_buffer = io.BytesIO()
        agent_pcm_buffer = io.BytesIO()

        async def receive_from_browser():
            nonlocal session_initialized, call_id
            async for msg in websocket.iter_text():
                data = json.loads(msg)

                if data.get("event") == "start":
                    token = data["start"]["customParameters"].get("token")
                    if not token:
                        print("❌ No token provided")
                        await websocket.close(code=1008, reason="Authentication required")
                        return

                    try:
                        user_data = verify_jwt_token(token)
                        print(f"✅ WebSocket authenticated: {user_data['username']}")
                    except HTTPException as e:
                        print(f"❌ Invalid token: {e.detail}")
                        await websocket.close(code=1008, reason="Invalid token")
                        return

                    call_id = data["start"]["customParameters"].get("call_id")
                    await initialize_session(openai_ws, call_id)
                    await send_initial_conversation_item(openai_ws)
                    session_initialized = True
                    continue

                if data.get("event") == "media" and session_initialized:
                    payload_b64 = data["media"]["payload"]
                    pcm_bytes = base64.b64decode(payload_b64)
                    user_pcm_buffer.write(pcm_bytes)

                    mulaw_bytes = audioop.lin2ulaw(pcm_bytes, 2)
                    audio_append = {
                        "type": "input_audio_buffer.append",
                        "audio": base64.b64encode(mulaw_bytes).decode('utf-8')
                    }
                    await openai_ws.send(json.dumps(audio_append))

                if data.get("event") == "stop":
                    break

        async def receive_from_openai_and_forward():
            async for raw in openai_ws:
                response = json.loads(raw)
                rtype = response.get("type")

                if rtype == 'input_audio_buffer.speech_started':
                    print("User interruption detected")
                    await openai_ws.send(json.dumps({"type": "response.cancel"}))
                    await websocket.send_json({"event": "clear"})
                    continue

                if rtype in LOG_EVENT_TYPES:
                    continue

                if rtype == "response.audio.delta" and "delta" in response:
                    mulaw_b64 = response["delta"]
                    mulaw_bytes = base64.b64decode(mulaw_b64)

                    try:
                        pcm = audioop.ulaw2lin(mulaw_bytes, 2)
                    except Exception:
                        pcm = mulaw_bytes

                    agent_pcm_buffer.write(pcm)
                    pcm_b64 = base64.b64encode(pcm).decode('utf-8')

                    out = {
                        "event": "media",
                        "media": {
                            "payload": pcm_b64,
                            "format": "raw_pcm",
                            "sampleRate": 8000,
                            "channels": 1,
                            "bitDepth": 16
                        }
                    }
                    await websocket.send_json(out)

                elif rtype == "response.function_call_arguments.done":
                    outgoing_func_result = {
                        "event": "function_result",
                        "name": response.get("name"),
                        "arguments": response.get("arguments")
                    }
                    await websocket.send_json(outgoing_func_result)

        recv_task = asyncio.create_task(receive_from_browser())
        send_task = asyncio.create_task(receive_from_openai_and_forward())

        try:
            await recv_task
        finally:
            if not send_task.done():
                send_task.cancel()
                with suppress(asyncio.CancelledError):
                    await send_task

            user_file_path = f"recordings/user/{call_id}_user.wav"
            agent_file_path = f"recordings/agent/{call_id}_agent.wav"

            def save_wav_file(path: str, pcm_data: bytes):
                with wave.open(path, 'wb') as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(8000)
                    wf.writeframes(pcm_data)

            save_wav_file(user_file_path, user_pcm_buffer.getvalue())
            save_wav_file(agent_file_path, agent_pcm_buffer.getvalue())

            print(f"✅ Saved user audio: {user_file_path}")
            print(f"✅ Saved agent audio: {agent_file_path}")

            user_transcript = await transcribe_audio(user_file_path)
            agent_transcript = await transcribe_audio(agent_file_path)

            transcripts_output = {
                "call_id": call_id,
                "user_transcript": user_transcript,
                "agent_transcript": agent_transcript
            }

            print(f"📝 Transcripts saved for call {call_id}")

            await analyze_call_with_llm(call_id, user_transcript, agent_transcript)

            with open(f"recordings/{call_id}_transcript.json", "w", encoding="utf-8") as f:
                json.dump(transcripts_output, f, ensure_ascii=False, indent=2)

            await websocket.close()


async def send_initial_conversation_item(openai_ws):
    await openai_ws.send(json.dumps({"type": "response.create"}))


async def initialize_session(openai_ws, call_id):
    meta = call_metadata.get(call_id, {})
    voice = meta.get("voice", "sage")
    temperature = meta.get("temperature", 0.8)
    speed = meta.get("speed", 1.05)
    caller = meta.get("phone", "")

    SYSTEM_MESSAGE = build_system_message(
        instructions="",
        caller=caller,
        voice=voice
    )

    print(f"🔧 Initializing session: voice={voice}, temp={temperature}, speed={speed}x")

    session_update = {
        "type": "session.update",
        "session": {
            "turn_detection": {
                "type": "server_vad",
                "threshold": 0.8,
                "prefix_padding_ms": 500,
                "silence_duration_ms": 1000,
                "create_response": True,
                "interrupt_response": True,
            },
            "input_audio_format": "g711_ulaw",
            "output_audio_format": "g711_ulaw",
            "voice": voice,
            "instructions": SYSTEM_MESSAGE,
            "modalities": ["text", "audio"],
            "temperature": temperature,
            "speed": speed,
            'tool_choice': 'auto',
            'tools': function_call_tools
        }
    }

    await openai_ws.send(json.dumps(session_update))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
