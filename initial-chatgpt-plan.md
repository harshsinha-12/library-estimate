I would build this as a **physical-world evidence system**, not as "an app that sends a video to an LLM."

The guiding principle would be:

> **Capture once, preserve raw evidence, extract observations deterministically where possible, let AI reason only where necessary, and make every final valuation traceable back to evidence.**

Below is the exact order I would follow.

---

# 0. Freeze the actual problem before writing code

The product ultimately needs to produce four things:

```text
1. Geometry
   What is the building/room?

2. Inventory
   What physical assets exist?

3. Condition
   What condition/damage does each asset have?

4. Valuation
   What is the replacement/insured value?
```

Everything else supports those four outputs.

For the MVP, I would define:

```text
Supported:
- iPhone/iPad LiDAR scan
- video capture
- photographs
- spoken annotations
- written annotations
- multiple rooms
- books
- paintings/portraits
- furniture
- generic miscellaneous objects
- damage annotations
- book valuation
- building reconstruction valuation
- 2D floor plan
- 3D representation
- Fable output
- Astra output
- Jev evaluation
```

I would explicitly **not** promise:

```text
- perfect recognition of every book from 5 metres away
- exact art appraisal from an image
- exact market value of real estate from RoomPlan
- 100% automatic ISBN recognition
- fully autonomous claims approval
```

That saves you from designing around impossible assumptions.

---

# 1. Establish one canonical data model first

This is probably the most important architectural decision.

Do not let RoomPlan, Fable, Astra, your CV pipeline and your frontend invent their own formats.

Create something like:

# `Survey IR v0.1`

Think of it as your equivalent of the FloorPlan IR from the Cozmo project, except now it represents **the building plus its contents and evidence**.

At the highest level:

```json
{
  "survey_id": "survey_123",

  "property": {},
  "rooms": [],
  "assets": [],
  "observations": [],
  "annotations": [],
  "valuations": [],
  "model_runs": [],
  "evaluations": []
}
```

---

# 2. Separate an Asset from an Observation

This solves a huge number of problems later.

Suppose you see one copy of Clean Code six times while walking past it.

You should have:

```text
1 Asset
6 Observations
```

Not:

```text
6 Assets
```

Your database therefore needs something like:

```json
{
  "asset_id": "asset_873",

  "category": "book",

  "room_id": "room_02",

  "location": {
    "shelf_id": "shelf_07",
    "position_xyz": [1.25, 1.72, -2.31]
  },

  "identity": {
    "isbn": "9780132350884",
    "title": "Clean Code",
    "author": "Robert C. Martin"
  },

  "condition": {
    "label": "good",
    "confidence": 0.91
  },

  "observation_ids": [
    "obs_1001",
    "obs_1029",
    "obs_1055"
  ]
}
```

And then:

```json
{
  "observation_id": "obs_1055",

  "asset_id": "asset_873",

  "timestamp_ms": 142920,

  "frame_id": "frame_1429",

  "camera_pose": {},

  "bounding_box": {},

  "ocr": {},

  "image_crop_url": "...",

  "confidence": 0.88
}
```

This distinction makes deduplication tractable.

---

# 3. Design the iOS capture experience

The app should not just have a camera record button.

I'd give the user a guided survey workflow.

## Screen 1: Create Survey

User enters:

```text
Property:
Biblioteca Example

Country:
Italy

City:
Milan

Address:
...

Survey type:
Commercial Library

Currency:
EUR
```

These geographic details become crucial later for valuation.

---

# 4. Start the scan session

Tap:

> Start Survey

Generate:

```text
survey_id
session_id
room_id
device_id
start_timestamp
```

Immediately start collecting multiple synchronized streams.

```text
                SURVEY SESSION
                      |
     +----------------+----------------+
     |                |                |
     v                v                v
  RoomPlan          Camera           Audio
     |                |                |
     +----------------+----------------+
                      |
                  timestamps
```

Everything needs the same timeline.

---

# 5. RoomPlan runs the spatial layer

RoomPlan gives you things such as rooms, floors, walls, windows, doors, openings and detected objects, and Apple supports combining multiple room scans into a `CapturedStructure`. ([Apple Developer][1])

So you capture:

```text
Room
Walls
Floor
Ceiling assumptions
Doors
Windows
Openings
Recognized furniture
Dimensions
Transforms
```

Store both:

```text
Raw CapturedRoomData
Processed CapturedRoom
```

Apple allows the raw results to be serialized and processed later, which is useful because **you should never throw away your original scan evidence**. ([Apple Developer][2])

---

# 6. Record RGB video separately

RoomPlan's representation isn't enough for recognizing individual books, artwork damage, text, ISBNs, etc.

So while RoomPlan is scanning, you need access to visual frames.

Conceptually:

```text
AR Session
   |
   +-> RoomPlan
   |
   +-> RGB frame stream
   |
   +-> camera transform
```

For each usable video frame, persist metadata:

