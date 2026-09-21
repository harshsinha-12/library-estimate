from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from uuid import UUID

from backend.app.domain.models import SurveyGeography, SurveyRecord, UploadedFile
from backend.app.domain.repository import SurveyNotFoundError, SurveyRepository
from backend.app.utils.paths import safe_join, validate_package_path


def migrate_sqlite_if_present(
    data_dir: Path, repository: SurveyRepository
) -> dict[str, int | bool]:
    """Copy Stage 1 SQLite rows and local files into Redis + object storage.

    The SQLite file is left in place as an archive. Runtime code does not read it.
    """
    database = data_dir / "library-survey.sqlite3"
    upload_dir = data_dir / "uploads"
    if not database.is_file():
        return {"migrated_surveys": 0, "migrated_files": 0, "skipped": 0, "preserved_sqlite": False}
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    surveys = connection.execute("SELECT * FROM surveys").fetchall()
    migrated_surveys = 0
    migrated_files = 0
    skipped = 0
    for row in surveys:
        survey_id = UUID(row["survey_id"])
        try:
            repository.get(survey_id)
            skipped += 1
            continue
        except SurveyNotFoundError:
            pass
        record = SurveyRecord(
            survey_id=survey_id,
            display_name=row["display_name"],
            geography=SurveyGeography.model_validate_json(row["geography_json"]),
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            sealed_at=datetime.fromisoformat(row["sealed_at"]) if row["sealed_at"] else None,
            package_hash=row["package_hash"],
        )
        repository.create(record)
        if record.status != "created":
            repository.transition(
                survey_id,
                record.status,
                occurred_at=record.sealed_at or record.created_at,
                detail="migrated-from-sqlite",
            )
        if record.package_hash and record.sealed_at:
            repository.seal(survey_id, record.package_hash, record.sealed_at)
        files = connection.execute(
            "SELECT path, mime_type, bytes, sha256 FROM uploaded_files WHERE survey_id = ?",
            (str(survey_id),),
        ).fetchall()
        for item in files:
            path = item["path"]
            try:
                validate_package_path(path)
            except ValueError:
                continue
            source = safe_join(upload_dir / str(survey_id), path)
            if not source.is_file():
                continue
            content = source.read_bytes()
            repository.store_upload(
                survey_id,
                UploadedFile(
                    path=path,
                    mime_type=item["mime_type"],
                    bytes=item["bytes"],
                    sha256=item["sha256"],
                ),
                content,
            )
            migrated_files += 1
        derived = upload_dir / str(survey_id) / "derived"
        if derived.is_dir():
            for file_path in derived.rglob("*"):
                if not file_path.is_file():
                    continue
                relative = file_path.relative_to(upload_dir / str(survey_id)).as_posix()
                repository.put_bytes(
                    survey_id,
                    relative,
                    file_path.read_bytes(),
                    "image/svg+xml" if relative.endswith(".svg") else "application/json",
                )
                migrated_files += 1
        migrated_surveys += 1
    connection.close()
    return {
        "migrated_surveys": migrated_surveys,
        "migrated_files": migrated_files,
        "skipped": skipped,
        "preserved_sqlite": True,
    }
