"""Voice input: the browser records a short voice note, this turns it into text."""
import os
import re
import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, func, ForeignKey, DateTime
from sqlalchemy.orm import Session, Mapped, mapped_column
from starlette.concurrency import run_in_threadpool

import ai_agent
from db import Base, get_db, utcnow, User
from auth import current_user

log = logging.getLogger("household")
router = APIRouter(prefix="/api")

VOICE_DAILY_LIMIT = int(os.environ.get("VOICE_DAILY_LIMIT", "40"))
VOICE_LANGUAGE = os.environ.get("VOICE_LANGUAGE", "en")  # set to "" to auto-detect
MAX_AUDIO_BYTES = 5 * 1024 * 1024
MIN_AUDIO_BYTES = 1000
SPEECH_MODEL = "whisper-large-v3-turbo"

AUDIO_EXT = {
    "audio/webm": "webm",
    "audio/mp4": "mp4",
    "audio/x-m4a": "m4a",
    "audio/m4a": "m4a",
    "audio/ogg": "ogg",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/mpeg": "mp3",
}

# Speech models often invent these phrases when they hear silence or noise
INVENTED = {"thank you", "thanks", "thank you for watching", "thanks for watching",
            "you", "bye", "bye bye", "thank you very much", "thanks for listening"}


class VoiceUse(Base):
    __tablename__ = "voice_uses"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[object] = mapped_column(DateTime, default=utcnow)


def _field(obj, name, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def clean_transcript(result):
    """Return the spoken text, or "" if the audio was silent or the text looks invented."""
    text = (_field(result, "text", "") or "").strip()
    segments = _field(result, "segments", None) or []

    weak = False
    if segments:
        kept = []
        for seg in segments:
            no_speech = _field(seg, "no_speech_prob", 0) or 0
            logprob = _field(seg, "avg_logprob", 0) or 0
            if no_speech > 0.6 and logprob < -1.0:
                continue  # the model itself says this part is silence
            kept.append(seg)
        text = " ".join((_field(sg, "text", "") or "").strip() for sg in kept).strip()
        weak = bool(kept) and all(
            (_field(sg, "no_speech_prob", 0) or 0) > 0.3 or (_field(sg, "avg_logprob", 0) or 0) < -0.7
            for sg in kept)

    normalized = re.sub(r"[^a-z ]", "", text.lower()).strip()
    if normalized in INVENTED and (weak or not segments):
        return ""
    return text


def do_transcribe(db: Session, user: User, audio: bytes, content_type: str):
    ext = AUDIO_EXT.get(content_type)
    if not ext:
        raise HTTPException(415, "That audio format isn't supported.")
    if len(audio) < MIN_AUDIO_BYTES:
        raise HTTPException(422, "That recording was too short. Please try again.")
    if len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "That recording is too long. Keep it under about a minute.")

    day_start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    used = db.scalar(select(func.count()).select_from(VoiceUse).where(
        VoiceUse.user_id == user.id, VoiceUse.created_at >= day_start))
    if used >= VOICE_DAILY_LIMIT:
        raise HTTPException(429, f"You've reached today's limit of {VOICE_DAILY_LIMIT} voice messages.")

    options = dict(file=(f"voice.{ext}", audio), model=SPEECH_MODEL,
                   response_format="verbose_json", temperature=0)
    if VOICE_LANGUAGE:
        options["language"] = VOICE_LANGUAGE
    try:
        result = ai_agent.client.audio.transcriptions.create(**options)
    except Exception:
        log.exception("Transcription failed")
        raise HTTPException(502, "I couldn't turn that into text just now. Please try again.")

    db.add(VoiceUse(user_id=user.id))
    db.commit()
    return {"text": clean_transcript(result)}


@router.post("/transcribe")
async def transcribe(request: Request, user: User = Depends(current_user),
                     db: Session = Depends(get_db)):
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "That recording is too long. Keep it under about a minute.")
    audio = await request.body()
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    return await run_in_threadpool(do_transcribe, db, user, audio, content_type)