```json
{
  "frame_id": "f_02991",
  "timestamp": 193.718,
  "camera_transform": [],
  "intrinsics": [],
  "resolution": [3840, 2160]
}
```

You don't necessarily upload every raw frame independently.

Store the original video plus selected frames.

---

# 7. Capture the camera pose with every useful frame

This is critical.

Without spatial information:

```text
Book appears in frame 100
Book appears in frame 320

Are these the same book?
¯\_(ツ)_/¯
```

With camera/world geometry:

```text
Frame 100:
object ~= world coordinate (3.4, 1.3, 7.2)

Frame 320:
object ~= world coordinate (3.42, 1.29, 7.18)

Likely same physical asset
```

Now your deduplication gets dramatically stronger.

---

# 8. Record audio continuously

Suppose the user says:

> "This painting has a crack on the bottom-right."

Store:

```text
Audio segment
00:02:41.180 -> 00:02:44.900
```

Run speech-to-text:

```json
{
  "text": "This painting has a crack on the bottom-right.",
  "start": 161.18,
  "end": 164.90
}
```

Because video, pose and audio share timestamps, you can ask:

> What was the user looking at between 161 and 165 seconds?

That's how speech gets connected to an actual physical object.

---

# 9. Written notes use the same mechanism

If the user taps an object and writes:

> "Original signed painting."

Store:

```json
{
  "annotation_id": "...",
  "timestamp": "...",
  "asset_id": "...",
  "room_id": "...",
  "text": "Original signed painting"
}
```

Don't just store free-floating notes.

Everything should either be:

```text
Property-level
Room-level
Asset-level
```

---

# 10. Do lightweight processing on-device

You don't want to upload a 15-minute 4K video and process every frame using Fable.

That will destroy your $50 budget.

Use the device for cheap operations.

Apple's Vision framework already supports things including text recognition, barcode detection, classification, segmentation and visual similarity-related tasks. ([Apple Developer][3])

I'd initially do:

```text
Blur detection
Exposure check
Barcode detection
OCR
Frame quality scoring
Scene change detection
```

Potentially some object detection later.

---

# 11. Implement scan quality warnings

During the scan, don't wait until the end to discover everything was blurry.

Calculate:

```text
motion blur
brightness
camera speed
distance from shelf
coverage
RoomPlan confidence
```

Then UI messages like:

```text
Slow down

Move closer to books

Shelf partially scanned

Poor lighting

Room coverage 74%

ISBN unreadable
```

This dramatically improves your final results.

---

# 12. Upload architecture

I would not send media through your normal API server.

Use:

```text
iPhone
   |
   | Request upload URLs
   v
Backend
   |
   v
Signed Upload URLs

iPhone
   |
   +---------------------> Object Storage
                             video.mov
                             audio.m4a
                             roomplan.json
                             images/
                             metadata.json
```

Then:

```text
POST /surveys/{id}/complete
```

tells the backend:

> All capture data has arrived. Start processing.

---

# 13. Suggested backend stack

Given your existing stack, I'd use:

```text
API / orchestration
TypeScript
Fastify or NestJS

Database
PostgreSQL + Prisma

Queue
Redis + BullMQ

Media
S3-compatible object storage

CV / ML workers
Python + FastAPI

Mobile
Swift + SwiftUI

Spatial
RoomPlan + ARKit

Local CV
Vision + CoreML where useful
```

Why two backend languages?

Because:

```text
TypeScript
= APIs, orchestration, state machine, database, jobs

Python
= OpenCV, PyTorch, OCR experiments, CV models
```

Don't force computer vision into TypeScript just for architectural purity.

---

# 14. Survey processing becomes a DAG

Once upload completes:

```text
survey.uploaded
      |
      v
normalize_media
      |
      +-------------------+
      |                   |
      v                   v
process_roomplan     process_video
      |                   |
      |              extract_keyframes
      |                   |
      |              detect_assets
      |                   |
      |              track_assets
      |                   |
      |              OCR / ISBN
      |                   |
      +---------+---------+
                |
                v
          build_asset_graph
                |
       +--------+---------+
       |                  |
       v                  v
    Fable             Astra
       |                  |
       +--------+---------+
                |
                v
              Jev
                |
                v
           valuation
                |
                v
             report
```

BullMQ fits this very naturally.

---

# 15. Don't analyze every video frame

30 FPS for 10 minutes:

$$
30 \times 60 \times 10
=
18,000\ frames
$$

Sending 18,000 frames to frontier models would be absurd.

First extract **useful frames**.

Example:

```text
18,000 original frames
        ↓
remove blurred frames
        ↓
remove near duplicates
        ↓
scene/keyframe extraction
        ↓
maybe 300 frames
        ↓
object tracking
        ↓
asset crops
        ↓
perhaps 500-1,000 useful crops
```

Only ambiguous/high-value cases should reach expensive reasoning models.

---

# 16. Create a Frame Quality Score

For every candidate frame:

