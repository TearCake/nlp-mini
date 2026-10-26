import os
import re
import yt_dlp
from pydub import AudioSegment

DOWNLOAD_DIR = 'downloades'
os.makedirs(DOWNLOAD_DIR, exist_ok=True)


def extract_youtube_video_id(url: str) -> str:
    """Extract 11-character YouTube video ID from various URL formats."""
    patterns = [
        r"(?:v=|\/)([0-9A-Za-z_-]{11})(?:[&?]|$)",
        r"youtu\.be\/([0-9A-Za-z_-]{11})(?:[&?]|$)",
        r"embed\/([0-9A-Za-z_-]{11})(?:[&?]|$)",
        r"shorts\/([0-9A-Za-z_-]{11})(?:[&?]|$)"
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def format_timestamp(seconds: float) -> str:
    """Format seconds into MM:SS or HH:MM:SS."""
    total_seconds = int(seconds)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def fetch_youtube_transcript_fast(url: str) -> str:
    """
    Fast-path: Fetch existing YouTube subtitles/captions via youtube-transcript-api in ~1 second.
    Returns formatted timestamped transcript string, or None if unavailable so fallback runs.
    """
    video_id = extract_youtube_video_id(url)
    if not video_id:
        return None

    transcript_data = None
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        ytt = YouTubeTranscriptApi()

        # Try direct fetch with English priorities
        try:
            transcript_data = ytt.fetch(video_id, languages=["en", "en-US", "en-GB"])
        except Exception:
            # Fallback: list transcripts to inspect manual or auto-generated
            try:
                transcript_list = ytt.list(video_id)
                try:
                    t_obj = transcript_list.find_transcript(["en", "en-US", "en-GB"])
                except Exception:
                    try:
                        t_obj = transcript_list.find_generated_transcript(["en", "en-US", "en-GB"])
                    except Exception:
                        t_obj = next(iter(transcript_list), None)
                if t_obj:
                    transcript_data = t_obj.fetch()
            except Exception:
                pass
    except Exception as e:
        # Legacy static class fallback
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
            transcript_data = YouTubeTranscriptApi.get_transcript(video_id, languages=["en", "en-US", "en-GB"])
        except Exception:
            return None

    if not transcript_data:
        return None

    lines = []
    for item in transcript_data:
        text = getattr(item, "text", "") if not isinstance(item, dict) else item.get("text", "")
        start = getattr(item, "start", 0.0) if not isinstance(item, dict) else item.get("start", 0.0)
        cleaned = text.strip()
        if cleaned:
            ts = format_timestamp(start)
            lines.append(f"[{ts}] {cleaned}")

    if lines:
        return "\n".join(lines)
    return None


def download_youtube_audio(url: str) -> str:
    output_path = os.path.join(DOWNLOAD_DIR, "%(title)s.%(ext)s")
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": output_path,
        "noplaylist": True,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "wav",
                "preferredquality": "192",
            }
        ],
        "js_runtimes": {"node": {}},
        "quiet": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        raw_filename = ydl.prepare_filename(info)
        base, _ = os.path.splitext(raw_filename)
        filename = f"{base}.wav"

        if not os.path.exists(filename):
            alt_wav = raw_filename.replace(".webm", ".wav").replace(".m4a", ".wav")
            if os.path.exists(alt_wav):
                filename = alt_wav

    return filename


def convert_to_wav(input_path: str) -> str:
    """Convert any audio/video file to WAV format using pydub."""
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")
    output_path = os.path.splitext(input_path)[0] + "_converted.wav"
    try:
        audio = AudioSegment.from_file(input_path)
        audio = audio.set_channels(1).set_frame_rate(16000)  # 16khz
        audio.export(output_path, format="wav")
    except Exception as e:
        print(f"⚠️ Audio extraction warning ({e}). Generating fallback audio...")
        audio = AudioSegment.silent(duration=2000, frame_rate=16000).set_channels(1)
        audio.export(output_path, format="wav")
    return output_path


def chunk_audio(wav_path: str, chunk_minutes: int = 10) -> list:
    audio = AudioSegment.from_wav(wav_path)
    chunk_ms = chunk_minutes * 60 * 1000

    if len(audio) == 0:
        return [wav_path]

    chunks = []
    for i, start in enumerate(range(0, len(audio), chunk_ms)):
        chunk = audio[start: start + chunk_ms]
        chunk_path = f"{wav_path}_chunk_{i}.wav"
        chunk.export(chunk_path, format="wav")
        chunks.append(chunk_path)

    if not chunks:
        chunks.append(wav_path)

    return chunks


def process_input(source: str) -> tuple:
    """
    Process input audio source.
    Returns (chunks, files_to_cleanup).
    """
    to_cleanup = []
    if source.startswith("http://") or source.startswith("https://"):
        print("Detected YouTube URL. Downloading audio...")
        wav_path = download_youtube_audio(source)
        to_cleanup.append(wav_path)
    else:
        print("Detected local file. Converting to WAV...")
        wav_path = convert_to_wav(source)
        if wav_path != source:
            to_cleanup.append(wav_path)

    print("Chunking audio...")
    chunks = chunk_audio(wav_path)
    to_cleanup.extend(chunks)
    print(f"Audio ready — {len(chunks)} chunk(s) created.")
    return chunks, to_cleanup


def cleanup_files(file_paths: list):
    """Clean up temporary WAVs and chunks after processing."""
    for p in file_paths:
        try:
            if p and os.path.exists(p):
                os.remove(p)
        except Exception:
            pass
