import sys
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

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
    try:
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
    except Exception as e:
        print(f"[WARN] Whisper transcription error: {e}")
        return ""


def _send_to_sarvam(piece_path: str) -> str:
    """Send one <=30s WAV file to Sarvam and return verbatim transcript in Roman transliteration."""
    api_key = os.getenv("SARVAM_API_KEY") or SARVAM_API_KEY
    if not api_key:
        raise RuntimeError("SARVAM_API_KEY is not set in environment / .env")

    headers = {"api-subscription-key": api_key}

    # 1. Primary: saaras:v4 with mode="translit" for native, verbatim Roman transliteration
    try:
        with open(piece_path, "rb") as f:
            files = {"file": (os.path.basename(piece_path), f, "audio/wav")}
            data = {"model": "saaras:v4", "mode": "translit"}
            response = requests.post(
                "https://api.sarvam.ai/speech-to-text",
                headers=headers,
                files=files,
                data=data,
                timeout=120,
            )
            if response.ok:
                txt = response.json().get("transcript", "").strip()
                if txt:
                    return txt
    except Exception as e:
        print(f"[WARN] Sarvam saaras:v4 translit attempt error: {e}")

    # 2. Secondary: saarika:v2.5 speech-to-text to capture verbatim spoken Hindi/Hinglish
    try:
        with open(piece_path, "rb") as f:
            files = {"file": (os.path.basename(piece_path), f, "audio/wav")}
            data = {"model": "saarika:v2.5", "language_code": "hi-IN"}
            response = requests.post(
                "https://api.sarvam.ai/speech-to-text",
                headers=headers,
                files=files,
                data=data,
                timeout=120,
            )
            if response.ok:
                txt = response.json().get("transcript", "").strip()
                if txt:
                    return txt
    except Exception as e:
        print(f"[WARN] Sarvam saarika:v2.5 attempt error: {e}")

    # 3. Fallback: saaras:v2.5 speech-to-text-translate
    with open(piece_path, "rb") as f:
        files = {"file": (os.path.basename(piece_path), f, "audio/wav")}
        data = {"model": "saaras:v2.5"}
        response = requests.post(
            SARVAM_STT_TRANSLATE_URL,
            headers=headers,
            files=files,
            data=data,
            timeout=120,
        )

    if not response.ok:
        print(f"\n[ERROR] Sarvam returned {response.status_code}: {response.text}\n")
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
            print(f"  -> Sarvam piece {i + 1}/{total_pieces} ...")
            piece_text = _send_to_sarvam(piece_path)
            if piece_text:
                lines.append(f"[{ts}] {piece_text}")
        finally:
            if os.path.exists(piece_path):
                os.remove(piece_path)

    return "\n".join(lines).strip()


def transcribe_chunk(chunk_path: str, language: str = "english", time_offset_sec: float = 0.0) -> str:
    """
    Route one chunk to Whisper or Sarvam depending on language choice,
    with smart bi-directional fallback if one engine returns empty text.
    """
    is_indic = language.lower() in ["hinglish", "hindi", "hindi_font", "devanagari", "romanized", "roman_transliteration"]

    if is_indic:
        try:
            sarvam_text = transcribe_chunk_sarvam(chunk_path, time_offset_sec=time_offset_sec)
            if sarvam_text and sarvam_text.strip():
                return sarvam_text
        except Exception as e:
            print(f"[WARN] Sarvam transcription failed ({e}), falling back to Whisper...")
        return transcribe_chunk_whisper(chunk_path, time_offset_sec=time_offset_sec)

    # English mode: Whisper first
    whisper_text = transcribe_chunk_whisper(chunk_path, time_offset_sec=time_offset_sec)
    if whisper_text and whisper_text.strip():
        return whisper_text

    # Automatic fallback: if Whisper returned empty on this chunk, try Sarvam
    try:
        print("[WARN] Whisper returned no dialogue on this chunk. Attempting Sarvam AI fallback...")
        sarvam_text = transcribe_chunk_sarvam(chunk_path, time_offset_sec=time_offset_sec)
        if sarvam_text and sarvam_text.strip():
            print("[OK] Sarvam AI successfully extracted spoken dialogue!")
            return sarvam_text
    except Exception as e:
        print(f"Sarvam fallback also failed: {e}")

    return ""


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
    if not raw_transcript:
        print("No dialogue lines detected in audio chunks.")
        return ""

    print("Running speaker diarization pass...")
    diarized_transcript = diarize_transcript(raw_transcript)
    print("Transcription and diarization complete.")

    # ALWAYS ensure transcript is in Roman transliteration for Hinglish audio
    is_indic = language.lower() in ["hinglish", "hindi", "hindi_font", "devanagari", "romanized", "roman_transliteration"]
    if is_indic or re.search(r"[\u0900-\u097F]", diarized_transcript):
        print("Ensuring transcript is in Roman transliteration for Hinglish audio...")
        diarized_transcript = transliterate_transcript_to_roman(diarized_transcript)

    return diarized_transcript


def transliterate_transcript_to_roman(transcript: str) -> str:
    """
    Converts any Devanagari Hindi lines in the transcript strictly into
    verbatim Roman script Hinglish (Latin alphabet), keeping timestamps and speaker tags.
    """
    if not transcript or not transcript.strip():
        return transcript

    lines = transcript.splitlines()
    if not lines:
        return transcript

    has_devanagari = any(re.search(r"[\u0900-\u097F]", l) for l in lines)
    if not has_devanagari:
        return transcript

    from core.rag_engine import get_llm
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.output_parsers import StrOutputParser

    prompt = ChatPromptTemplate.from_messages([
        (
            "system",
            """You are an expert Hindi/Hinglish transliterator.
Transliterate each line of Devanagari Hindi text strictly into natural Roman script Hinglish (Latin alphabet).
RULES:
1. Do NOT translate into English. Preserve the exact spoken Hindi words in Latin characters (e.g. 'bhaiya apne ko', 'yaar humko bhi mila tha', 'kamaal ka', 'bawaal hai', 'marks milenge').
2. Retain all English/technical words in English spelling (Git, video, minutes, developers, project, etc.).
3. Maintain line format exactly: `[MM:SS] Speaker X: <roman_transliterated_dialogue>`
4. Output ONLY the transliterated lines without introductory or concluding text."""
        ),
        ("human", "{chunk}")
    ])

    batch_size = 6
    converted_lines = []

    try:
        chain = prompt | get_llm(model="openai/gpt-oss-120b", max_tokens=3000) | StrOutputParser()
        for i in range(0, len(lines), batch_size):
            batch = lines[i:i + batch_size]
            batch_text = "\n".join(batch)
            if not re.search(r"[\u0900-\u097F]", batch_text):
                converted_lines.extend(batch)
                continue
            res = ""
            try:
                res = chain.invoke({"chunk": batch_text}).strip()
            except Exception as batch_err:
                print(f"[WARN] Batch transliteration with 120b failed ({batch_err}), attempting 20b fallback...")
                try:
                    fallback_chain = prompt | get_llm(model="openai/gpt-oss-20b", max_tokens=2500) | StrOutputParser()
                    res = fallback_chain.invoke({"chunk": batch_text}).strip()
                except Exception as fb_err:
                    print(f"[WARN] Fallback transliteration also failed: {fb_err}")
            if res:
                converted_lines.append(res)
            else:
                converted_lines.append(batch_text)
        return "\n".join(converted_lines).strip()
    except Exception as e:
        print(f"[WARN] Transliteration fallback ({e}), returning original lines.")
        return transcript