```text
Q =
w1 * sharpness
+ w2 * exposure
+ w3 * object_visibility
+ w4 * text_readability
- w5 * motion_blur
```

For an asset track:

```text
Asset #123 seen in:

Frame 51  Q=.42
Frame 54  Q=.67
Frame 58  Q=.93
Frame 61  Q=.81
```

Use frame 58 as the primary evidence image.

Keep the others available.

---

# 17. Detect shelves first

For books, don't immediately think:

> Detect all books globally.

Structure the scene hierarchically:

```text
Room
 ↓
Shelf unit
 ↓
Shelf level
 ↓
Book spine
 ↓
Physical book
```

For example:

```text
Room 3
  Shelf Unit 7
    Row 1
      book_001
      book_002
    Row 2
      book_003
```

Now physical location helps identity.

---

# 18. Assign shelves spatial IDs

Example:

```text
room_01.shelf_04.row_03
```

And store its approximate 3D bounds.

Now when the same book appears again:

```text
ISBN same
Visual appearance same
Shelf same
World position same

→ duplicate observation
```

But:

```text
ISBN same
Shelf different
World position different

→ different physical copy
```

---

# 19. Book detection

The first CV stage should identify book-spine regions.

Output:

```json
{
  "frame": "frame_821",
  "detections": [
    {
      "class": "book_spine",
      "bbox": [421, 88, 487, 620],
      "confidence": 0.94
    }
  ]
}
```

Don't worry about title yet.

Detection asks:

> Is there a book here?

Recognition asks:

> Which book is this?

Those should remain separate.

---

# 20. Book tracking

Track detections over consecutive frames.

```text
frame 100 -> book detection
frame 101 -> same object
frame 102 -> same object
...
frame 126 -> same object
```

Assign:

```text
track_id = track_book_884
```

This prevents counting the same object 20 times simply because it appeared in 20 frames.

---

# 21. Multi-frame recognition

Now collect:

```text
track_book_884
    |
    + frame 101
    + frame 106
    + frame 110
    + frame 115
```

Choose the best crops.

Run OCR on all of them.

Suppose:

```text
Frame 101:
DESIGNING DAT...

Frame 106:
DESIGNING DATA INTENSIVE

Frame 110:
MARTIN KLEPPMANN
```

Merge evidence:

```text
Title:
Designing Data-Intensive Applications

Author:
Martin Kleppmann
```

Much better than single-frame OCR.

---

# 22. ISBN recognition should have multiple levels

### Level A: Visible barcode

Best case.

```text
Vision barcode detector
→ ISBN-13
```

### Level B: ISBN printed as text

OCR:

```text
ISBN 978-...
```

### Level C: Spine identity

Use:

```text
title
author
publisher
edition information
```

Then search metadata and obtain candidate ISBNs.

### Level D: Uncertain

Return:

```text
Needs Verification
```

Never pretend certainty.

---

# 23. Add a manual verification mode

This is important for the demo.

After scanning:

```text
482 books scanned

451 confidently identified
19 partially identified
12 unresolved
```

Tap:

> Review unresolved

App shows:

```text
Book #173
[spine image]

Possible:
1. Clean Architecture
2. Clean Code
3. Unknown

[Scan barcode]
```

User walks up and scans the barcode.

Now identification is definitive.

That's realistic product engineering.

---

# 24. Deduplication

I'd make deduplication a scored decision.

For observations \(A\) and \(B\):

$$
P(same) =
f(
spatialDistance,
visualSimilarity,
ISBN,
shelf,
trajectory,
time
)
$$

Example:

```text
ISBN match              +0.25
same shelf              +0.20
distance < 10 cm        +0.30
visual embedding match  +0.20
continuous track        +0.30
```

Then:

```text
score > 0.85
→ auto merge

0.60 - 0.85
→ evaluator/model

<0.60
→ separate assets
```

Do not make an LLM decide every duplicate.

---

# 25. Detect non-book assets separately

Your detector taxonomy might start with:

```text
book
painting
portrait
sculpture
computer
monitor
printer
table
chair
shelf
cabinet
lamp
electronics
decorative_object
cup
other
```

Then classification:

```text
Asset
 ↓
potentially insurable?
 ↓
yes / no / uncertain
```

A coffee mug may still be inventoried but receive:

```text
valuation_required = false
```

---

# 26. Don't discard zero-value objects

This is subtle.

Suppose insurance rules say mugs don't matter.

Still record:

```json
{
  "category": "cup",
  "count": 14,
  "valuation_required": false
}
```

Why?

Because you want your system to prove:

> I saw them and deliberately excluded them.

Rather than:

> The model might have failed to see them.

Huge difference in auditability.

---

# 27. Damage classification

Damage needs its own schema.

Example:

```json
{
  "damage_id": "damage_91",

  "asset_id": "painting_12",

  "damage_type": "surface_crack",

  "severity": "moderate",

  "region": {
    "description": "bottom-right",
    "bbox": []
  },

  "sources": [
    "vision",
    "spoken_annotation"
  ],

  "confidence": 0.91
}
```

