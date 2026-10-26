import sys
from dotenv import load_dotenv

if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from utils.audio_processor import process_input, fetch_youtube_transcript_fast, cleanup_files
from core.transcriber import transcribe_all, diarize_transcript
from core.extractor import extract_meeting_insights, convert_transcript_format
from core.rag_engine import build_rag_chain, ask_questions

load_dotenv()


def run_pipeline(source: str, language: str = "english", output_format: str = "translated", meeting_id: str = None) -> dict:
    print(f"Starting AI Video Assistant pipeline for meeting [{meeting_id}] with output format [{output_format}]...")

    transcript = None

    # Step 1: Fast-path for YouTube URLs (if existing captions/subtitles are available)
    if (source.startswith("http://") or source.startswith("https://")) and language.lower() == "english":
        print("Checking YouTube captions fast-path...")
        try:
            fast_transcript = fetch_youtube_transcript_fast(source)
            if fast_transcript:
                print("⚡ Subtitles fetched instantly via YouTube Captions Fast-Path!")
                transcript = diarize_transcript(fast_transcript)
        except Exception as e:
            print(f"Fast-path check failed ({e}), proceeding with Whisper audio pipeline...")

    # Step 2: Fallback to audio download/extraction + Whisper/Sarvam transcription
    if not transcript:
        chunks, to_cleanup = process_input(source)
        try:
            transcript = transcribe_all(chunks, language)
        finally:
            cleanup_files(to_cleanup)

    # Step 3: Format transcript dialogue according to user's selected output_format
    fmt = (output_format or "translated").lower().strip()
    if fmt in ["hindi", "hindi_font", "devanagari", "romanized", "roman_transliteration", "hinglish"]:
        print(f"Converting transcript dialogue to format [{fmt}]...")
        transcript = convert_transcript_format(transcript, fmt)

    print(f"Transcript ready ({len(transcript)} chars). Generating meeting insights in [{fmt}]...")

    # Step 4: Extract structured insights (Title, Summary, Action items, Key decisions, Questions)
    insights = extract_meeting_insights(transcript, output_format=fmt)

    title = insights.get("title", "Meeting Workspace")
    summary = insights.get("summary", "")
    action_items = insights.get("action_items", [])
    key_decisions = insights.get("key_decisions", [])
    open_questions = insights.get("open_questions", [])

    # Step 5: Index into vector store for RAG chat
    print(f"Indexing transcript into vector store for meeting [{meeting_id}]...")
    build_rag_chain(transcript, meeting_id, output_format=fmt)

    return {
        "meeting_id": meeting_id,
        "title": title,
        "transcript": transcript,
        "summary": summary,
        "action_items": action_items,
        "key_decisions": key_decisions,
        "open_questions": open_questions,
        "output_format": fmt,
    }


if __name__ == "__main__":
    source = input("Enter YouTube URL or local file path: ").strip()
    language = input("Language (english/hinglish): ").strip() or "english"
    meeting_id = input("Meeting ID optional: ").strip() or "cli-test-meeting"

    result = run_pipeline(source, language, meeting_id)

    print("\n" + "=" * 60)
    print(f"Meeting ID: {result['meeting_id']}")
    print(f"Title: {result['title']}")
    print(f"\nSummary:\n{result['summary']}")
    print(f"\nAction Items:\n{result['action_items']}")
    print(f"\nKey Decisions:\n{result['key_decisions']}")
    print(f"\nOpen Questions:\n{result['open_questions']}")
    print("=" * 60)

    print("\nChat with your meeting (type 'exit' to quit)\n")

    from core.rag_engine import load_rag_chain

    rag_chain = load_rag_chain(meeting_id)

    while True:
        question = input("You: ").strip()
        if question.lower() in ["exit", "quit", "q"]:
            print("Goodbye!")
            break
        if not question:
            continue
        answer = ask_questions(rag_chain, question)
        print(f"\nAssistant: {answer}\n")