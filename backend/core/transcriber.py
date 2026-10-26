import os
import re
import requests
from dotenv import load_dotenv
from pydub import AudioSegment
import whisper

load_dotenv()

# Sarvam's sync STT-translate API rejects audio longer than 30s.
# We slice each chunk into 25s pieces (with a 5s safety margin) before sending.
SARVAM_PIECE_SECONDS = 25

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY")
SARVAM_STT_TRANSLATE_URL = "https://api.sarvam.ai/speech-to-text-translate"
SARVAM_MODEL = os.getenv("SARVAM_STT_MODEL", "saaras:v2.5")

_model = None


def load_model():
    global _model
    if _model is None:
        print(f"Loading Whisper model: {WHISPER_MODEL} ...")
        _model = whisper.load_model(WHISPER_MODEL)
        print("Whisper model loaded.")
    return _model


def format_timestamp(seconds: float) -> str:
    total_seconds = int(seconds)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def transcribe_chunk_whisper(chunk_path: str, time_offset_sec: float = 0.0) -> str:
    """Transcribe chunk using Whisper and preserve segment timestamps."""
    model = load_model()
    result = model.transcribe(chunk_path, task="transcribe")

    segments = result.get("segments", [])
    if segments:
        lines = []
        for seg in segments:
            text = seg.get("text", "").strip()
            if text:
                start_sec = seg.get("start", 0.0) + time_offset_sec
                ts = format_timestamp(start_sec)
                lines.append(f"[{ts}] {text}")
        if lines:
            return "\n".join(lines)

    return result.get("text", "").strip()


def _send_to_sarvam(piece_path: str) -> str:
    """Send one <=30s WAV file to Sarvam and return English translated transcript."""
    api_key = os.getenv("SARVAM_API_KEY") or SARVAM_API_KEY
    if not api_key:
        raise RuntimeError("SARVAM_API_KEY is not set in environment / .env")

    headers = {"api-subscription-key": api_key}

    with open(piece_path, "rb") as f:
        files = {"file": (os.path.basename(piece_path), f, "audio/wav")}
        data = {
            "model": os.getenv("SARVAM_STT_MODEL", "saaras:v2.5"),
        }
        response = requests.post(
            SARVAM_STT_TRANSLATE_URL,
            headers=headers,
            files=files,
            data=data,
            timeout=120,
        )

    if not response.ok:
        print(f"\n❌ Sarvam returned {response.status_code}: {response.text}\n")
        response.raise_for_status()

    res_json = response.json()
    return res_json.get("transcript", "").strip()


def transcribe_chunk_sarvam(chunk_path: str, time_offset_sec: float = 0.0) -> str:
    """
    Sarvam sync API only accepts <=30s audio. We split this chunk into
    25-second pieces, translate each with Sarvam, and join with timestamps.
    """
    audio = AudioSegment.from_wav(chunk_path)
    piece_ms = SARVAM_PIECE_SECONDS * 1000

    lines = []
    total_pieces = (len(audio) + piece_ms - 1) // piece_ms

    for i, start in enumerate(range(0, len(audio), piece_ms)):
        piece = audio[start: start + piece_ms]
        piece_path = f"{chunk_path}_sv_{i}.wav"
        piece.export(piece_path, format="wav")

        current_sec = (start / 1000.0) + time_offset_sec
        ts = format_timestamp(current_sec)

        try:
            print(f"  → Sarvam piece {i + 1}/{total_pieces} ...")
            piece_text = _send_to_sarvam(piece_path)
            if piece_text:
                lines.append(f"[{ts}] {piece_text}")
        finally:
            if os.path.exists(piece_path):
                os.remove(piece_path)

    return "\n".join(lines).strip()


def transcribe_chunk(chunk_path: str, language: str = "english", time_offset_sec: float = 0.0) -> str:
    """Route one chunk to Whisper or Sarvam depending on language choice."""
    if language.lower() == "hinglish":
        try:
            return transcribe_chunk_sarvam(chunk_path, time_offset_sec=time_offset_sec)
        except Exception as e:
            print(f"⚠️ Sarvam transcription failed ({e}), falling back to Whisper...")
            return transcribe_chunk_whisper(chunk_path, time_offset_sec=time_offset_sec)
    return transcribe_chunk_whisper(chunk_path, time_offset_sec=time_offset_sec)