---

# 28. Use spoken comments as evidence, not truth

Suppose surveyor says:

> "This book is badly damaged."

Don't immediately set:

```text
condition = badly damaged
```

Instead:

```text
Human annotation:
claimedCondition = damaged

CV:
condition = worn
confidence=.71

Fable:
condition=damaged
confidence=.82

Astra:
condition=damaged
confidence=.89
```

Then resolve.

This gives you provenance.

---

# 29. Associate spoken descriptions spatially

For a note at time \(t\):

Find:

```text
camera pose at t
objects visible at t
object nearest image center
recently selected object
semantic match
```

Example:

> "This portrait is damaged."

At that timestamp:

```text
Image center:
portrait_19

Other objects:
chair_18
bookshelf_7
```

High probability the annotation belongs to `portrait_19`.

If uncertain:

```text
annotation.asset_id = null
status = NEEDS_REVIEW
```

---

# 30. Property geometry

Now separately build the building model.

RoomPlan gives you structured surfaces like walls, doors, windows, openings and floors, and can export 3D results such as USDZ. ([Apple Developer][1])

Convert that into your internal geometry:

```text
Property
 ├── floor_1
 │    ├── room_1
 │    ├── room_2
 │    └── room_3
 └── floor_2
```

Calculate:

```text
floor area
wall area
room dimensions
number of rooms
number of floors
door/window counts
ceiling estimates
```

---

# 31. Separate 2D and 3D outputs

Your backend IR is the source of truth.

From it generate:

### 2D

```text
SVG
or
vector floor plan
```

Use for:

```text
room labels
asset positions
damage markers
```

### 3D

Use RoomPlan's geometry/USDZ plus your asset annotations.

User can tap:

```text
Shelf 7
→ 47 books
→ estimated replacement €921
```

Very strong demo.

---

# 32. Property valuation is NOT an LLM task

Your geometry engine gives:

```text
602.4 m²
2 floors
commercial library
```

Then valuation requires external data:

```text
Location
Construction class
Local rebuild cost
Finish quality
Age
Building condition
HVAC
Electrical
Fire protection
Special architecture
etc.
```

Then:

$$
ReconstructionValue =
BaseArea \times LocalCostPerM^2
+ Adjustments
$$

Don't prompt Fable:

> How much is this building worth?

That is not defensible.

---

# 33. Create a ValuationProvider abstraction

Something like:

```ts
interface ValuationProvider {
  estimateBuilding(
    geometry,
    location,
    buildingClass
  ): Promise<BuildingValuation>
}
```

Initially you could even use a small manually maintained dataset for the demo:

```json
{
  "IN": {
    "commercial_library": {
      "low": 400,
      "medium": 650,
      "premium": 1000
    }
  }
}
```

Obviously substitute real validated data for production.

Your architecture shouldn't care where it comes from.

---

# 34. Book pricing is another provider

```ts
interface BookPriceProvider {
  lookup({
    isbn,
    country,
    currency,
    condition
  }): Promise<BookPricing>
}
```

Output:

```json
{
  "isbn": "...",

  "market": "IT",

  "offers": [
    31.99,
    34.50,
    29.95
  ],

  "method": "median",

  "replacement_value": 31.99,

  "currency": "EUR",

  "retrieved_at": "..."
}
```

---

# 35. Don't couple the architecture to Amazon scraping

Even if your interviewer specifically mentioned Amazon scraping, implement:

```text
BookPriceProvider
   |
   + AmazonProvider
   + OtherProvider
   + CachedProvider
   + ManualProvider
```

If automated access is blocked or prohibited, don't build bypasses around CAPTCHAs or anti-bot systems. Swap providers.

Engineering-wise, that's also much cleaner.

---

# 36. Cache book prices

A library may contain:

```text
5,000 books
```

But perhaps only:

```text
3,100 unique ISBNs
```

You shouldn't retrieve the same ISBN price repeatedly.

Cache:

```text
pricing:isbn:978...
```

with:

```text
market
currency
timestamp
```

---

# 37. Condition should modify valuation

Maybe:

```text
New        100%
Good        85%
Worn        60%
Damaged     30%
Severe       5%
```

Those factors are just illustrative.

The actual insurer must define them.

Keep them configuration-driven:

```json
{
  "good": 0.85,
  "worn": 0.60
}
```

Not buried in application code.

---

# 38. Separate valuable art from commodity objects

A painting is very different from a normal book.

The system should recognize:

```text
Painting detected
 ↓
Can we identify artist/title?
 ↓
Existing provenance/documentation?
 ↓
Known market value?
 ↓
Requires specialist appraisal?
```

For expensive objects, output:

```text
REQUIRES_APPRAISAL
```

rather than hallucinating `$18,436`.

---

# 39. Now introduce Fable

Only after the evidence pipeline exists.

Fable 5.1 is positioned for complex knowledge work and has vision capabilities. ([Anthropic][4])

