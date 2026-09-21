# Model and provider contracts

The model boundary is deliberately provider-neutral. Fable and Astra replay receive the same sealed evidence package and must return the same `ModelAssessment v1` shape before Jev can compare them.

## Configured OpenAI roles

| Role | Default | Configuration |
| --- | --- | --- |
| Focused smaller-model work | `gpt-5.6-luna` | `OPENAI_SMALL_MODEL` |
| Cover/spine title reading | `gpt-4o-mini` | `OPENAI_VISION_MODEL` |
| Text-to-speech | `gpt-4o-mini-tts` | `OPENAI_TTS_MODEL` |
| TTS voice | `marin` | `OPENAI_TTS_VOICE` |

The TTS UI must disclose that generated speech is AI-generated. The defaults follow the [official OpenAI text-to-speech guide](https://developers.openai.com/api/docs/guides/text-to-speech) and remain server-side configuration; no provider key belongs in the iOS app.

## Stage 5 replay and usage

`POST /v1/surveys/{survey_id}/assets/{asset_copy_id}/model-replay` freezes a bounded per-copy evidence JSON in object storage, then sends its same bytes independently to Fable (`claude-fable-5-1`) and Astra replay (`gpt-6-astra`). The iOS inventory copy screen posts this route and `GET /v1/surveys/{survey_id}/model-runs` shows the stored results. The package names the already-extracted copy in `target_identity` (title, ISBN, crop when present) so a crowded shelf frame is scored as that one copy, not as an unlabeled stack. Up to two stored JPEG/PNG references are attached as provider image inputs, with a per-copy crop preferred when it exists. Raw provider responses are saved before normalized assessments. Jev (`jev-latest`) then receives only structured A/B assessments and deterministic flags; its choice and probabilities are retained separately from the final policy decision. A failed or unavailable provider routes to human review. No live Fable call is part of local verification.

Every replay decision appends an `RLTransition` with `reward: null` until independently labeled feedback exists. Human Stage 3 review actions also append transitions, but the offline trainer excludes human-selected actions from policy fitting. Independent labels carry the reward table outcome. A disjoint-survey trainer fits supported logged policy actions and specialist heads, registers a shadow artifact, and scores labeled holdout transitions. Shadow output includes action support and an overlap warning; it never updates live routing.

Provider calls record the returned model ID, input/output/cached tokens, web-search call count, latency, dated rate source, and estimated USD cost under the HTTP `X-Survey-Run-Id`. Read one run at `GET /v1/surveys/{survey_id}/runs/{run_id}/usage` or the survey ledger at `GET /v1/surveys/{survey_id}/usage`. Unknown model IDs are recorded as unpriced, not zero cost. The `$50` survey cap uses conservative pre-call reservations; failed calls keep their reservation because upstream billing may still occur. Published rates are snapshots and invoice charges may differ.

Reports are available as JSON and PDF at `GET /v1/surveys/{survey_id}/report` and `GET /v1/surveys/{survey_id}/report.pdf`. These endpoints require a sealed survey. Authenticated evidence access, optional object encryption, retention selection, and operator access logging are implemented. Secure network deployment and physical-device validation remain open; see [`stage-5-validation.md`](stage-5-validation.md).

## Fable and Astra output

The canonical contract is [`schemas/model-assessment.schema.json`](../schemas/model-assessment.schema.json). It permits bounded classification and routing fields, but it does not permit a model to write geometry, invent an ISBN, fetch a hidden price, merge physical copies, or perform valuation arithmetic.

Runtime validation is provider-specific at the edge:

- `backend/app/providers/models/fable.py` accepts only `pipeline=fable`.
- `backend/app/providers/models/astra.py` accepts only `pipeline=astra_replay`.
- `backend/app/providers/models/normalization.py` performs strict Pydantic validation and rejects extra fields.

The raw provider response and the normalized assessment are different records. Provider integrations must preserve the raw response as immutable audit evidence before parsing it; only the validated normalized assessment may enter Jev.

## Zod and other clients

Python is the backend implementation language, so Pydantic is the runtime validator there. A future TypeScript service or client should generate or define a strict Zod validator from the canonical JSON Schema and run it at the same provider boundary. It must not introduce a second, divergent response shape.

## Fetchers and adapters

External I/O remains behind one interface per concern:

- catalog fetchers implement `CatalogFetcher`;
- pricing fetchers implement `PriceFetcher`;
- model adapters implement `ModelAssessmentProvider`;
- provider-specific parsing remains in separate adapter files.

Configuration, hashing, canonical JSON, path safety, and clocks live in dedicated modules under `backend/app/config/` and `backend/app/utils/`.
