import pytest

from aurora import db


@pytest.fixture(autouse=True)
def banco(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "aurora.db")
    db.restaurar()
    return db.DB_PATH