Your Fable input should NOT be:

> Here's a 10-minute video, figure everything out.

Give it structured evidence:

```json
{
  "asset_candidate": {...},

  "best_images": [...],

  "ocr": [...],

  "audio_notes": [...],

  "spatial_context": {...},

  "possible_identity": [...]
}
```

Ask for structured output.

Example:

```json
{
  "asset_category": "book",
  "condition": "worn",
  "damage": ["spine_wear"],
  "valuable": true,
  "isbn_candidate": "...",
  "confidence": 0.87
}
```

---

# 40. Run Fable only when it adds value

Don't ask Fable:

> Is barcode `978...` an ISBN?

A deterministic parser can answer that.

Use Fable for:

```text
ambiguous visual classification
condition reasoning
spoken note interpretation
conflicting evidence
unusual objects
damage analysis
complex entity resolution
```

---

# 41. Astra becomes Pipeline B

GPT-6 Astra currently supports text and image input and structured outputs through the API. ([OpenAI Developers][5])

I'd make Astra a separate independent inference path.

```text
Evidence Package
      |
 +----+----+
 |         |
 v         v
Fable    Astra
 |         |
 v         v
Result A Result B
```

Don't show Astra Fable's answer initially.

Otherwise they aren't meaningfully independent.

---

# 42. Your near-real-time path

During capture:

```text
camera
 ↓
sample useful frames
 ↓
local detection
 ↓
high-value/uncertain frame?
 ↓ YES
Astra
 ↓
structured observation
```

For example:

```text
Shelf 3

Current:
Detected physical books: 42
Recognized: 35
Uncertain: 7
```

That gives you the live-demo wow factor.

But the final authoritative result still comes from postprocessing.

---

# 43. Fable can be your deep/batch path

After scanning:

```text
All evidence available
 ↓
best frames
 ↓
OCR
 ↓
spatial information
 ↓
spoken notes
 ↓
metadata
 ↓
Fable
```

That becomes your slower deeper interpretation.

So conceptually:

```text
Astra = live/fast observer

Fable = post-scan deep reasoner
```

Not because they absolutely must behave that way, but because it makes the architecture coherent.

---

# 44. Normalize both outputs

Both should return exactly the same schema.

Example:

```ts
type ModelAssetAssessment = {
  category: AssetCategory;
  condition: Condition;
  damage: DamageType[];
  shouldValue: boolean;
  identity?: BookIdentity;
  confidence: number;
};
```

Then comparison is trivial.

Without standardized outputs, your evaluator becomes spaghetti.

---

# 45. Introduce Jev AFTER both pipelines

Jev is particularly suited to typed probabilistic decisions, with defined output types and probabilities rather than long generated text. ([TypeSafe AI][6])

This is exactly where I would use it.

Example input:

```json
{
  "task": "resolve_asset_condition",

  "fable": {
    "condition": "damaged",
    "confidence": 0.88
  },

  "astra": {
    "condition": "worn",
    "confidence": 0.74
  },

  "ocr": {...},

  "human_note": "damaged spine",

  "vision_score": 0.81
}
```

Jev returns something conceptually like:

```json
{
  "decision": "damaged",
  "probabilities": {
    "new": 0.01,
    "good": 0.04,
    "worn": 0.13,
    "damaged": 0.82
  }
}
```

---

# 46. Jev does not replace evaluation

You still need actual ground truth.

For your demo, manually label a subset:

```text
Actual count
Actual ISBN
Actual condition
Actual damage
Actual room dimensions
Actual value where known
```

Call that:

```text
GroundTruthSet
```

Then compare.

---

# 47. Build an evaluation harness

Metrics should be task-specific.

### Book count

$$
CountError =
\frac{|Predicted-Actual|}{Actual}
$$

### Book identification

```text
ISBN accuracy
title accuracy
author accuracy
```

### Deduplication

```text
precision
recall
false merges
false splits
```

### Damage

```text
precision
recall
F1
```

### Geometry

```text
room dimension error
floor-area error
wall-length error
```

### Valuation

```text
absolute error
percentage error
coverage
```

---

# 48. Make deduplication evaluation first-class

This system can fail horribly while appearing impressive.

Suppose actual:

```text
500 books
```

System outputs:

```text
842 books
```

because it double-counted them.

Recognition accuracy becomes irrelevant.

So I'd track:

```text
Duplicate Rate
Missed Asset Rate
False Merge Rate
```

very prominently.

---

# 49. Add model disagreement metrics

For every field:

```text
Fable == Astra?
```

Measure:

```text
category agreement
condition agreement
identity agreement
damage agreement
valuation eligibility agreement
```

Example dashboard:

```text
Fable/Astra agreement: 91.3%

Disagreement:
Condition      5.2%
ISBN           1.4%
Damage         2.1%
Asset type     0.7%
```

Then Jev's utility becomes measurable.

---

# 50. Your "RL feedback loop" starts as logged feedback

