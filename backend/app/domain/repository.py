from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from uuid import UUID

from backend.app.domain.models import (
    SurveyGeography,
    SurveyRecord,
    SurveyState,
    SurveyStateEvent,
    UploadedFile,
)
from backend.app.utils.json_codec import pretty_json
from backend.app.utils.paths import safe_join


class SurveyNotFoundError(KeyError):
    pass


class SurveyRepository:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.upload_dir = data_dir / "uploads"
        self.database_path = data_dir / "library-survey.sqlite3"

    def initialize(self) -> None:
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS surveys (
                    survey_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    geography_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    sealed_at TEXT,
                    package_hash TEXT
                );

                CREATE TABLE IF NOT EXISTS uploaded_files (
                    survey_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    PRIMARY KEY (survey_id, path),
                    FOREIGN KEY (survey_id) REFERENCES surveys(survey_id)
                );

                CREATE TABLE IF NOT EXISTS idempotency_records (
                    scope TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (scope, idempotency_key)
                );

                CREATE TABLE IF NOT EXISTS survey_state_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    survey_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    detail TEXT,
                    FOREIGN KEY (survey_id) REFERENCES surveys(survey_id)
                );
                """
            )

    def create(self, record: SurveyRecord) -> SurveyRecord:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO surveys (
                    survey_id, display_name, geography_json, status, created_at,
                    sealed_at, package_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(record.survey_id),
                    record.display_name,
                    record.geography.model_dump_json(),
                    record.status,
                    record.created_at.isoformat(),
                    None,
                    None,
                ),
            )
            connection.execute(
                """
                INSERT INTO survey_state_events (survey_id, state, occurred_at, detail)
                VALUES (?, ?, ?, ?)
                """,
                (str(record.survey_id), record.status, record.created_at.isoformat(), None),
            )
        return record

    def get(self, survey_id: UUID) -> SurveyRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM surveys WHERE survey_id = ?", (str(survey_id),)
            ).fetchone()
        if row is None:
            raise SurveyNotFoundError(str(survey_id))
        return SurveyRecord(
            survey_id=row["survey_id"],
            display_name=row["display_name"],
            geography=SurveyGeography.model_validate_json(row["geography_json"]),
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            sealed_at=datetime.fromisoformat(row["sealed_at"]) if row["sealed_at"] else None,
            package_hash=row["package_hash"],
        )

    def store_upload(self, survey_id: UUID, upload: UploadedFile, content: bytes) -> UploadedFile:
        self.get(survey_id)
        destination = self.upload_path(survey_id, upload.path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_bytes(content)
        temporary.replace(destination)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO uploaded_files (survey_id, path, mime_type, bytes, sha256)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(survey_id, path) DO UPDATE SET
                    mime_type = excluded.mime_type,
                    bytes = excluded.bytes,
                    sha256 = excluded.sha256
                """,
                (
                    str(survey_id),
                    upload.path,
                    upload.mime_type,
                    upload.bytes,
                    upload.sha256,
                ),
            )
        return upload

    def uploads(self, survey_id: UUID) -> dict[str, UploadedFile]:
        self.get(survey_id)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT path, mime_type, bytes, sha256 FROM uploaded_files WHERE survey_id = ?",
                (str(survey_id),),
            ).fetchall()
        return {row["path"]: UploadedFile.model_validate(dict(row)) for row in rows}

    def seal(self, survey_id: UUID, package_hash: str, sealed_at: datetime) -> SurveyRecord:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE surveys
                SET sealed_at = ?, package_hash = ?
                WHERE survey_id = ?
                """,
                (sealed_at.isoformat(), package_hash, str(survey_id)),
            )
            if cursor.rowcount == 0:
                raise SurveyNotFoundError(str(survey_id))
        return self.get(survey_id)

    def transition(
        self,
        survey_id: UUID,
        state: SurveyState,
        *,
        detail: str | None = None,
        occurred_at: datetime,
    ) -> SurveyRecord:
        self.get(survey_id)
        with self._connect() as connection:
            connection.execute(
                "UPDATE surveys SET status = ? WHERE survey_id = ?",
                (state, str(survey_id)),
            )
            connection.execute(
                """
                INSERT INTO survey_state_events (survey_id, state, occurred_at, detail)
                VALUES (?, ?, ?, ?)
                """,
                (str(survey_id), state, occurred_at.isoformat(), detail),
            )
        return self.get(survey_id)

    def events(self, survey_id: UUID) -> list[SurveyStateEvent]:
        self.get(survey_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT sequence, survey_id, state, occurred_at, detail
                FROM survey_state_events
                WHERE survey_id = ?
                ORDER BY sequence
                """,
                (str(survey_id),),
            ).fetchall()
        return [SurveyStateEvent.model_validate(dict(row)) for row in rows]

    def write_manifest(self, survey_id: UUID, manifest: dict[str, object]) -> None:
        destination = self.upload_dir / str(survey_id) / "manifest.json"
        destination.write_text(pretty_json(manifest), encoding="utf-8")

    def get_idempotency(self, scope: str, key: str) -> tuple[str, str] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT request_hash, response_json
                FROM idempotency_records
                WHERE scope = ? AND idempotency_key = ?
                """,
                (scope, key),
            ).fetchone()
        if row is None:
            return None
        return row["request_hash"], row["response_json"]

    def save_idempotency(
        self,
        *,
        scope: str,
        key: str,
        request_hash: str,
        response_json: str,
        created_at: datetime,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO idempotency_records (
                    scope, idempotency_key, request_hash, response_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (scope, key, request_hash, response_json, created_at.isoformat()),
            )

    def upload_path(self, survey_id: UUID, relative_path: str) -> Path:
        return safe_join(self.upload_dir / str(survey_id), relative_path)

    def package_root(self, survey_id: UUID) -> Path:
        return self.upload_dir / str(survey_id)

    def _connect(self) -> sqlite3.Connection:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection
