import os
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database.database import get_db, SessionLocal
from models.models import User, Meeting, ChatHistory
from auth.auth import get_current_user
from core.rag_engine import load_rag_chain, ask_questions
from core.vector_store import BASE_CHROMA_DIR, build_vector_store

router = APIRouter(prefix="/meetings", tags=["Chat"])


class QuestionRequest(BaseModel):
    question: str
    output_format: str = "translated"


def get_or_create_rag_chain(meeting_id: int, transcript: str, output_format: str = "translated"):
    persist_dir = os.path.join(BASE_CHROMA_DIR, str(meeting_id))
    if (not os.path.exists(persist_dir) or not os.listdir(persist_dir)) and transcript:
        build_vector_store(transcript, str(meeting_id))
    return load_rag_chain(str(meeting_id), output_format=output_format)


@router.post("/{meeting_id}/ask")
def ask_meeting(
    meeting_id: int,
    req: QuestionRequest,
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

    try:
        rag_chain = get_or_create_rag_chain(meeting.id, meeting.transcript or "", output_format=req.output_format)
        answer = ask_questions(
            rag_chain,
            req.question
        )
    except Exception as e:
        try:
            fallback_chain = load_rag_chain(str(meeting_id), model="openai/gpt-oss-20b", output_format=req.output_format)
            answer = ask_questions(fallback_chain, req.question)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to generate answer: {str(e)}"
            )

    chat = ChatHistory(
        meeting_id=meeting.id,
        user_id=current_user.id,
        question=req.question,
        answer=answer
    )

    db.add(chat)
    db.commit()
    db.refresh(chat)

    return {
        "id": chat.id,
        "meeting_id": meeting.id,
        "question": chat.question,
        "answer": chat.answer,
        "created_at": chat.created_at
    }


@router.post("/{meeting_id}/ask/stream")
def ask_meeting_stream(
    meeting_id: int,
    req: QuestionRequest,
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

    rag_chain = get_or_create_rag_chain(meeting.id, meeting.transcript or "", output_format=req.output_format)

    def generate():
        tokens = []
        try:
            for chunk in rag_chain.stream(req.question):
                tokens.append(chunk)
                yield chunk
        except Exception as e:
            error_str = str(e)
            print(f"Streaming error on primary model: {error_str}")
            try:
                fallback_chain = load_rag_chain(str(meeting_id), model="openai/gpt-oss-20b", output_format=req.output_format)
                for chunk in fallback_chain.stream(req.question):
                    tokens.append(chunk)
                    yield chunk
            except Exception as fb_err:
                print(f"Fallback streaming error: {fb_err}")
                if "429" in error_str or "rate_limit" in error_str.lower():
                    friendly_msg = "\n\n⚠️ Groq rate limit reached on this model. Please wait a few seconds and try again."
                else:
                    friendly_msg = f"\n\n⚠️ Error generating answer: {error_str}"
                tokens.append(friendly_msg)
                yield friendly_msg
        finally:
            full_answer = "".join(tokens).strip()
            if full_answer:
                save_db = SessionLocal()
                try:
                    chat = ChatHistory(
                        meeting_id=meeting.id,
                        user_id=current_user.id,
                        question=req.question,
                        answer=full_answer
                    )
                    save_db.add(chat)
                    save_db.commit()
                finally:
                    save_db.close()

    return StreamingResponse(
        generate(),
        media_type="text/plain",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )


@router.get("/{meeting_id}/chats")
def get_meeting_chats(
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

    chats = (
        db.query(ChatHistory)
        .filter(ChatHistory.meeting_id == meeting_id)
        .order_by(ChatHistory.created_at.asc())
        .all()
    )

    return [
        {
            "id": chat.id,
            "question": chat.question,
            "answer": chat.answer,
            "created_at": chat.created_at
        }
        for chat in chats
    ]