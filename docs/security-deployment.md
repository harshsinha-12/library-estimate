# Security deployment and destructive-operation validation

## Production HTTPS

The checked-in `deploy/Caddyfile` is a deployable Caddy reverse proxy with automatic
certificate issuance. Keep Uvicorn private and let only Caddy listen publicly:

```sh
export LIBRARY_API_DOMAIN=survey-api.example.com
export ACME_EMAIL=operations@example.com
caddy validate --config deploy/Caddyfile --adapter caddyfile
caddy run --config deploy/Caddyfile --adapter caddyfile
python3 -m uvicorn backend.app.main:production_app --factory --host 127.0.0.1 --port 8000
```

Set `LIBRARY_REQUIRE_AUTH=1`, a strong `LIBRARY_OPERATOR_TOKEN`, and
`LIBRARY_DATA_ENCRYPTION_KEY` in the backend service environment. Keep R2 private.
Do not put secrets in the Caddyfile or its access log.

Validate from another host:

```sh
curl --fail --proto '=https' --tlsv1.2 https://survey-api.example.com/healthz
curl -I http://survey-api.example.com/healthz
```

The first command must return healthy JSON over HTTPS. The second must redirect to
HTTPS. Confirm the certificate hostname and issuer before configuring the iOS app.

Local development intentionally remains unauthenticated HTTP: leave
`LIBRARY_REQUIRE_AUTH=0`, do not configure an operator token in the app, and bind
Uvicorn to the required LAN interface. Never send a bearer token over HTTP.

## iOS package protection and face redaction

Sealed package files use iOS Data Protection
`completeUntilFirstUserAuthentication` and are excluded from device backups. For a
private survey build, set `LibraryFaceRedactionRequired` to `true` in
`ios/LibrarySurvey/project.yml`, regenerate with `make ios-project`, and build/install
that configuration. When enabled, every JPEG written to the sealed package passes
through Vision face detection and pixelation; decode, detection, or render failures
abort sealing instead of retaining unredacted bytes. Inspect
`privacy/face-redaction.json` in the resulting package and retain it with the run
artifacts.

The hook detects faces, not arbitrary body silhouettes or speech PII. Keep the capture
zone controlled, obtain consent, and review package evidence before provider upload.

## R2 plaintext encryption migration

The migration command is dry-run-only unless given the exact plan ID it prints:

```sh
python3 scripts/migrate_object_encryption.py --prefix 'SURVEY_OR_NAMESPACE_PREFIX/'
python3 scripts/migrate_object_encryption.py \
  --prefix 'SURVEY_OR_NAMESPACE_PREFIX/' \
  --execute-plan REVIEWED_PLAN_ID
```

Review the complete key/digest list and retain the dry-run output in the change
record. A content change produces a different plan or is skipped during execution.
Run the dry run again afterward; `plaintext=0` is the success condition. Do not run
this against live storage without a tested restore path and a maintenance window.

## Retention execution

Retention also requires the exact current dry-run plan ID:

```sh
python3 scripts/retention_cleanup.py
python3 scripts/retention_cleanup.py --execute-plan REVIEWED_PLAN_ID
```

Review every survey ID before execution. A newly eligible survey changes the plan
ID and prevents execution under the older approval. Re-running an already executed
plan is safe and reports already-absent surveys.
