# library-estimate

**Start here:** [`IMPLEMENTATION.md`](IMPLEMENTATION.md) — entire project, A to Z, 24-hour clock. Architecture is in [`FINAL-PLAN.md`](FINAL-PLAN.md). USB install onto the iPhone: [`INSTALLATION.md`](INSTALLATION.md).

## Current implementation

Stage 1's device gate is closed; the canonical sealed capture is `eb3f30fa`. Stage 2–4
fixture/storage gates pass, and Stage 5 now has security, accessibility, evaluation, and
demo-readiness tooling. The physical 8–10-book row, live Fable/Astra/Jev confirmation,
independent 50–100-book holdout, policy promotion, and recorded LiDAR demo remain open.
See [`LEFTOVER.md`](LEFTOVER.md) and [`CHECKPOINTS.md`](CHECKPOINTS.md) for the current
operator boundary instead of treating fixture results as device accuracy.

Storage decision: Redis holds Survey IR, metadata, idempotency, jobs, and state. Cloudflare R2 (S3-compatible) holds sealed media. SQLite is an archive of Stage 1 demo data only; it is not a runtime dependency.

```bash
make check         # Ruff, compileall, and backend/schema tests
make run-backend   # local FastAPI service
make ios-project   # regenerate the Xcode project with XcodeGen
make ios-build     # compile for a generic iOS Simulator
```

Secrets stay in `.env.local`. The iOS app contains no provider credentials.

I’d structure the actual implementation around one principle:

> **Never trust one frame, one model, or one signal. Combine geometry + tracking + visual evidence + OCR + speech + metadata, and preserve confidence/provenance at every step.**

## 1. Capture layer: collect everything once

The iOS app would be Swift/SwiftUI with **RoomPlan + ARKit + AVFoundation + Vision**.

