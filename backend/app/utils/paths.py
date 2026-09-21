from pathlib import Path, PurePosixPath


def validate_package_path(value: str) -> str:
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or ".." in candidate.parts or "\\" in value:
        raise ValueError("file path must be a safe package-relative POSIX path")
    if value != candidate.as_posix() or value in {"", "."}:
        raise ValueError("file path must be normalized")
    return value


def safe_join(base: Path, relative_path: str) -> Path:
    validate_package_path(relative_path)
    resolved_base = base.resolve()
    destination = (resolved_base / relative_path).resolve()
    if not destination.is_relative_to(resolved_base):
        raise ValueError("path escapes its package root")
    return destination

