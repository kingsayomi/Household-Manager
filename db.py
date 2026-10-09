import os
import datetime
from dotenv import load_dotenv
from sqlalchemy import create_engine, String, Boolean, DateTime, Text, JSON, Float, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///household.db")
# Accept the usual Postgres URL styles from hosts like Neon or Render
for _prefix in ("postgres://", "postgresql://"):
    if DATABASE_URL.startswith(_prefix):
        DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len(_prefix):]
        break

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class AlertFlags:
    day_before_announced: Mapped[bool] = mapped_column(Boolean, default=False)
    hour_before_announced: Mapped[bool] = mapped_column(Boolean, default=False)


class Household(Base):
    __tablename__ = "households"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    invite_code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(60))
    password_hash: Mapped[str] = mapped_column(String(300))
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id"), index=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime.datetime] = mapped_column(DateTime)


class ShoppingItem(Base):
    __tablename__ = "shopping_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))


class Task(AlertFlags, Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    due_at: Mapped[str] = mapped_column(String(16), default="")


class Reminder(AlertFlags, Base):
    __tablename__ = "reminders"
    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id"), index=True)
    text: Mapped[str] = mapped_column(String(200))
    remind_at: Mapped[str] = mapped_column(String(16))


class Event(AlertFlags, Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    starts_at: Mapped[str] = mapped_column(String(16))


class PantryItem(Base):
    __tablename__ = "pantry_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[str] = mapped_column(String(50), default="")


class Budget(Base):
    __tablename__ = "budgets"
    household_id: Mapped[int] = mapped_column(ForeignKey("households.id"), primary_key=True)
    total: Mapped[float] = mapped_column(Float, default=0)
    period: Mapped[str] = mapped_column(String(32), default="month")
    categories: Mapped[dict] = mapped_column(JSON, default=dict)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)


def init_db():
    Base.metadata.create_all(engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()