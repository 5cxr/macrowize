"""Point the DB at a throwaway file before anything imports `config`."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP_DB = Path(tempfile.mkdtemp(prefix="macrowize-test-")) / "test.db"
os.environ["MACROWIZE_DB"] = str(_TMP_DB)

import pytest  # noqa: E402

from models.db import Base, SessionLocal, engine  # noqa: E402


@pytest.fixture
def session():
    """Give each test an empty database and a session against it."""
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        yield db
        db.commit()
    finally:
        db.close()
        Base.metadata.drop_all(engine)
