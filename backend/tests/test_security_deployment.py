from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_reverse_proxy_requires_https_and_keeps_backend_private() -> None:
    caddy = (ROOT / "deploy" / "Caddyfile").read_text(encoding="utf-8")
    assert "{$LIBRARY_API_DOMAIN}" in caddy
    assert "reverse_proxy 127.0.0.1:8000" in caddy
    assert "Strict-Transport-Security" in caddy
    assert "tls internal" not in caddy


def test_security_operations_require_reviewed_plan_ids() -> None:
    retention = (ROOT / "scripts" / "retention_cleanup.py").read_text(encoding="utf-8")
    migration = (ROOT / "scripts" / "migrate_object_encryption.py").read_text(
        encoding="utf-8"
    )
    assert "--execute-plan" in retention
    assert "--execute-plan" in migration
    assert 'parser.add_argument("--execute", action="store_true")' not in retention
