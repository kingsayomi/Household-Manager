import os
import secrets
import datetime
import logging
from typing import Optional, Dict
from zoneinfo import ZoneInfo

from fastapi import FastAPI, APIRouter, Depends, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.orm import Session

import ai_agent
import voice
from db import (init_db, get_db, utcnow, Household, User, ShoppingItem, Task, Reminder,
                Event, PantryItem, Budget, ChatMessage)
from auth import (hash_password, verify_password, DUMMY_HASH, start_session, end_session,
                  current_user, throttle, clear_throttle, client_ip, EMAIL_RE)

log = logging.getLogger("household")
AI_DAILY_LIMIT = int(os.environ.get("AI_DAILY_LIMIT", "60"))

init_db()
app = FastAPI(title="Household Manager")
router = APIRouter(prefix="/api")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Cache-Control", "no-store" if request.url.path.startswith("/api") else "no-cache")
    return response


# ---------- Request shapes ----------
class SignupIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    email: str = Field(max_length=200)
    password: str = Field(min_length=8, max_length=200)
    household_name: str = Field(default="", max_length=100)
    invite_code: str = Field(default="", max_length=16)
    timezone: str = Field(default="UTC", max_length=64)


class LoginIn(BaseModel):
    email: str = Field(max_length=200)
    password: str = Field(max_length=200)


class HouseholdIn(BaseModel):
    name: Optional[str] = Field(default=None, max_length=100)
    timezone: Optional[str] = Field(default=None, max_length=64)


class NameIn(BaseModel):
    name: str = Field(max_length=200)


class TaskIn(BaseModel):
    title: str = Field(max_length=200)
    due_at: str = Field(default="", max_length=16)


class ReminderIn(BaseModel):
    text: str = Field(max_length=200)
    remind_at: str = Field(max_length=16)


class EventIn(BaseModel):
    title: str = Field(max_length=200)
    starts_at: str = Field(max_length=16)


class PantryIn(BaseModel):
    name: str = Field(max_length=200)
    quantity: str = Field(default="", max_length=50)


class BudgetIn(BaseModel):
    total: float = Field(ge=0)
    period: str = Field(max_length=32)
    categories: Dict[str, float]


class CategoryIn(BaseModel):
    category: str = Field(max_length=100)
    amount: float = Field(ge=0)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=1000)


# ---------- Validation helpers ----------
def clean(value, what="This field"):
    value = (value or "").strip()
    if not value:
        raise HTTPException(422, f"{what} can't be empty.")
    return value


def check_dt(value, required=True):
    value = (value or "").strip()
    if not value:
        if required:
            raise HTTPException(422, "A date and time is required.")
        return ""
    try:
        datetime.datetime.strptime(value, "%Y-%m-%d %H:%M")
    except ValueError:
        raise HTTPException(422, "Date/time must look like 2026-10-05 14:30.")
    return value


def valid_tz(name):
    try:
        ZoneInfo(name)
        return True
    except Exception:
        return False


def reset_flags(obj):
    if hasattr(obj, "day_before_announced"):
        obj.day_before_announced = False
        obj.hour_before_announced = False


# ---------- Accounts and households ----------
def me_payload(db: Session, user: User):
    household = db.get(Household, user.household_id)
    members = db.scalars(select(User.name).where(User.household_id == household.id).order_by(User.id)).all()
    return {
        "user": {"name": user.name, "email": user.email},
        "household": {"name": household.name, "timezone": household.timezone,
                      "invite_code": household.invite_code},
        "members": list(members),
    }


def make_invite_code(db: Session):
    while True:
        code = secrets.token_hex(4).upper()
        if not db.scalar(select(Household).where(Household.invite_code == code)):
            return code


@router.post("/auth/signup")
def signup(data: SignupIn, request: Request, response: Response, db: Session = Depends(get_db)):
    throttle(f"signup:{client_ip(request)}", limit=10)
    email = data.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(422, "Please enter a valid email address.")
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "An account with this email already exists.")
    name = clean(data.name, "Name")

    code = data.invite_code.strip().upper()
    if code:
        household = db.scalar(select(Household).where(Household.invite_code == code))
        if not household:
            raise HTTPException(404, "That invite code wasn't found.")
    else:
        household = Household(
            name=data.household_name.strip() or f"{name}'s household",
            timezone=data.timezone if valid_tz(data.timezone) else "UTC",
            invite_code=make_invite_code(db),
        )
        db.add(household)
        db.flush()

    user = User(email=email, name=name, password_hash=hash_password(data.password),
                household_id=household.id)
    db.add(user)
    db.commit()
    start_session(db, user, response)
    return me_payload(db, user)


