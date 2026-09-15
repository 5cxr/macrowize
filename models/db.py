"""SQLAlchemy models, session factory, and the handful of queries Phase 1 needs.

Single-user app: `UserProfile` holds exactly one row, pinned to id=1.

Timestamps are stored as naive *local* time, not UTC. A daily tally has to line up
with the user's own calendar day -- under UTC an 00:30 IST snack would be counted
against the previous day.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime
from typing import Any, Iterator

from sqlalchemy import JSON, DateTime, Float, Integer, String, create_engine, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from config import DATABASE_URL

PROFILE_ID = 1


class Base(DeclarativeBase):
    pass


class UserProfile(Base):
    """Body stats plus the cached targets derived from them."""

    __tablename__ = "user_profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=PROFILE_ID)
    height_cm: Mapped[float] = mapped_column(Float)
    weight_kg: Mapped[float] = mapped_column(Float)
    age: Mapped[int] = mapped_column(Integer)
    sex: Mapped[str] = mapped_column(String)
    activity_level: Mapped[str] = mapped_column(String)
    goal_type: Mapped[str] = mapped_column(String)
    target_kcal: Mapped[int] = mapped_column(Integer)
    target_protein_min_g: Mapped[int] = mapped_column(Integer)
    target_protein_max_g: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class MealLog(Base):
    """One logged meal. Totals are denormalized so daily sums stay a single query."""

    __tablename__ = "meal_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    raw_text: Mapped[str] = mapped_column(String)
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    total_kcal: Mapped[float] = mapped_column(Float)
    total_protein_g: Mapped[float] = mapped_column(Float)


class ChatMessage(Base):
    """One turn of the conversation, so a page reload doesn't wipe the transcript."""

    __tablename__ = "chat_message"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    role: Mapped[str] = mapped_column(String)  # "user" | "assistant"
    content: Mapped[str] = mapped_column(String)


class FoodCache(Base):
    """Per-100g macros for a normalized food name, cached from USDA/CSV lookups."""

    __tablename__ = "food_cache"

    food_name: Mapped[str] = mapped_column(String, primary_key=True)
    kcal_per_100g: Mapped[float] = mapped_column(Float)
    protein_g_per_100g: Mapped[float] = mapped_column(Float)
    carbs_g_per_100g: Mapped[float] = mapped_column(Float)
    fat_g_per_100g: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)


def init_db() -> None:
    """Create any missing tables. Safe to call on every startup."""
    Base.metadata.create_all(engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Yield a session that commits on success and rolls back on error."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_profile(session: Session) -> UserProfile | None:
    """Return the single profile row, or None if onboarding hasn't happened."""
    return session.get(UserProfile, PROFILE_ID)


def save_profile(
    session: Session,
    *,
    height_cm: float,
    weight_kg: float,
    age: int,
    sex: str,
    activity_level: str,
    goal_type: str,
    target_kcal: int,
    target_protein_min_g: int,
    target_protein_max_g: int,
) -> UserProfile:
    """Insert or overwrite the single profile row and return it."""
    profile = get_profile(session)
    if profile is None:
        profile = UserProfile(id=PROFILE_ID)
        session.add(profile)

    profile.height_cm = height_cm
    profile.weight_kg = weight_kg
    profile.age = age
    profile.sex = sex
    profile.activity_level = activity_level
    profile.goal_type = goal_type
    profile.target_kcal = target_kcal
    profile.target_protein_min_g = target_protein_min_g
    profile.target_protein_max_g = target_protein_max_g
    profile.updated_at = datetime.now()

    session.flush()
    return profile


def save_meal(
    session: Session,
    *,
    raw_text: str,
    items: list[dict[str, Any]],
    timestamp: datetime | None = None,
) -> MealLog:
    """Persist a meal, deriving its denormalized totals from the item list."""
    meal = MealLog(
        timestamp=timestamp or datetime.now(),
        raw_text=raw_text,
        items=items,
        total_kcal=sum(item["kcal"] for item in items),
        total_protein_g=sum(item["protein_g"] for item in items),
    )
    session.add(meal)
    session.flush()
    return meal


def get_daily_tally(session: Session, day: date | None = None) -> tuple[float, float]:
    """Return (total_kcal, total_protein_g) logged on `day`, defaulting to today."""
    day = day or date.today()
    row = session.execute(
        select(
            func.coalesce(func.sum(MealLog.total_kcal), 0.0),
            func.coalesce(func.sum(MealLog.total_protein_g), 0.0),
        ).where(func.date(MealLog.timestamp) == day.isoformat())
    ).one()
    return float(row[0]), float(row[1])


def save_chat_message(session: Session, role: str, content: str) -> ChatMessage:
    """Append one turn to the stored transcript."""
    message = ChatMessage(role=role, content=content)
    session.add(message)
    session.flush()
    return message


def get_chat_history(session: Session, limit: int = 200) -> list[tuple[str, str]]:
    """Return the last `limit` turns as (role, content), oldest first.

    Plain tuples rather than ORM rows -- callers read these after the session has
    closed, and a detached row raises on attribute access.
    """
    recent = session.execute(
        select(ChatMessage.role, ChatMessage.content)
        .order_by(ChatMessage.id.desc())
        .limit(limit)
    ).all()
    return [(role, content) for role, content in reversed(recent)]


def clear_chat_history(session: Session) -> int:
    """Delete the stored transcript. Returns how many turns were removed."""
    count = session.query(ChatMessage).delete()
    return int(count)


def get_meals_for_day(session: Session, day: date | None = None) -> list[MealLog]:
    """Return every meal logged on `day`, oldest first."""
    day = day or date.today()
    return list(
        session.scalars(
            select(MealLog)
            .where(func.date(MealLog.timestamp) == day.isoformat())
            .order_by(MealLog.timestamp)
        )
    )