Don't jump immediately into online reinforcement learning.

First:

```text
Prediction
 ↓
Jev decision
 ↓
Human review
 ↓
Correct answer
 ↓
Feedback dataset
```

Record:

```json
{
  "state": {...},

  "fable_action": {...},

  "astra_action": {...},

  "jev_action": {...},

  "human_truth": {...},

  "reward": 1
}
```

Over time this becomes training/evaluation data.

---

# 51. Reward can be task-specific

For example:

```text
Correct asset detection       +1
Wrong asset detection         -1

Correct ISBN                  +2
Wrong ISBN                    -2

Duplicate book counted        -3

Missed valuable artwork       -5

Correct exclusion of mug      +0.2
```

In insurance, different errors have different costs.

Missing a €100,000 painting is much worse than counting one extra mug.

Your reward function should reflect that.

---

# 52. Eventually train your own cheap specialist models

The long-term architecture shouldn't call Fable/Astra forever for every tiny choice.

Once you have enough labelled examples:

```text
Fable/Astra/Jev
      ↓
human corrections
      ↓
training data
      ↓
small specialist models
```

You can train things like:

```text
book condition classifier
asset-value eligibility classifier
damage classifier
duplicate resolver
frame quality classifier
```

Then frontier models only handle edge cases.

That's where your unit economics become interesting.

---

# 53. Backend state machine

A survey should move through states:

```text
CREATED
 ↓
CAPTURING
 ↓
UPLOADING
 ↓
PROCESSING_GEOMETRY
 ↓
PROCESSING_ASSETS
 ↓
IDENTIFYING
 ↓
VALUING
 ↓
EVALUATING
 ↓
NEEDS_REVIEW
 ↓
COMPLETE
```

Never just have:

```text
processing: true
```

You will regret that instantly.

---

# 54. Make every pipeline idempotent

If:

```text
identify_books
```

crashes halfway through, rerunning it should not create 2x as many assets.

Every job should use stable keys:

```text
survey_id
frame_id
track_id
asset_id
pipeline_version
```

And preferably database upserts.

This matters hugely with BullMQ retries.

---

# 55. Version everything

Store:

```text
roomplan_version
detector_version
ocr_version
fable_model
astra_model
jev_version
prompt_version
pricing_algorithm_version
valuation_dataset_version
```

Why?

Insurance output needs reproducibility.

Six months later someone may ask:

> Why was this painting marked damaged?

You need to know exactly which model and evidence produced that answer.

---

# 56. Evidence provenance should be everywhere

Every final assertion:

```text
Clean Code
Condition: damaged
Value: €24
```

should support:

> Show evidence

Then display:

```text
Frame 1282
Frame 1287
OCR output
Barcode
Spoken annotation
Fable result
Astra result
Jev resolution
Pricing source
```

This is one of the strongest aspects of the entire architecture.

---

# 57. Human review should be confidence-driven

Don't make people inspect everything.

Use thresholds:

```text
confidence > .95
AUTO_ACCEPT

.70 - .95
ACCEPT_WITH_FLAG

< .70
HUMAN_REVIEW
```

And different thresholds by asset value:

```text
€5 book
lower review threshold requirement

€50,000 painting
very high confidence required
```

Risk-based review is much smarter.

---

# 58. Database structure

Roughly:

```text
Survey
Property
Floor
Room
Shelf
Asset
Observation
Frame
Media
Annotation
Damage
IdentityCandidate
AssetAssessment
ModelRun
ModelPrediction
Valuation
Evaluation
Review
```

Relationships:

```text
Survey
  └ Property
      └ Rooms
          └ Assets
              └ Observations
                  └ Frames
```

---

# 59. Media should stay outside Postgres

Postgres stores:

```text
URLs
metadata
hash
dimensions
timestamps
```

Object storage stores:

```text
.mov
.jpg
.png
.m4a
.usdz
```

Do not shove video blobs into Postgres.

---

# 60. Add hashes

For every uploaded file:

```text
SHA-256
```

Store:

```text
original_hash
```

Then you can prove:

> This image/report corresponds to the original captured evidence.

Again, very useful for insurance/auditing.

---

# 61. The mobile app after scanning

I would show four major tabs.

```text
Overview
Floor Plan
Inventory
Review
```

### Overview

```text
Rooms: 4
Floor area: 648 m²
Assets: 5,291
Books: 5,103

Estimated Contents Value:
€83,411

Estimated Building Replacement:
€1.42M

Needs Review:
17 assets
```

---

# 62. Floor Plan

2D:

```text
+------------------------------+
|                              |
| SHELF A          PAINTING X  |
|                              |
|                              |
|          TABLE               |
|                              |
+------------------------------+
```

Tap painting:

```text
Portrait #7

Condition:
Damaged

Damage:
lower-left surface

Evidence:
[image]

Value:
Requires appraisal
```

---

# 63. 3D view

Use the RoomPlan output as the base model.

Overlay:

