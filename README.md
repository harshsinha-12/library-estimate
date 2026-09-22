# Library Survey

iOS capture + FastAPI backend for a **library replacement-cost survey**. RoomPlan geometry, on-device spine tracking, sealed evidence in Redis + Cloudflare R2, OpenAI web-search drafts, then Fable / Astra Extra / Jev after seal.

Models classify and propose. They do **not** write count, ISBN, geometry, or money. Draft web prices stay drafts until an operator confirms a physical listing.

## Current state (2026-09-22)

| Area | Status |
| --- | --- |
| Stage 1 capture, seal, 2D/3D | Closed on device (`eb3f30fa` canonical) |
| Stage 2–4 fixture/storage gates | Passed |
| Live Pass B count | On-device rectangles + shelf-face tracking; reverse sweep must not double |
| Stage 4/5 physical 8–10 book row | **Open** |
| Independent 50–100 copy holdout / policy promotion | **Open** |

Full clocks: [`CHECKPOINTS.md`](CHECKPOINTS.md). Remaining work: [`LEFTOVER.md`](LEFTOVER.md). Architecture: [`FINAL-PLAN.md`](FINAL-PLAN.md). Build order: [`IMPLEMENTATION.md`](IMPLEMENTATION.md). Phone USB install: [`INSTALLATION.md`](INSTALLATION.md).

**Storage:** Redis holds Survey IR, jobs, and state. Cloudflare R2 holds sealed media (`audio/survey.m4a`, frames, USDZ). SQLite is a Stage 1 archive only.

## Invertis Library report

Live survey **2026-09-22** at Invertis Library, Bareilly (`en-IN`).

- Survey ID: `4b9d7885-af80-42b8-b8c3-827c7a7f07fd`
- PDF: [`docs/invertis-library-report.pdf`](docs/invertis-library-report.pdf)
- Recorded copies: **45**
- Unique book titles / copies: **5 / 45**
- Confirmed / eligible books: **0 / 45** (no operator-confirmed listings)
- Draft-priced / eligible books: **18 / 45** (four titled groups with web-search unit prices × copy count)
- Contents confirmed: none
- Contents drafts (books): **8,288 INR** (not confirmed)
- Other object drafts: mattress, cupboards, coffee table, study tables, split ACs, windows (operator/speech counts × unit price; not confirmed)
- Building reconstruction: **37,761,129.14 INR** for **580.94 m²** (demo rebuild rates, not sale value)
- Fable (A), Astra Extra (B), Jev, and Astra-live assist all **ran**
- Inventory status: **partial**

This does **not** close the 8–10 book physical row gate.

### Sealed package

Hashes verified locally. 8 walls, **580.9 m²**, ceiling 250 cm. N is scan +Z, not magnetic north. Geography: Bareilly, IN (`source: gps`). 323 files. Shelf pins are **unregistered overlays** (not operator-placed footprints).

![2D tagged floor plan](docs/invertis-library/sealed-2d-plan.png)

![Walls, doors, windows, openings, shelves](docs/invertis-library/sealed-openings-shelves.png)

![Spatial evidence 2D and 3D](docs/invertis-library/spatial-evidence.png)

![3D RoomPlan model and package](docs/invertis-library/sealed-3d-package.png)

![Upload accepted](docs/invertis-library/sealed-upload.png)

### Pass B on the stacks

Astra-live captions are assist only, not inventory. Rows stayed **partial** (blur / unconfirmed actual).

![Five persistent candidates on a row](docs/invertis-library/pass-b-row-five.jpg)

![Slot 1 on a run of Organization Behavior](docs/invertis-library/pass-b-slot-one.jpg)

![Eleven persistent candidates, 86% readable coverage](docs/invertis-library/pass-b-row-eleven.jpg)

Download a sealed report from the laptop while uvicorn is up:

```bash
curl -o ~/Downloads/library-survey.pdf \
  http://127.0.0.1:8000/v1/surveys/4b9d7885-af80-42b8-b8c3-827c7a7f07fd/report.pdf
```

## Run

Copy [`.env.example`](.env.example) to `.env.local` (untracked). Never put keys in the iOS app.

```bash
make check         # Ruff, compileall, backend/schema tests
python3 -m uvicorn backend.app.main:production_app --factory --host 0.0.0.0 --port 8000
```

Phone Backend URL: `http://<Mac-Wi-Fi-IP>:8000`. Mac and iPhone must share a network that allows client-to-client traffic. Uvicorn binds `0.0.0.0`, not `127.0.0.1`.

```bash
make ios-project   # XcodeGen
make ios-build     # generic iOS Simulator
```

Physical install: [`INSTALLATION.md`](INSTALLATION.md).

## Capture rules that actually ship

1. **Sequential camera.** RoomPlan owns Pass A. Pass B shelf AR starts after RoomPlan releases. Pass C stills after shelf AR stops. No optical zoom during RoomPlan.
2. **Hierarchy.** room → unit → face A/B → row → spine instance → physical copy. Face B is a different copy. Reverse sweep updates evidence; it does not mint a second copy. Same ISBN in two slots stays two IDs.
3. **Astra-live** during Pass B/C is assist metadata, not inventory.
4. **After seal.** Fable (A) and Astra Extra (B) on the same sealed bytes; Jev scores A vs B and does not write count or price.
5. **Price search** once per unique edition + market via OpenAI Responses `web_search` (survey geography as `user_location`). No Bing. No Amazon scrape.

## Layout

```text
backend/     FastAPI, Redis, R2, pricing, Fable/Astra/Jev, reports
ios/          LibrarySurvey (SwiftUI, RoomPlan, Vision)
cv/           labeled-JSON shelf count helpers
schemas/      Survey IR and related JSON Schema
docs/         gates, deployment, Invertis PDF and screenshots
eval/         holdout/preflight (templates are not device accuracy)
```
