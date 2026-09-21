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