```text
assets
damage indicators
room labels
shelf locations
```

You don't need photorealistic NeRF-quality reconstruction for the MVP.

That's unnecessary scope creep.

---

# 64. Inventory screen

Something like:

| Asset     | Count | Condition |    Value | Confidence |
| --------- | ----: | --------- | -------: | ---------: |
| Books     | 5,103 | Mixed     |  €81,400 |        94% |
| Paintings |     7 | Mixed     |   Review |        81% |
| Computers |    12 | Good      |   €8,900 |        96% |
| Cups      |    21 | N/A       | Excluded |        98% |

Then drill down.

---

# 65. Review queue

This is very important.

```text
17 items need attention

Book #812
ISBN ambiguous
[Resolve]

Painting #7
Value unavailable
[Add appraisal]

Book #1192
Possible duplicate
[Merge] [Keep Separate]

Portrait #2
Damage disagreement
[Review Evidence]
```

This demonstrates that your system understands uncertainty rather than hallucinating certainty.

---

# 66. Final report

Generate something like:

```text
Property Details
Survey Metadata

Building Measurements
Room Breakdown
2D Floor Plan
3D Model Reference

Inventory Summary
Book Inventory
High-Value Assets

Damage Summary

Building Replacement Estimate
Contents Replacement Estimate

Unresolved Items

Methodology

Evidence References
Model Versions
```

Ideally PDF plus JSON.

---

# 67. The $50 budget strategy

The key is **model sparsity**.

Don't spend frontier-model money on:

```text
barcode parsing
OCR cleanup
coordinates
simple duplicates
room dimensions
arithmetic
```

Do those locally/deterministically.

Use expensive models for:

```text
uncertain condition
ambiguous identity
damage reasoning
object semantics
conflicting evidence
```

Current API listings put both Fable 5.1 and Astra at the high end of frontier-model pricing, with Fable 5.1 at $10/M input and $50/M output and Astra also listed at $10/M input and $50/M output, so aggressive preprocessing matters. ([Anthropic][7])

Jev is positioned as substantially cheaper for its typed decision workload. ([TypeSafe AI][6])

---

# 68. The exact order I would actually implement it

I would make the implementation sequence:

### Milestone 1 - Geometry only

Build iOS app:

```text
Start scan
→ RoomPlan
→ Stop
→ Save CapturedRoom
→ Show 3D
→ Generate simple 2D floor plan
```

Nothing else.

Verify geometry first.

---

### Milestone 2 - Multimedia capture

Add:

```text
video
photos
audio
written notes
timestamps
camera pose
```

Prove that everything aligns temporally.

---

### Milestone 3 - Backend upload

Build:

```text
create survey
signed URLs
upload media
complete survey
job queue
```

Verify one complete scan safely reaches storage.

---

### Milestone 4 - Frame extraction

Implement:

```text
video
→ keyframes
→ sharpness ranking
→ thumbnails
```

Create a page where you can visually inspect extracted frames.

---

### Milestone 5 - Basic book detection

Ignore ISBN.

Only solve:

> How many visible physical books can we identify?

Get tracking working.

---

### Milestone 6 - Deduplication

Scan the same shelf:

```text
left → right
right → left
```

If actual books = 50:

```text
system should still say ~50
not ~100
```

I would treat this as your first serious benchmark.

---

### Milestone 7 - OCR

Add:

```text
spine OCR
title
author
publisher
```

Store raw OCR, not just cleaned text.

---

### Milestone 8 - ISBN resolution

Add:

```text
barcode
visible ISBN
metadata inference
manual correction
```

Now you have actual book identity.

---

### Milestone 9 - Pricing

Add:

```text
ISBN
→ pricing provider
→ country
→ price
→ condition adjustment
```

Cache aggressively.

---

### Milestone 10 - Other objects

Add:

```text
paintings
furniture
electronics
mugs
```

Classify:

```text
value
ignore
manual appraisal
```

---

### Milestone 11 - Damage

Add visual/spoken damage flow.

Test:

> "This portrait has damage in the lower-left."

Then verify:

```text
correct asset
correct annotation
correct evidence frame
```

---

### Milestone 12 - Fable

Introduce Fable on selected ambiguous asset packages.

Measure its results independently.

---

### Milestone 13 - Astra

Run the same standardized evidence against Astra.

Keep its outputs independent.

---

### Milestone 14 - Jev

Give Jev:

```text
Fable result
Astra result
deterministic evidence
```

Return:

```text
chosen result
probability
human review flag
```

---

### Milestone 15 - Evaluation

Create manually labelled ground truth.

Run:

```text
Fable
Astra
Jev-combined
```

Compare all three.

This gives you an actual research/evaluation story instead of:

> "Look, three AI APIs!"

---

### Milestone 16 - Property valuation

Once geometry works:

```text
RoomPlan
→ total floor area
→ building type
→ location
→ cost provider
→ reconstruction estimate
```

Keep this separate from contents valuation.

---

### Milestone 17 - Final UI

