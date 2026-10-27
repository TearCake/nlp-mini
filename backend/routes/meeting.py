import json
import os
import shutil
import uuid
import re
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.database import get_db
from models.models import User, Meeting
from auth.auth import get_current_user
from main import run_pipeline

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

router = APIRouter(prefix="/meetings", tags=["Meetings"])


class RenameMeetingRequest(BaseModel):
    title: str


class MeetingRequest(BaseModel):
    source: str
    language: str = "english"
    output_format: str = "translated"


class FormatMeetingRequest(BaseModel):
    output_format: str


def normalize_format(fmt: str) -> str:
    f = (fmt or "translated").lower().strip()
    if f in ["hindi", "hindi_font", "devanagari"]:
        return "hindi"
    elif f in ["romanized", "roman_transliteration", "hinglish"]:
        return "romanized"
    else:
        return "translated"


@router.post("/process")
def process_meeting(
    req: MeetingRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    normalized_fmt = normalize_format(req.output_format)

    # Step 1: create initial meeting record
    new_meeting = Meeting(
        user_id=current_user.id,
        source=req.source,
        language=req.language or "english",
        output_format=normalized_fmt
    )

    db.add(new_meeting)
    db.commit()
    db.refresh(new_meeting)

    meeting_id = str(new_meeting.id)

    # Step 2: run AI pipeline safely
    try:
        result = run_pipeline(
            source=req.source,
            language=req.language,
            output_format=normalized_fmt,
            meeting_id=meeting_id
        )
    except Exception as e:
        db.delete(new_meeting)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process meeting: {str(e)}"
        )

    # Step 3: save result in DB and initialize format cache
    final_fmt = normalize_format(result.get("output_format", normalized_fmt))
    orig_transcript = result.get("original_transcript", result["transcript"])

    new_meeting.title = result["title"]
    new_meeting.transcript = result["transcript"]
    new_meeting.original_transcript = orig_transcript
    new_meeting.summary = result["summary"]
    new_meeting.action_items = json.dumps(result["action_items"])
    new_meeting.key_decisions = json.dumps(result["key_decisions"])
    new_meeting.open_questions = json.dumps(result["open_questions"])
    new_meeting.vector_path = f"vector_db/{meeting_id}"
    new_meeting.language = result.get("effective_language", req.language or "english")
    new_meeting.output_format = final_fmt

    # Cache this initial format so converting back is instantaneous and 100% lossless
    initial_cache = {
        final_fmt: {
            "title": result["title"],
            "summary": result["summary"],
            "action_items": result["action_items"],
            "key_decisions": result["key_decisions"],
            "open_questions": result["open_questions"],
            "transcript": result["transcript"],
        }
    }
    new_meeting.formats_data = json.dumps(initial_cache)

    db.commit()
    db.refresh(new_meeting)

    return {
        "meeting_id": new_meeting.id,
        "title": new_meeting.title,
        "language": new_meeting.language,
        "transcript": new_meeting.transcript,
        "summary": new_meeting.summary,
        "action_items": json.loads(new_meeting.action_items),
        "key_decisions": json.loads(new_meeting.key_decisions),
        "open_questions": json.loads(new_meeting.open_questions),
        "output_format": new_meeting.output_format,
    }


@router.post("/upload")
def upload_and_process_meeting(
    file: UploadFile = File(...),
    language: str = Form("english"),
    output_format: str = Form("translated"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No file selected."
        )

    # Sanitize and assign unique filename in uploads/
    clean_name = re.sub(r"[^a-zA-Z0-9_.-]", "_", file.filename)
    unique_prefix = uuid.uuid4().hex[:8]
    saved_filename = f"{unique_prefix}_{clean_name}"
    file_path = os.path.join(UPLOAD_DIR, saved_filename)

    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save uploaded file: {str(e)}"
        )

    normalized_fmt = normalize_format(output_format)

    # Step 1: create initial meeting record with clean display name
    new_meeting = Meeting(
        user_id=current_user.id,
        source=file.filename,
        language=language or "english",
        output_format=normalized_fmt
    )

    db.add(new_meeting)
    db.commit()
    db.refresh(new_meeting)

    meeting_id = str(new_meeting.id)

    # Step 2: run AI pipeline safely on the uploaded file
    try:
        result = run_pipeline(
            source=file_path,
            language=language,
            output_format=normalized_fmt,
            meeting_id=meeting_id
        )
    except Exception as e:
        db.delete(new_meeting)
        db.commit()
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process uploaded file: {str(e)}"
        )

    # Step 3: save result in DB and initialize format cache
    final_fmt = normalize_format(result.get("output_format", normalized_fmt))
    orig_transcript = result.get("original_transcript", result["transcript"])

    new_meeting.title = result["title"]
    new_meeting.transcript = result["transcript"]
    new_meeting.original_transcript = orig_transcript
    new_meeting.summary = result["summary"]
    new_meeting.action_items = json.dumps(result["action_items"])
    new_meeting.key_decisions = json.dumps(result["key_decisions"])
    new_meeting.open_questions = json.dumps(result["open_questions"])
    new_meeting.vector_path = f"vector_db/{meeting_id}"
    new_meeting.language = result.get("effective_language", language or "english")
    new_meeting.output_format = final_fmt

    # Cache initial format for instant zero-loss retrieval
    initial_cache = {
        final_fmt: {
            "title": result["title"],
            "summary": result["summary"],
            "action_items": result["action_items"],
            "key_decisions": result["key_decisions"],
            "open_questions": result["open_questions"],
            "transcript": result["transcript"],
        }
    }
    new_meeting.formats_data = json.dumps(initial_cache)

    db.commit()
    db.refresh(new_meeting)

    return {
        "meeting_id": new_meeting.id,
        "title": new_meeting.title,
        "language": new_meeting.language,
        "transcript": new_meeting.transcript,
        "summary": new_meeting.summary,
        "action_items": json.loads(new_meeting.action_items),
        "key_decisions": json.loads(new_meeting.key_decisions),
        "open_questions": json.loads(new_meeting.open_questions),
        "output_format": new_meeting.output_format,
    }


