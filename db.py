"""SQLAlchemy models and every query the app makes.

Single user, so UserProfile is one row pinned to id=1.

Timestamps are naive *local* time, not UTC: a daily tally has to line up with the
user's own calendar day, and under UTC a 00:30 snack would land on the day before.

Query helpers return plain dicts, never ORM rows -- callers read them after the
session has closed, and a detached row raises on attribute access.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from dotenv import load_dotenv
from sqlalchemy import JSON, DateTime, Float, Integer, String, create_engine, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

load_dotenv(Path(__file__).resolve().parent / ".env")

DB_PATH = os.getenv("MACROWIZE_DB", str(Path(__file__).resolve().parent / "macrowize.db"))
PROFILE_ID = 1


class Base(DeclarativeBase):
    pass


class UserProfile(Base):
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
    """Totals are denormalized so a daily sum stays one query."""

    __tablename__ = "meal_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    raw_text: Mapped[str] = mapped_column(String)
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    total_kcal: Mapped[float] = mapped_column(Float)
    total_protein_g: Mapped[float] = mapped_column(Float)


class FoodCache(Base):
    """USDA results, kept so the same food doesn't re-hit the API every meal."""

    __tablename__ = "food_cache"

    query: Mapped[str] = mapped_column(String, primary_key=True)
    description: Mapped[str] = mapped_column(String)
    kcal: Mapped[float] = mapped_column(Float)
    protein_g: Mapped[float] = mapped_column(Float)
    carbs_g: Mapped[float] = mapped_column(Float)
    fat_g: Mapped[float] = mapped_column(Float)


class ChatMessage(Base):
    __tablename__ = "chat_message"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    role: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(String)


engine = create_engine(f"sqlite:///{DB_PATH}")
SessionLocal = sessionmaker(bind=engine)


def init_db() -> None:
    """Create any missing tables. Safe on every startup."""
    Base.metadata.create_all(engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Commit on success, roll back on error, always close."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_profile(session: Session) -> dict[str, Any] | None:
    """The profile as a plain dict, or None before onboarding."""
    profile = session.get(UserProfile, PROFILE_ID)
    if profile is None:
        return None
    return {
        "height_cm": profile.height_cm,
        "weight_kg": profile.weight_kg,
        "age": profile.age,
        "sex": profile.sex,
        "activity_level": profile.activity_level,
        "goal_type": profile.goal_type,
        "target_kcal": profile.target_kcal,
        "target_protein_min_g": profile.target_protein_min_g,
        "target_protein_max_g": profile.target_protein_max_g,
    }


def save_profile(session: Session, **fields) -> None:
    """Insert or overwrite the single profile row."""
    profile = session.get(UserProfile, PROFILE_ID) or UserProfile(id=PROFILE_ID)
    for name, value in fields.items():
        setattr(profile, name, value)
    profile.updated_at = datetime.now()
    session.add(profile)
    session.flush()


def save_meal(
    session: Session, raw_text: str, items: list[dict], timestamp: datetime | None = None
) -> MealLog:
    """Persist a meal, deriving its totals from the items."""
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


def delete_meal(session: Session, meal_id: int) -> bool:
    """Delete one meal. False if it was already gone."""
    meal = session.get(MealLog, meal_id)
    if meal is None:
        return False
    session.delete(meal)
    session.flush()
    return True


def get_daily_tally(session: Session, day: date | None = None) -> tuple[float, float]:
    """(kcal, protein) logged on `day`, defaulting to today."""
    day = day or date.today()
    row = session.execute(
        select(
            func.coalesce(func.sum(MealLog.total_kcal), 0.0),
            func.coalesce(func.sum(MealLog.total_protein_g), 0.0),
        ).where(func.date(MealLog.timestamp) == day.isoformat())
    ).one()
    return float(row[0]), float(row[1])


def get_meals_for_day(session: Session, day: date | None = None) -> list[dict[str, Any]]:
    """Every meal logged on `day`, oldest first."""
    day = day or date.today()
    meals = session.scalars(
        select(MealLog)
        .where(func.date(MealLog.timestamp) == day.isoformat())
        .order_by(MealLog.timestamp)
    )
    return [
        {
            "id": meal.id,
            "time": meal.timestamp.strftime("%H:%M"),
            "raw_text": meal.raw_text,
            "items": meal.items,
            "total_kcal": meal.total_kcal,
            "total_protein_g": meal.total_protein_g,
        }
        for meal in meals
    ]


def get_daily_history(session: Session, days: int = 7) -> list[dict[str, Any]]:
    """Per-day totals for the last `days` days, newest first.

    Days with nothing logged are included as zeros, so a gap stays visible
    instead of the timeline silently closing up.
    """
    day_column = func.date(MealLog.timestamp)
    rows = session.execute(
        select(
            day_column,
            func.sum(MealLog.total_kcal),
            func.sum(MealLog.total_protein_g),
            func.count(MealLog.id),
        ).group_by(day_column)
    ).all()
    totals = {
        row[0]: {"kcal": float(row[1]), "protein_g": float(row[2]), "meals": int(row[3])}
        for row in rows
    }

    today = date.today()
    empty = {"kcal": 0.0, "protein_g": 0.0, "meals": 0}
    return [
        {"date": today - timedelta(days=n),
         **totals.get((today - timedelta(days=n)).isoformat(), empty)}
        for n in range(days)
    ]


def get_cached_food(session: Session, query: str) -> dict[str, Any] | None:
    """A previously resolved USDA lookup, or None."""
    row = session.get(FoodCache, query)
    if row is None:
        return None
    return {
        "description": row.description, "kcal": row.kcal,
        "protein_g": row.protein_g, "carbs_g": row.carbs_g, "fat_g": row.fat_g,
    }


def cache_food(session: Session, query: str, food: dict[str, Any]) -> None:
    """Remember a USDA result so the next lookup is free."""
    session.merge(FoodCache(query=query, **food))
    session.flush()


def save_chat_message(session: Session, role: str, content: str) -> None:
    session.add(ChatMessage(role=role, content=content))
    session.flush()


def get_chat_history(session: Session, limit: int = 200) -> list[tuple[str, str]]:
    """The last `limit` turns as (role, content), oldest first."""
    recent = session.execute(
        select(ChatMessage.role, ChatMessage.content)
        .order_by(ChatMessage.id.desc())
        .limit(limit)
    ).all()
    return [(role, content) for role, content in reversed(recent)]


def clear_chat_history(session: Session) -> int:
    """Delete the transcript. Returns how many turns went."""
    return int(session.query(ChatMessage).delete())