@router.post("/auth/login")
def login(data: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    email = data.email.strip().lower()
    key = f"login:{email}"
    throttle(key)
    user = db.scalar(select(User).where(User.email == email))
    ok = verify_password(data.password, user.password_hash if user else DUMMY_HASH)
    if not user or not ok:
        raise HTTPException(401, "Wrong email or password.")
    clear_throttle(key)
    start_session(db, user, response)
    return me_payload(db, user)


@router.post("/auth/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    end_session(db, request, response)
    return {"status": "signed_out"}


@router.get("/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return me_payload(db, user)


@router.put("/household")
def update_household(data: HouseholdIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    household = db.get(Household, user.household_id)
    if data.name is not None:
        household.name = clean(data.name, "Household name")
    if data.timezone is not None:
        if not valid_tz(data.timezone.strip()):
            raise HTTPException(422, "Unknown timezone. Try something like Africa/Lagos.")
        household.timezone = data.timezone.strip()
    db.commit()
    return me_payload(db, user)


# ---------- Generic household-scoped CRUD ----------
def crud(path, Model, InModel, fields, prep, dedupe=None):
    def to_dict(o):
        return {"id": o.id, **{f: getattr(o, f) for f in fields}}

    def owned(db, user, item_id):
        obj = db.get(Model, item_id)
        if not obj or obj.household_id != user.household_id:
            raise HTTPException(404, "Not found.")
        return obj

    @router.get(f"/{path}", name=f"list_{path}")
    def list_items(user: User = Depends(current_user), db: Session = Depends(get_db)):
        rows = db.scalars(select(Model).where(Model.household_id == user.household_id)
                          .order_by(Model.id)).all()
        return [to_dict(r) for r in rows]

    @router.post(f"/{path}", name=f"add_{path}")
    def add_item(data: InModel, user: User = Depends(current_user), db: Session = Depends(get_db)):
        values = prep(data)
        if dedupe:
            existing = db.scalar(select(Model).where(
                Model.household_id == user.household_id,
                func.lower(getattr(Model, dedupe)) == values[dedupe].lower()))
            if existing:
                for k, v in values.items():
                    setattr(existing, k, v)
                db.commit()
                return to_dict(existing)
        obj = Model(household_id=user.household_id, **values)
        db.add(obj)
        db.commit()
        return to_dict(obj)

    @router.put(f"/{path}/{{item_id}}", name=f"edit_{path}")
    def edit_item(item_id: int, data: InModel, user: User = Depends(current_user),
                  db: Session = Depends(get_db)):
        obj = owned(db, user, item_id)
        for k, v in prep(data).items():
            setattr(obj, k, v)
        reset_flags(obj)
        db.commit()
        return to_dict(obj)

    @router.delete(f"/{path}/{{item_id}}", name=f"remove_{path}")
    def remove_item(item_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
        db.delete(owned(db, user, item_id))
        db.commit()
        return {"status": "removed"}


crud("shopping", ShoppingItem, NameIn, ("name",),
     lambda d: {"name": clean(d.name, "Name")}, dedupe="name")
crud("tasks", Task, TaskIn, ("title", "due_at"),
     lambda d: {"title": clean(d.title, "Title"), "due_at": check_dt(d.due_at, required=False)})
crud("reminders", Reminder, ReminderIn, ("text", "remind_at"),
     lambda d: {"text": clean(d.text, "Text"), "remind_at": check_dt(d.remind_at)})
crud("events", Event, EventIn, ("title", "starts_at"),
     lambda d: {"title": clean(d.title, "Title"), "starts_at": check_dt(d.starts_at)})
crud("pantry", PantryItem, PantryIn, ("name", "quantity"),
     lambda d: {"name": clean(d.name, "Name"), "quantity": (d.quantity or "").strip()}, dedupe="name")


# ---------- Budget ----------
@router.get("/budget")
def get_budget(user: User = Depends(current_user), db: Session = Depends(get_db)):
    b = db.get(Budget, user.household_id)
    if not b:
        return {}
    return {"total": b.total, "period": b.period, "categories": b.categories or {}}


@router.post("/budget")
def set_budget(data: BudgetIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if any(v < 0 for v in data.categories.values()):
        raise HTTPException(422, "Category amounts can't be negative.")
    b = db.get(Budget, user.household_id) or Budget(household_id=user.household_id)
    b.total = data.total
    b.period = clean(data.period, "Period")
    b.categories = {clean(k, "Category"): v for k, v in data.categories.items()}
    db.add(b)
    db.commit()
    return {"total": b.total, "period": b.period, "categories": b.categories}


@router.put("/budget/category")
def adjust_category(data: CategoryIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    b = db.get(Budget, user.household_id)
    if not b:
        raise HTTPException(404, "No budget has been set yet.")
    cats = dict(b.categories or {})
    cats[clean(data.category, "Category")] = data.amount
    b.categories = cats
    db.commit()
    return {"total": b.total, "period": b.period, "categories": cats}


@router.delete("/budget/category/{category}")
def delete_category(category: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    b = db.get(Budget, user.household_id)
    if not b or category not in (b.categories or {}):
        raise HTTPException(404, "Not found.")
    cats = dict(b.categories)
    del cats[category]
    b.categories = cats
    db.commit()
    return {"status": "removed"}


# ---------- Alerts (day-before / hour-before; exact-time alarm runs in the browser) ----------
def parse_dt(text):
    try:
        return datetime.datetime.strptime(text, "%Y-%m-%d %H:%M")
    except Exception:
        return None


@router.get("/alerts")
def get_alerts(user: User = Depends(current_user), db: Session = Depends(get_db)):
    household = db.get(Household, user.household_id)
    now = ai_agent.household_now(household).replace(tzinfo=None)
    sources = (
        (Reminder, "remind_at", lambda r: f"Reminder: {r.text}"),
        (Event, "starts_at", lambda e: f"You have {e.title}"),
        (Task, "due_at", lambda t: f"Task due: {t.title}"),
    )
    alerts, changed = [], False
    for Model, field, label in sources:
        for item in db.scalars(select(Model).where(Model.household_id == household.id)).all():
            target = parse_dt(getattr(item, field))
            if not target:
                continue
            seconds = (target - now).total_seconds()
            if 23 * 3600 <= seconds <= 25 * 3600 and not item.day_before_announced:
                alerts.append({"message": f"{label(item)} tomorrow"})
                item.day_before_announced = True
                changed = True
            if 50 * 60 <= seconds <= 70 * 60 and not item.hour_before_announced:
                when = target.strftime("%I:%M %p").lstrip("0")
                alerts.append({"message": f"{label(item)} soon, at {when}"})
                item.hour_before_announced = True
                changed = True
    if changed:
        db.commit()
    return {"alerts": alerts}


# ---------- Chat ----------
@router.post("/chat")
def chat(data: ChatIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    day_start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    used = db.scalar(select(func.count()).select_from(ChatMessage).where(
        ChatMessage.user_id == user.id, ChatMessage.role == "user",
        ChatMessage.created_at >= day_start))
    if used >= AI_DAILY_LIMIT:
        return {"reply": f"You've reached today's limit of {AI_DAILY_LIMIT} messages. "
                         f"It resets at midnight UTC."}
    try:
        reply = ai_agent.chat(db, user, data.message.strip())
    except Exception:
        db.rollback()
        log.exception("AI chat failed")
        reply = "Sorry, I couldn't reach the AI just now. Please try again in a moment."
    return {"reply": reply}


@router.get("/chat/history")
def chat_history(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(ChatMessage).where(ChatMessage.user_id == user.id)
                      .order_by(ChatMessage.id.desc()).limit(40)).all()
    return [{"role": m.role, "content": m.content} for m in reversed(rows)]


@router.delete("/chat/history")
def clear_history(user: User = Depends(current_user), db: Session = Depends(get_db)):
    for m in db.scalars(select(ChatMessage).where(ChatMessage.user_id == user.id)).all():
        db.delete(m)
    db.commit()
    return {"status": "cleared"}


app.include_router(router)
app.include_router(voice.router)

# Must be LAST: serves frontend/index.html, style.css, app.js as the website itself.
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
