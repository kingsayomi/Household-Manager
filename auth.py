import os
import re
import time
import hmac
import base64
import hashlib
import secrets
import datetime
from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session
from db import get_db, User, AuthSession, utcnow

COOKIE = "hm_session"
SESSION_DAYS = 30
# Set COOKIE_SECURE=1 once the site is served over https
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "0") == "1"
ITERATIONS = 600_000
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        ITERATIONS,
        base64.b64encode(salt).decode(),
        base64.b64encode(digest).decode(),
    )


def verify_password(password, stored):
    try:
        _, iterations, salt_b64, hash_b64 = stored.split("$")
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iterations))
        return hmac.compare_digest(digest, expected)
    except Exception:
        return False


# Used so a wrong email takes as long to reject as a wrong password
DUMMY_HASH = hash_password("not-a-real-password")


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def start_session(db: Session, user: User, response: Response):
    token = secrets.token_urlsafe(32)
    db.add(AuthSession(
        token_hash=hash_token(token),
        user_id=user.id,
        expires_at=utcnow() + datetime.timedelta(days=SESSION_DAYS),
    ))
    db.commit()
    response.set_cookie(
        COOKIE, token,
        max_age=SESSION_DAYS * 86400,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
        path="/",
    )


def end_session(db: Session, request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        row = db.get(AuthSession, hash_token(token))
        if row:
            db.delete(row)
            db.commit()
    response.delete_cookie(COOKIE, path="/")


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "Not signed in")
    session = db.get(AuthSession, hash_token(token))
    if not session or session.expires_at < utcnow():
        raise HTTPException(401, "Not signed in")
    user = db.get(User, session.user_id)
    if not user:
        raise HTTPException(401, "Not signed in")
    return user


# Simple in-memory throttle against password guessing
_attempts = {}


def throttle(key, limit=8, window=900):
    now = time.time()
    hits = [t for t in _attempts.get(key, []) if now - t < window]
    if len(hits) >= limit:
        raise HTTPException(429, "Too many attempts. Please wait a few minutes and try again.")
    hits.append(now)
    _attempts[key] = hits


def clear_throttle(key):
    _attempts.pop(key, None)


def client_ip(request: Request):
    return request.client.host if request.client else "unknown"