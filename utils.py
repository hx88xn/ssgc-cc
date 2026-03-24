import uuid
import io
from pydub import AudioSegment
import audioop

try:
    import pyaudio
except ImportError:
    pyaudio = None

CHUNK = 1024
if pyaudio:
    FORMAT = pyaudio.paInt16
else:
    FORMAT = None
CHANNELS = 1
RATE = 8000


def generate_call_id():
    return str(uuid.uuid4())


def get_total_duration_ms(events):
    if not events:
        return 0
    last_offset = max(offset for offset, _ in events)
    chunk_duration_ms = int((CHUNK / RATE) * 1000)
    total = int(last_offset * 1000) + chunk_duration_ms
    return total


def merge_timeline_events(events, total_duration_ms):
    base = AudioSegment.silent(duration=total_duration_ms, frame_rate=RATE)
    sorted_events = sorted(events, key=lambda x: x[0])
    for offset, audio_data in sorted_events:
        try:
            pcm_audio = audioop.ulaw2lin(audio_data, 2)
            seg = AudioSegment.from_raw(io.BytesIO(pcm_audio), frame_rate=RATE, channels=1, sample_width=2)
            base = base.overlay(seg, position=int(offset * 1000))
        except Exception as e:
            print(f"Error overlaying chunk at {offset:.2f} sec: {e}")
    return base


def make_filenames(call_id):
    return (
        f"call_{call_id}_incoming.wav",
        f"call_{call_id}_outgoing.wav",
        f"call_{call_id}_merged.wav"
    )