Now build the polished app:

```text
Scan
Processing
Overview
2D
3D
Inventory
Review
Report
```

Do UI polish last.

---

# 69. What I would demo to the interviewer

I would intentionally prepare a controlled mini-library setup:

```text
50-100 books
2 identical copies of one title
1 book scanned twice deliberately
1 damaged book
1 painting
1 visibly damaged painting/object
1 mug
1 laptop
1 table
1 shelf
```

This lets you explicitly demonstrate every difficult problem.

During the demo:

```text
Start survey.

Walk around.

Say:
"This portrait is damaged in the lower-right."

Scan Shelf A.

Walk backwards past Shelf A again.

Scan Shelf B containing another copy of one book.

Finish.
```

Then show:

```text
2D room generated
3D room generated

48 physical books
43 ISBN resolved
5 need verification

Duplicate scanning did NOT double count Shelf A.

Two separate copies of Clean Code counted correctly.

Painting linked to spoken damage annotation.

Mug detected but excluded from valuation.

Contents valuation calculated.

Building replacement estimate calculated.

Fable and Astra predictions visible.

Jev disagreements/evaluation visible.
```

That would be a seriously compelling demo.

---

# 70. The architecture I'd ultimately present

```text
                         iOS SURVEY APP
                              |
        +---------------------+----------------------+
        |                     |                      |
        v                     v                      v
     RoomPlan             RGB Video             Audio/Notes
        |                     |                      |
        +---------------------+----------------------+
                              |
                              v
                     RAW EVIDENCE STORE
                              |
                              v
                   NORMALIZATION PIPELINE
                              |
        +---------------------+----------------------+
        |                     |                      |
        v                     v                      v
   Spatial Model        Visual Pipeline        Speech Pipeline
        |                     |                      |
        |                 Detection                  |
        |                 Tracking                   |
        |                 OCR / ISBN                 |
        |                 Damage                     |
        |                     |                      |
        +---------------------+----------------------+
                              |
                              v
                         SURVEY IR
                              |
                 +------------+------------+
                 |                         |
                 v                         v
             FABLE 5.1               GPT-6 ASTRA
                 |                         |
                 +------------+------------+
                              |
                              v
                             JEV
                              |
                       +------+------+
                       |             |
                       v             v
                    ACCEPT       HUMAN REVIEW
                       |             |
                       +------+------+
                              |
                              v
                     VALUATION ENGINE
                    /                 \
                   v                   v
              CONTENTS             BUILDING
              valuation            valuation
                   \                  /
                    +--------+-------+
                             |
                             v
                     INSURANCE REPORT
                             |
                    +--------+--------+
                    |        |        |
                    v        v        v
                   2D       3D     Inventory
```

And the deepest principle behind all of it is:

> **RoomPlan tells you where things are.**
>
> **Computer vision tells you what appears to be there.**
>
> **Tracking tells you whether you've already seen it.**
>
> **OCR/barcodes tell you identity when available.**
>
> **Fable and Astra reason over ambiguous evidence.**
>
> **Jev resolves structured decisions and uncertainty.**
>
> **External market/cost data determines value.**
>
> **Humans resolve the cases where confidence is insufficient.**

That is the architecture I would defend in an interview because every model has a clear job instead of "AI magically watches a video."

The first thing I would personally implement tomorrow morning is **not ISBN or Fable**. I would build the iOS capture prototype that can simultaneously produce **RoomPlan geometry + RGB evidence + timestamps + camera poses**, because virtually every downstream capability depends on getting that evidence layer right. ([Apple Developer][1])

And here's the question I'd use to check that the architecture has clicked: **why do you think I am treating `Observation` and `Asset` as two different entities instead of simply creating one database row every time a book detector sees a book?**

[1]: https://developer.apple.com/documentation/roomplan/capturedroom?changes=_2_1_4_5&language=objc&utm_source=chatgpt.com "CapturedRoom | Apple Developer Documentation"
[2]: https://developer.apple.com/documentation/roomplan/capturedroomdata?changes=latest_b__8&utm_source=chatgpt.com "CapturedRoomData | Apple Developer Documentation"
[3]: https://developer.apple.com/documentation/vision?featured_on=pythonbytes&utm_source=chatgpt.com "Vision | Apple Developer Documentation"
[4]: https://www.anthropic.com/claude-fable-and-mythos-5-1?frmapp=yes&utm_source=chatgpt.com "Introducing Claude Fable 5.1 and Claude Mythos 5.1 \ Anthropic"
[5]: https://developers.openai.com/api/docs/models/gpt-6-astra?utm_source=chatgpt.com "GPT-6 Astra Model | OpenAI API"
[6]: https://typesafe.ai/blog/introducing-system-one-models-and-jev?utm_source=chatgpt.com "Introducing System One Models & Jev - TypeSafe AI Blog"
[7]: https://www.anthropic.com/claude/fable?utm_source=chatgpt.com "Claude Fable \ Anthropic"
