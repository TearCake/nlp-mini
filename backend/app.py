import sys

if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database.database import Base, engine
from models import models

from routes.auth1 import router as auth_router
from routes.meeting import router as meetings_router
from routes.chat import router as chat_router


Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="AI Video Meeting Assistant",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {
        "message": "AI Video Meeting Assistant Backend Running"
    }


app.include_router(auth_router)
app.include_router(meetings_router)
app.include_router(chat_router)