@router.get("/")
def get_meetings(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    meetings = (
        db.query(Meeting)
        .filter(Meeting.user_id == current_user.id)
        .order_by(Meeting.created_at.desc())
        .all()
    )

    return [
        {
            "id": meeting.id,
            "title": meeting.title,
            "source": meeting.source,
            "language": meeting.language or "english",
            "output_format": meeting.output_format or "translated",
            "created_at": meeting.created_at,
            "chat_count": len(meeting.chats)
        }
        for meeting in meetings
    ]


@router.get("/{meeting_id}")
def get_single_meeting(
    meeting_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    meeting = (
        db.query(Meeting)
        .filter(
            Meeting.id == meeting_id,
            Meeting.user_id == current_user.id
        )
        .first()
    )

    if not meeting:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Meeting not found"
        )

    return {
        "id": meeting.id,
        "title": meeting.title,
        "source": meeting.source,
        "language": meeting.language or "english",
        "transcript": meeting.transcript,
        "summary": meeting.summary,
        "action_items": json.loads(meeting.action_items or "[]"),
        "key_decisions": json.loads(meeting.key_decisions or "[]"),
        "open_questions": json.loads(meeting.open_questions or "[]"),
        "output_format": meeting.output_format or "translated",
        "created_at": meeting.created_at,
    }


@router.post("/{meeting_id}/format")
def change_meeting_format(
    meeting_id: int,
    req: FormatMeetingRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    meeting = (
        db.query(Meeting)
        .filter(
            Meeting.id == meeting_id,
            Meeting.user_id == current_user.id
        )
        .first()
    )

    if not meeting:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Meeting not found"
        )

    target_fmt = normalize_format(req.output_format)
    current_fmt = normalize_format(meeting.output_format)

    # 1. Parse existing formats cache
    formats_cache = {}
    try:
        if meeting.formats_data:
            formats_cache = json.loads(meeting.formats_data)
    except Exception:
        formats_cache = {}

    # Backfill current format in cache if not present
    if current_fmt not in formats_cache and meeting.summary:
        try:
            formats_cache[current_fmt] = {
                "title": meeting.title or "Meeting",
                "summary": meeting.summary or "",
                "action_items": json.loads(meeting.action_items or "[]"),
                "key_decisions": json.loads(meeting.key_decisions or "[]"),
                "open_questions": json.loads(meeting.open_questions or "[]"),
                "transcript": meeting.transcript or "",
            }
        except Exception:
            pass

    # If already on target format, return current meeting data
    if target_fmt == current_fmt and target_fmt in formats_cache:
        cached = formats_cache[target_fmt]
        return {
            "id": meeting.id,
            "title": cached["title"],
            "source": meeting.source,
            "language": meeting.language or "english",
            "transcript": cached.get("transcript", meeting.transcript),
            "summary": cached["summary"],
            "action_items": cached.get("action_items", []),
            "key_decisions": cached.get("key_decisions", []),
            "open_questions": cached.get("open_questions", []),
            "output_format": target_fmt,
            "created_at": meeting.created_at,
        }

    # 2. CHECK CACHE: If target format was already converted before, restore it instantly!
    # No re-calling the LLM, zero latency, zero information loss!
    if target_fmt in formats_cache:
        print(f"[CACHE HIT] Restoring saved [{target_fmt}] version for meeting [{meeting.id}] with zero re-conversion!")
        cached = formats_cache[target_fmt]
        meeting.title = cached["title"]
        meeting.summary = cached["summary"]
        meeting.action_items = json.dumps(cached.get("action_items", []))
        meeting.key_decisions = json.dumps(cached.get("key_decisions", []))
        meeting.open_questions = json.dumps(cached.get("open_questions", []))
        meeting.transcript = cached.get("transcript", meeting.transcript)
        meeting.output_format = target_fmt

        db.commit()
        db.refresh(meeting)

        from core.vector_store import build_vector_store
        if meeting.transcript:
            build_vector_store(meeting.transcript, str(meeting.id))

        return {
            "id": meeting.id,
            "title": meeting.title,
            "source": meeting.source,
            "language": meeting.language or "english",
            "transcript": meeting.transcript,
            "summary": meeting.summary,
            "action_items": json.loads(meeting.action_items or "[]"),
            "key_decisions": json.loads(meeting.key_decisions or "[]"),
            "open_questions": json.loads(meeting.open_questions or "[]"),
            "output_format": meeting.output_format,
            "created_at": meeting.created_at,
        }

    # 3. CACHE MISS: Convert for the first time without losing information
    try:
        from core.extractor import extract_meeting_insights, convert_meeting_insights, convert_transcript_format
        from core.vector_store import build_vector_store
        from core.transcriber import transliterate_transcript_to_roman

        canonical_transcript = meeting.original_transcript or meeting.transcript or ""

        # Determine if this meeting's audio is Hinglish
        is_hinglish_audio = (
            (meeting.language and meeting.language.lower() in ["hinglish", "hindi", "hindi_font", "devanagari", "romanized", "roman_transliteration"])
            or (meeting.output_format and meeting.output_format.lower() in ["romanized", "hinglish"])
            or bool(re.search(r"[\u0900-\u097F]", canonical_transcript))
        )

        if is_hinglish_audio:
            # The transcript must ALWAYS be in Roman transliteration for Hinglish audio.
            if re.search(r"[\u0900-\u097F]", canonical_transcript):
                canonical_transcript = transliterate_transcript_to_roman(canonical_transcript)
            target_transcript = canonical_transcript
        else:
            # English audio: convert transcript dialogue from canonical original
            if target_fmt == "translated":
                target_transcript = canonical_transcript
            else:
                target_transcript = convert_transcript_format(canonical_transcript, target_fmt)

        # Generate insights for target_fmt directly from canonical transcript so NO information is lost
        if len(canonical_transcript.strip()) > 100:
            print(f"Extracting full insights in [{target_fmt}] directly from canonical transcript for meeting [{meeting.id}]...")
            new_insights = extract_meeting_insights(canonical_transcript, output_format=target_fmt)
        else:
            current_insights = {
                "title": meeting.title or "Meeting",
                "summary": meeting.summary or "",
                "action_items": json.loads(meeting.action_items or "[]"),
                "key_decisions": json.loads(meeting.key_decisions or "[]"),
                "open_questions": json.loads(meeting.open_questions or "[]"),
            }
            new_insights = convert_meeting_insights(current_insights, target_fmt)

        # 4. Save newly generated format in formats_cache permanently
        formats_cache[target_fmt] = {
            "title": new_insights["title"],
            "summary": new_insights["summary"],
            "action_items": new_insights.get("action_items", []),
            "key_decisions": new_insights.get("key_decisions", []),
            "open_questions": new_insights.get("open_questions", []),
            "transcript": target_transcript,
        }

        meeting.title = new_insights["title"]
        meeting.summary = new_insights["summary"]
        meeting.action_items = json.dumps(new_insights.get("action_items", []))
        meeting.key_decisions = json.dumps(new_insights.get("key_decisions", []))
        meeting.open_questions = json.dumps(new_insights.get("open_questions", []))
        meeting.transcript = target_transcript
        meeting.original_transcript = canonical_transcript
        meeting.formats_data = json.dumps(formats_cache)
        meeting.output_format = target_fmt

        db.commit()
        db.refresh(meeting)

        # 5. Update vector store
        if meeting.transcript:
            build_vector_store(meeting.transcript, str(meeting.id))

        return {
            "id": meeting.id,
            "title": meeting.title,
            "source": meeting.source,
            "language": meeting.language or "english",
            "transcript": meeting.transcript,
            "summary": meeting.summary,
            "action_items": json.loads(meeting.action_items or "[]"),
            "key_decisions": json.loads(meeting.key_decisions or "[]"),
            "open_questions": json.loads(meeting.open_questions or "[]"),
            "output_format": meeting.output_format,
            "created_at": meeting.created_at,
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to change format: {str(e)}"
        )


@router.delete("/{meeting_id}")
def delete_meeting(
    meeting_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    meeting = (
        db.query(Meeting)
        .filter(
            Meeting.id == meeting_id,
            Meeting.user_id == current_user.id
        )
        .first()
    )

    if not meeting:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Meeting not found"
        )

    # Clean up ChromaDB directory on disk if it exists
    vector_dir = os.path.join("vector_db", str(meeting_id))
    if os.path.exists(vector_dir):
        shutil.rmtree(vector_dir, ignore_errors=True)

    db.delete(meeting)
    db.commit()

    return {"message": "Meeting deleted successfully"}


@router.patch("/{meeting_id}/rename")
def rename_meeting(
    meeting_id: int,
    data: RenameMeetingRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    meeting = (
        db.query(Meeting)
        .filter(
            Meeting.id == meeting_id,
            Meeting.user_id == current_user.id
        )
        .first()
    )

    if not meeting:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Meeting not found"
        )

    meeting.title = data.title.strip()
    db.commit()

    return {
        "message": "Meeting renamed successfully"
    }