RoomPlan handles room geometry. Apple supports combining multiple room scans into a `CapturedStructure`, so scanning Room 1 and Room 2 separately can still produce one building representation. ([Apple Developer](https://developer.apple.com/documentation/roomplan/capturedstructure?changes=_2.&utm_source=chatgpt.com))

At the same time, I store RGB video, audio and ARKit camera poses. ARKit exposes the camera's position and orientation in world coordinates, which becomes extremely useful for deduplication. ([Apple Developer](https://developer.apple.com/documentation/arkit/arcamera/transform?changes=__2&utm_source=chatgpt.com))

Every piece of evidence gets synchronized by timestamp:

```text
t = 124.82 sec

RGB frame
Camera pose
RoomPlan state
Audio transcript
Detected objects
User annotation
```

So if the surveyor says:

> "This painting is damaged."

I can inspect exactly what the camera was pointing at when those words were spoken.

---



# 2. Immediately check capture quality

Before worrying about AI, detect bad evidence.

For every frame I'd calculate things such as blur, exposure, camera velocity, object size and text readability.

Example:

```text
Book spine detected
width = 17 pixels
OCR confidence = 0.21

→ DON'T attempt ISBN identification
→ show "Move closer to shelf"
```

This avoids garbage-in-garbage-out.

Apple Vision can run OCR locally and supports on-device text recognition, which is useful for this first pass. ([Apple Developer](https://developer.apple.com/documentation/vision/recognizing-text-in-images?changes=_2_1__2_8&language=objc&utm_source=chatgpt.com))

---



# 3. Build the room first

RoomPlan gives me:

```text
walls
floors
doors
windows
openings
objects
room relationships
```

and can export a 3D model. ([Apple Developer](https://developer.apple.com/documentation/roomplan/capturedroom?changes=_2_1_4_5&language=objc&utm_source=chatgpt.com))

I convert that into my own representation:

```text
Property
 └── Floor 1
      ├── Room A
      │    ├── Shelf 1
      │    └── Shelf 2
      └── Room B
```

I would never make the rest of the backend depend directly on Apple's data format.

Instead:

```text
RoomPlan
   ↓
Survey IR
```

This way an Android/SfM pipeline could eventually produce the same `Survey IR`.

---



# 4. Detect shelves before detecting books

A major improvement would be hierarchical detection.

Instead of:

```text
image → find 400 books
```

do:

```text
Room
 ↓
Bookshelf
 ↓
Shelf row
 ↓
Individual spine
```

Then an individual book might become:

```text
room_2.shelf_4.row_3.book_12
```

Its location becomes another identity signal.

This is extremely useful for deduplication.

---



# 5. Track books instead of counting detections

Suppose one book appears across 40 video frames.

You don't have 40 books.

You have:

```text
Physical Asset
book_327

Observations
obs_1
obs_2
obs_3
...
obs_40
```

I'd use an object tracker, potentially a ByteTrack/BoT-SORT-style pipeline initially, combined with camera motion and spatial information.

The important architecture is:

```text
Detection != Asset

Detection
   ↓
Tracking
   ↓
Spatial reconciliation
   ↓
Physical Asset
```

---



# 6. The nasty duplicate edge case

Imagine:

```text
Shelf A contains Clean Code

You scan left → right.

Then accidentally scan right → left.

Then another Clean Code exists on Shelf C.
```

ISBN alone cannot solve this because both physical copies have the same ISBN.

I'd combine:

```text
visual embedding
ISBN/title
shelf ID
3D position
camera trajectory
temporal continuity
book dimensions
neighbouring books
```

For example:

```text
Observation A
ISBN = X
Shelf = 3
XYZ = (2.1, 1.4, 5.8)

Observation B
ISBN = X
Shelf = 3
XYZ = (2.12, 1.39, 5.81)

→ probably same physical book
```

But:

```text
Observation C
ISBN = X
Shelf = 8
XYZ = (12.6, 1.3, 2.4)

→ another physical copy
```

I would calculate a `P(same_asset)` score instead of using a brittle equality rule.

---



# 7. Books with unreadable spines

This will happen constantly.

Instead of failing immediately:

```text
Frame 1 → "DESIGNING..."
Frame 2 → "DATA INT..."
Frame 3 → "MARTIN KLE..."
```

Aggregate information over the whole track.

Then infer:

```text
Designing Data-Intensive Applications
Martin Kleppmann
```

Now search your book catalogue for candidates.

The system stores:

```text
identity.status = probable
confidence = 0.91
```

Not:

```text
ISBN = definitely XYZ
```

---



# 8. ISBN edge cases

I would use this confidence hierarchy:


| Evidence                             | Reliability |
| ------------------------------------ | ----------- |
| Barcode successfully decoded         | Very high   |
| Full ISBN text visible               | High        |
| Title + author + publisher + edition | Medium/high |
| Title + author only                  | Medium      |
| Visual similarity only               | Low         |


If multiple editions exist, don't guess.

Example:

```text
Harry Potter...
Candidate 1: Hardcover 2019
Candidate 2: Paperback 2021

→ NEEDS_VERIFICATION
```

App asks the surveyor:

> Scan the barcode/back cover.

---



# 9. Books aren't always vertical

You'll encounter:

```text
stacked books
horizontal books
books behind other books
books facing backwards
double-row shelves
books lying on tables
```

This is why shelf-level counting alone won't work.

I'd let the detector classify orientation and visibility.

Example:

```text
book_491
visibility = 0.42
identity = unresolved
count_confidence = 0.91
```

Important distinction:

> We may know that **a physical book exists** without knowing **which book it is**.

So inventory count can still be correct even if ISBN coverage isn't 100%.

---



# 10. Books hidden behind books

No computer vision system can count something it literally never sees.

So the app needs a **coverage metric**.

Example:

```text
Shelf 8

Front coverage: 98%
Side coverage: 91%
Rear visibility: unavailable

Possible concealed inventory: YES
```

The final insurance report should say:

```text
Visible inventory count: 2,941
Verified ISBN count: 2,611
Unresolved visible books: 330
Hidden inventory cannot be verified
```

Much better than pretending certainty.

---



# 11. Other objects

I'd use a general object detector initially and then specialist classification.

```text
Object detector
     ↓
book
painting
furniture
electronics
cup
sculpture
unknown
```

But classification and valuation are separate.

A cup could be:

```text
detected = true
insurable = false
valuation = skipped
```

This proves we noticed it rather than accidentally missing it.

---



# 12. Unknown/high-value object edge case

Suppose the model sees some unusual antique.

Don't let it invent:

> Estimated value $26,400.

Instead:

```text
category: decorative_object
potential_high_value: true
identity_confidence: 0.34

valuation_status:
REQUIRES_APPRAISAL
```

Insurance workflows should strongly prefer uncertainty over fabricated precision.

---



# 13. Damage detection

I'd combine three signals:

```text
visual evidence
+
surveyor speech
+
surveyor manual annotation
```

Suppose the surveyor says:

> "This painting is scratched."

but the CV system doesn't see the scratch.

Store both:

```text
human_claim: scratched

vision:
damage = none
confidence = .63
```

Then send the crop plus annotation to Fable/Astra.

Never overwrite one source with another.

---



# 14. Spoken annotation edge case

Imagine camera contains:

```text
painting
chair
lamp
bookshelf
```

and surveyor says:

> "This is damaged."

What is "this"?

Use:

```text
camera centre
gaze direction approximation
nearest object
object recently tapped
semantic compatibility
audio timing
```

If confidence remains low:

```text
annotation_target = UNKNOWN
```

and the review UI asks:

> Which object were you referring to?

---



# 15. Object moves during scanning

Suppose someone removes a book halfway through the scan.

Pure XYZ matching could fail.

I'd keep:

```text
first_seen
last_seen
trajectory
visual embedding
neighbour context
```

Then the system can determine:

```text
Possible moved asset
```

rather than automatically creating another one.

---



# 16. Fable pipeline

Fable gets **evidence packages**, not raw hours of video.

For one uncertain asset:

```json
{
  "best_frames": ["...", "..."],
  "ocr": "...",
  "audio_note": "...",
  "location": "...",
  "candidate_identifications": ["..."],
  "deterministic_cv": "..."
}
```

Fable then reasons about things such as:

```text
asset type
condition
damage
identity ambiguity
whether human review is necessary
```

Fable 5.1 is Anthropic's current high-end knowledge-work model. ([Anthropic](https://www.anthropic.com/claude-fable-and-mythos-5-1?frmapp=yes&utm_source=chatgpt.com))

---



# 17. Astra pipeline

The **same evidence package** independently goes to GPT-6 Astra.

Astra supports text/image inputs and structured output workflows, so I'd force it to conform to the same schema. ([OpenAI Developers](https://developers.openai.com/api/docs/models/gpt-6-astra?utm_source=chatgpt.com))

For example:

```json
{
  "asset_type": "painting",
  "condition": "damaged",
  "damage_types": ["surface_crack"],
  "value_required": true,
  "confidence": 0.87
}
```

Crucially:

```text
Fable cannot see Astra's result.
Astra cannot see Fable's result.
```

Otherwise your comparison is contaminated.

---



# 18. Jev's job

Then:

```text
Fable output ─┐
              ├─→ Jev
Astra output ─┘
```

Jev is built around typed probabilistic decisions rather than generated prose. ([TypeSafe AI](https://typesafe.ai/blog/introducing-system-one-models-and-jev?utm_source=chatgpt.com))

So I would ask something like:

```text
ConditionDecision:
 GOOD
 WORN
 DAMAGED
 HUMAN_REVIEW
```

with probabilities.

Example:

```text
DAMAGED       0.76
WORN          0.18
GOOD          0.04
HUMAN_REVIEW  0.02
```

---



# 19. But Jev doesn't become "ground truth"

Very important.

You still maintain manually verified examples.

```text
Prediction
  ↓
Fable
Astra
Jev
  ↓
Human truth
```

Then measure:

```text
Fable accuracy
Astra accuracy
Jev-combined accuracy
```

That's your actual evaluation system.

---



# 20. Property valuation

I would make this completely separate.

RoomPlan tells you:

```text
734.7 m²
2 floors
wall/floor dimensions
building layout
```

Then obtain:

```text
country
city
postal code
building class
construction quality
local reconstruction €/m²
HVAC/fire systems
special finishes
age/condition
```

Then deterministic valuation:

$$
BuildingReplacement =
Area \times LocalReplacementRate \times Adjustments
$$

The AI extracts attributes.

The valuation engine calculates money.

---



# 21. Property valuation edge case

Suppose:

```text
RoomPlan = 600 m²
```

but there's an inaccessible basement.

Don't silently value 600 m² as the entire property.

Store:

```text
measured_area = 600
survey_coverage = PARTIAL
excluded_area = basement
```

Final report:

> Building valuation based only on 600 m² of captured space.

That's the sort of detail insurers care about.

---



# 22. Pricing books

Book:

```text
ISBN
 ↓
BookPriceProvider
 ↓
country-specific listings
 ↓
remove obvious outliers
 ↓
condition adjustment
 ↓
replacement estimate
```

I would build:

```ts
BookPriceProvider
├── MarketplaceProvider
├── CatalogueProvider
├── CachedProvider
└── ManualProvider
```

So the whole system doesn't collapse if one marketplace changes its site.

For automated marketplace access, I'd only use sources/methods permitted by the provider rather than engineering around access controls.

---



# 23. Rare books

Amazon prices can be nonsense for rare/out-of-print inventory.

Example:

```text
Normal book:
median replacement listings → usable

Rare first edition:
one seller = ₹4 lakh

→ do NOT automatically value
→ HIGH_VALUE_REVIEW
```

The valuation engine should have outlier thresholds.

---



# 24. Network failure

The scanning app should work offline.

Store locally:

```text
video
RoomPlan data
audio
annotations
metadata
```

Then queue upload when connectivity returns.

Every upload should be resumable.

You don't want a 15-minute scan disappearing at 99%.

---



# 25. Processing failure

Backend jobs should be BullMQ jobs:

```text
extractFrames
detectAssets
trackAssets
runOCR
resolveBooks
runFable
runAstra
runJev
priceAssets
generateReport
```

Each job is idempotent.

So if `runOCR` crashes:

```text
retry runOCR
```

rather than restarting the entire survey.

---



# 26. Tools I'd actually choose


| Layer                 | Tool                                                   |
| --------------------- | ------------------------------------------------------ |
| iOS                   | Swift + SwiftUI                                        |
| Room geometry         | RoomPlan                                               |
| Device pose           | ARKit                                                  |
| Video/audio           | AVFoundation                                           |
| On-device OCR         | Apple Vision                                           |
| Barcode               | Vision                                                 |
| CV experiments        | Python + OpenCV                                        |
| Object detection      | YOLO-family model initially                            |
| Tracking              | ByteTrack/BoT-SORT-style tracker                       |
| Visual embeddings     | CLIP/SigLIP-style encoder                              |
| API                   | TypeScript + Fastify/NestJS                            |
| ORM                   | Prisma                                                 |
| DB                    | Redis (durable records have no TTL; caches have explicit TTL) |
| Queue                 | Redis + BullMQ                                         |
| Media                 | S3-compatible storage                                  |
| Heavy ML              | Python workers                                         |
| Deep reasoning        | Fable                                                  |
| Independent reasoning | Astra                                                  |
| Decision arbitration  | Jev                                                    |
| 2D plan               | Tagged SVG (numbered walls, openings, cm; N = scan +Z) |
| 3D                    | RoomPlan/USDZ                                          |
| Monitoring            | OpenTelemetry + Sentry                                 |
| Evaluation            | Python notebooks/scripts + Redis                       |


So the full system becomes:

```text
                        iPhone
                           │
        ┌──────────────────┼──────────────────┐
        │                  │                  │
    RoomPlan            Camera             Audio
        │                  │                  │
        └──────────────────┼──────────────────┘
                           ↓
                    Raw Evidence
                           ↓
                 Quality Validation
                           ↓
       ┌───────────────────┼───────────────────┐
       ↓                   ↓                   ↓
   Geometry            Detection             OCR
                           ↓
                       Tracking
                           ↓
                     Deduplication
                           ↓
                      Survey IR
                           │
               ┌───────────┴───────────┐
               ↓                       ↓
             Fable                   Astra
               └───────────┬───────────┘
                           ↓
                          Jev
                           ↓
              ┌────────────┴─────────────┐
              ↓                          ↓
       Asset Valuation           Building Valuation
              └────────────┬─────────────┘
                           ↓
                   Human Review
                           ↓
                Insurance Report
```

The important part is that **Fable/Astra/Jev sit fairly late in the architecture**. Most raw perception, tracking, geometry, OCR, counting and arithmetic should happen without an expensive frontier model.

That simultaneously makes the system **cheaper, faster, explainable and much easier to evaluate**.

And the first nasty benchmark I would build is deliberately adversarial: scan a shelf once, scan it backwards again, place two copies of the same ISBN in different shelves, partially hide one book, make one spine unreadable, and verbally mark one painting as damaged. If the system handles that correctly, you have proven far more than a clean happy-path demo.
