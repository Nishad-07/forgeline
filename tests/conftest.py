from __future__ import annotations

import pytest

from forgeline.store.db import Database


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "test.db")
    yield database
    database.close()
