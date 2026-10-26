import json
import os
import shutil
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.database import get_db
from models.models import User, Meeting
from auth.auth import get_current_user
from main import run_pipeline


router = APIRouter(prefix="/meetings", tags=["Meetings"])


class RenameMeetingRequest(BaseModel):
    title: str


class MeetingRequest(BaseModel):
    source: str
    language: str = "english"
    output_format: str = "translated"


class FormatMeetingRequest(BaseModel):
    output_format: str


@router.post("/process")
def process_meeting(
    req: MeetingRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    # Step 1: create initial meeting record
    new_meeting = Meeting(
        user_id=current_user.id,
        source=req.source,
        output_format=req.output_format
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
            output_format=req.output_format,
            meeting_id=meeting_id
        )
    except Exception as e:
        db.delete(new_meeting)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process meeting: {str(e)}"
        )

    # Step 3: save result in DB
    new_meeting.title = result["title"]
    new_meeting.transcript = result["transcript"]
    new_meeting.summary = result["summary"]
    new_meeting.action_items = json.dumps(result["action_items"])
    new_meeting.key_decisions = json.dumps(result["key_decisions"])
    new_meeting.open_questions = json.dumps(result["open_questions"])
    new_meeting.vector_path = f"vector_db/{meeting_id}"
    new_meeting.output_format = result.get("output_format", req.output_format)

    db.commit()
    db.refresh(new_meeting)

    return {
        "meeting_id": new_meeting.id,
        "title": new_meeting.title,
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

    target_fmt = req.output_format.lower().strip()
    current_fmt = (meeting.output_format or "translated").lower().strip()

    if target_fmt == current_fmt:
        return {
            "id": meeting.id,
            "title": meeting.title,
            "source": meeting.source,
            "transcript": meeting.transcript,
            "summary": meeting.summary,
            "action_items": json.loads(meeting.action_items or "[]"),
            "key_decisions": json.loads(meeting.key_decisions or "[]"),
            "open_questions": json.loads(meeting.open_questions or "[]"),
            "output_format": meeting.output_format or "translated",
            "created_at": meeting.created_at,
        }

    try:
        from core.extractor import convert_meeting_insights, convert_transcript_format
        from core.vector_store import build_vector_store

        # 1. Convert transcript dialogue while preserving timestamps and speakers
        converted_transcript = convert_transcript_format(meeting.transcript or "", target_fmt)

        # 2. Convert meeting insights
        current_insights = {
            "title": meeting.title or "Meeting",
            "summary": meeting.summary or "",
            "action_items": json.loads(meeting.action_items or "[]"),
            "key_decisions": json.loads(meeting.key_decisions or "[]"),
            "open_questions": json.loads(meeting.open_questions or "[]"),
        }
        converted_insights = convert_meeting_insights(current_insights, target_fmt)

        meeting.title = converted_insights["title"]
        meeting.summary = converted_insights["summary"]
        meeting.action_items = json.dumps(converted_insights["action_items"])
        meeting.key_decisions = json.dumps(converted_insights["key_decisions"])
        meeting.open_questions = json.dumps(converted_insights["open_questions"])
        meeting.transcript = converted_transcript
        meeting.output_format = target_fmt

        db.commit()
        db.refresh(meeting)

        # 3. Update vector store
        if meeting.transcript:
            build_vector_store(meeting.transcript, str(meeting.id))

        return {
            "id": meeting.id,
            "title": meeting.title,
            "source": meeting.source,
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