def _parse_time_to_seconds(ts_str: str) -> float:
    try:
        parts = ts_str.strip("[] ").split(":")
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except Exception:
        pass
    return 0.0


def diarize_transcript(transcript: str) -> str:
    """
    Safely annotates speaker labels onto transcript lines without modifying or truncating dialogue text.
    Every line and timestamp from the original transcript is 100% preserved.
    """
    if not transcript or not transcript.strip():
        return transcript

    lines = transcript.splitlines()
    if not lines:
        return transcript

    # If transcript already has speaker annotations (e.g. from Sarvam), keep as is
    has_speakers = any(re.match(r"^\[\d+:\d+(?::\d+)?\]\s*[^:]+:\s*", l.strip()) for l in lines[:15])
    if has_speakers:
        return transcript

    sample_lines = lines[:25]
    sample_text = "\n".join(sample_lines)

    turns = []
    try:
        from core.rag_engine import get_llm
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_core.output_parsers import JsonOutputParser

        prompt = ChatPromptTemplate.from_messages([
            (
                "system",
                """Analyze the transcript sample and identify speakers and speech turns.
Return ONLY valid JSON:
{{
  "dialogue_type": "monologue",
  "speakers": ["Speaker 1"],
  "turns": [
    {{"timestamp": "00:00", "speaker": "Speaker 1"}}
  ]
}}
Do not write commentary outside the JSON."""
            ),
            ("human", "Transcript sample:\n\n{transcript}")
        ])

        chain = prompt | get_llm(max_tokens=300) | JsonOutputParser()
        analysis = chain.invoke({"transcript": sample_text})
        turns = analysis.get("turns", []) if isinstance(analysis, dict) else []
    except Exception as e:
        print(f"Speaker turn detection fallback ({e}), defaulting to Speaker 1.")

    turn_map = []
    for turn in turns:
        if isinstance(turn, dict):
            ts = turn.get("timestamp", "00:00")
            spk = turn.get("speaker", "Speaker 1").strip()
            turn_map.append((_parse_time_to_seconds(ts), spk))
    turn_map.sort(key=lambda x: x[0])

    output_lines = []
    current_speaker = "Speaker 1"

    for line in lines:
        trimmed = line.strip()
        if not trimmed:
            continue

        match = re.match(r"^\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*(?:([^:\n]+):\s*)?(.*)$", trimmed)
        if match:
            ts_str = match.group(1)
            existing_speaker = match.group(2)
            dialogue = match.group(3)

            if existing_speaker:
                output_lines.append(trimmed)
                continue

            sec = _parse_time_to_seconds(ts_str)
            for turn_sec, turn_spk in turn_map:
                if sec >= turn_sec:
                    current_speaker = turn_spk

            output_lines.append(f"[{ts_str}] {current_speaker}: {dialogue}")
        else:
            output_lines.append(trimmed)

    return "\n".join(output_lines)


def transcribe_all(chunks: list, language: str = "english") -> str:
    """Transcribe all chunks sequentially with accurate time offsets and diarize."""
    full_transcript_parts = []
    engine = "Sarvam AI" if language.lower() == "hinglish" else "Whisper"
    print(f"Using {engine} for transcription.")

    accumulated_seconds = 0.0
    for i, chunk in enumerate(chunks):
        print(f"Transcribing chunk {i + 1}/{len(chunks)}...")
        chunk_text = transcribe_chunk(chunk, language=language, time_offset_sec=accumulated_seconds)
        if chunk_text:
            full_transcript_parts.append(chunk_text)

        # Increment time offset by chunk duration
        try:
            audio = AudioSegment.from_wav(chunk)
            accumulated_seconds += len(audio) / 1000.0
        except Exception:
            accumulated_seconds += 10.0 * 60.0

    raw_transcript = "\n".join(full_transcript_parts).strip()

    print("Running speaker diarization pass...")
    diarized_transcript = diarize_transcript(raw_transcript)
    print("Transcription and diarization complete.")

    return diarized_transcript
