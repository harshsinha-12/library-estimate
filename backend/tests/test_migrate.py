from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from uuid import uuid4

import fakeredis

from backend.app.domain.repository import SurveyRepository
from backend.app.storage.objects import MemoryObjectStore
from backend.app.workflows.migrate_sqlite import migrate_sqlite_if_present


def test_sqlite_archive_is_migrated_and_preserved(tmp_path) -> None:
    database = tmp_path / "library-survey.sqlite3"
    upload_root = tmp_path / "uploads"
    survey_id = uuid4()
    package = upload_root / str(survey_id)
    package.mkdir(parents=True)
    payload = b'{"ok":true}\n'
    (package / "notes").mkdir()
    file_path = package / "notes" / "annotations.json"
    file_path.write_bytes(payload)
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE surveys (
            survey_id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            geography_json TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            sealed_at TEXT,
            package_hash TEXT
        );
        CREATE TABLE uploaded_files (
            survey_id TEXT NOT NULL,
            path TEXT NOT NULL,
            mime_type TEXT NOT NULL,
            bytes INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            PRIMARY KEY (survey_id, path)
        );
        CREATE TABLE idempotency_records (
            scope TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            response_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (scope, idempotency_key)
        );
        CREATE TABLE survey_state_events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            survey_id TEXT NOT NULL,
            state TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            detail TEXT
        );
        """
    )
    now = datetime.now(UTC).isoformat()
    geography = (
        '{"country_code":"IN","city":"New Delhi","currency":"INR",'
        '"market":"en-IN","source":"manual","precise_location_consent":false}'
    )
    connection.execute(
        "INSERT INTO surveys VALUES (?, ?, ?, ?, ?, ?, ?)",
        (str(survey_id), "Archived", geography, "created", now, None, None),
    )
    connection.execute(
        "INSERT INTO uploaded_files VALUES (?, ?, ?, ?, ?)",
        (str(survey_id), "notes/annotations.json", "application/json", len(payload), "a" * 64),
    )
    connection.commit()
    connection.close()

    repository = SurveyRepository(
        fakeredis.FakeRedis(decode_responses=True),
        MemoryObjectStore(),
        key_prefix="ls:test:migrate",
    )
    result = migrate_sqlite_if_present(tmp_path, repository)
    assert result["migrated_surveys"] == 1
    assert result["preserved_sqlite"] is True
    assert database.is_file()
    stored = repository.get(survey_id)
    assert stored.display_name == "Archived"
    assert repository.get_bytes(survey_id, "notes/annotations.json") == payload
    again = migrate_sqlite_if_present(tmp_path, repository)
    assert again["skipped"] == 1
    assert again["migrated_surveys"] == 0
