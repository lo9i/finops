"""Natural-language chat endpoint backed by the Claude API tool runner."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..ai.chat import is_enabled, run_chat

router = APIRouter(prefix="/api", tags=["chat"])


class ChatMessage(BaseModel):
    role: str = Field(..., description="'user' or 'assistant'")
    content: str


class ChatBody(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    history: list[ChatMessage] = Field(default_factory=list)


@router.get("/chat/status")
def chat_status():
    return {"enabled": is_enabled()}


@router.post("/chat")
def chat(body: ChatBody):
    if not is_enabled():
        raise HTTPException(
            status_code=503,
            detail=(
                "ANTHROPIC_API_KEY is not set on the server. "
                "Export ANTHROPIC_API_KEY=... and restart to enable the assistant."
            ),
        )
    try:
        history = [m.model_dump() for m in body.history]
        return run_chat(body.message, history)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"chat failed: {exc}") from exc
