# `src/domains/vision/` — production hole-detection domain

Self-contained Django domain that ports the locked cv/ spike (commit `76f6fc4`)
into production: deterministic geometry + a vision-language detector behind a
strategy seam. Two invocation paths share the same `services` code:

- **Standalone CLI** — pure Python, no Django required.
- **Django / q2 production path** — `ScoringJob` ORM row + async task via
  django-q2's ORM broker (SQLite-backed, per AGENTS.md §2).

The `cv/` sandbox stays frozen as a historical reference — this domain has
**zero runtime imports from `cv/`** (CI-enforced by
`tests/test_no_cv_imports.py`).

## Prerequisites

- **Images**: the CLI takes one or more image **paths** (relative to repo root
  or absolute). A small 4-image versioned fixture ships with the repo at
  `src/domains/vision/tests/fixtures/` (ids 12, 46, 29, 21 + their
  `_marked.jpg` siblings). Use any of your own images the same way.
- **Optional `<stem>_marked.jpg` sibling**: when present in the same directory
  as the image, AdaptiveFrameSizer enlarges the warp canvas to fit all GT
  holes (no content crop). Pass `--no-gt` to disable the lookup.
- For the **Google** detector: `GOOGLE_API_KEY` in env or `.env`.
- For the **Ollama** detector: local `ollama serve` + a pulled model
  (`gemma4:latest` by default).
- The **mock** detector needs no external services — useful for plumbing tests.

## CLI usage

Run from the **repository root** (`./`). All commands use `uv run`.

### Quick start

```bash
# Fastest end-to-end check — mock detector on the versioned fixture, no API calls
uv run python -m src.domains.vision src/domains/vision/tests/fixtures/12.jpg \
    --detector mock --out /tmp/vision_test

# Live Google AI Studio on the 4 versioned fixtures (locked model: gemini-3.5-flash-lite)
uv run python -m src.domains.vision \
    src/domains/vision/tests/fixtures/12.jpg \
    src/domains/vision/tests/fixtures/46.jpg \
    src/domains/vision/tests/fixtures/29.jpg \
    src/domains/vision/tests/fixtures/21.jpg \
    --detector google --eval

# Local Ollama (lower fidelity, free, offline)
uv run python -m src.domains.vision path/to/your/image.jpg \
    --detector ollama --out /tmp/vision_ollama

# Process your own image (the CLI doesn't care where it lives)
uv run python -m src.domains.vision ~/Pictures/target_001.jpg --detector google
```

### Full argument reference

```bash
uv run python -m src.domains.vision <IMAGE_PATH>... [OPTIONS]
```

| Flag / arg | Default | Description |
|---|---|---|
| `IMAGE_PATHS` (positional, ≥1 required) | — | One or more image paths (relative or absolute) to process. |
| `--detector` | `google` | One of `google`, `ollama`, `mock`. |
| `--target-type` | `air_pistol` | One of `air_pistol`, `precision_pistol`. |
| `--caliber` | (none) | Primary caliber hint (e.g. `9mm`, `22lr`). When `--eval` is set and `--caliber` is absent, the CLI reads `metadata.yml`'s value matched by image stem. |
| `--out` | `resources/train/intermediate_vision` | Output directory (created if absent). |
| `--no-gt` | (off) | Disable AdaptiveFrameSizer's GT-aware margin (skip the `<stem>_marked.jpg` sibling lookup). |
| `--debug` | (off) | Also write the 14-file Phase-2.5 diagnostic manifest (intake / crop / detect / warp / per-stage projections / source-predict). |
| `--eval` | (off) | Compute score-multiset Jaccard vs `resources/paper_targets/metadata.yml`, print the per-image table, and include `mean_jaccard` in `_summary.json`. |

**Sibling lookup:** for each image at `path/to/<stem>.jpg`, the CLI checks
for `path/to/<stem>_marked.jpg` (same directory) and feeds it to
AdaptiveFrameSizer when present. Override with `--no-gt`.

**Missing paths:** a non-existent path is reported and skipped; the rest of
the run continues. The exit code is still 0.

### Output contract

For each image `<stem>.jpg`, the CLI writes exactly **3 deliverables** into
`--out` (using `<stem>` as the file prefix):

```
<stem>_llm_input.png    # the 1024×1024 normalized orthogonal LLM input
<stem>_marked.png       # llm_input + magenta dots (∝ caliber, 70% of hole)
                         # + faint canonical ring frame + score labels
<stem>_result.json      # holes (x,y,score,confidence,caliber) + geometry +
                         # refinement diagnostics + self_test invert err
```

Plus one `_summary.json` at the end of the run carrying env presence, per-image
status, and (when `--eval`) the mean Jaccard.

### Examples

```bash
# Single image, mock, with diagnostics
uv run python -m src.domains.vision src/domains/vision/tests/fixtures/12.jpg \
    --detector mock --debug --out /tmp/v

# All 4 versioned fixtures, live Google, with eval table
uv run python -m src.domains.vision \
    src/domains/vision/tests/fixtures/{12,46,29,21}.jpg \
    --detector google --eval

# Override caliber for an unmarked image
uv run python -m src.domains.vision path/to/img21.jpg --detector google --caliber slug

# Ollama with an explicit host + model
OLLAMA_HOST=http://gpu-box:11434 OLLAMA_MODEL=llama3.2:latest \
    uv run python -m src.domains.vision path/to/img.jpg --detector ollama
```

## Python API

For programmatic use (inside Django, scripts, notebooks):

```python
from pathlib import Path
from src.domains.vision.detectors.factory import DetectorFactory
from src.domains.vision.pipeline.pipeline_runner import PipelineRunner

detector = DetectorFactory.build("google")        # or "ollama", "mock"
runner = PipelineRunner(detector)

result = runner.run(
    Path("path/to/image.jpg"),
    target_type="air_pistol",
    caliber_hint="9mm",
    out_dir=Path("/tmp/v"),
    gt_marked_path=Path("path/to/image_marked.jpg"),  # optional
)
print(result["count"], result["total_llm"], result["scores_llm"])
```

To run **just the geometry** (no detector, no API calls) — useful for
debugging the warp:

```python
from pathlib import Path
from src.domains.vision.geometry.geometry_pipeline import GeometryPipeline

g = GeometryPipeline().run(
    Path("path/to/image.jpg"),
    target_type="air_pistol",
    gt_marked_path=Path("path/to/image_marked.jpg"),
)
print(g.target_ring1_px, g.coordinate_frame.self_test_inversion())
```

## Django / q2 production path

The same pipeline runs asynchronously via django-q2 (SQLite ORM broker).
`.env` / env requirements: `GOOGLE_API_KEY` for the default detector.

```bash
# Apply migrations (creates vision_scoringjob + django_q tables)
uv run python src/manage.py migrate

# Start the qcluster in a separate terminal — it picks up enqueued tasks
uv run python src/manage.py qcluster

# In another shell, enqueue a job
uv run python src/manage.py shell -c "
from uuid import uuid4
from pathlib import Path
from src.domains.vision.pipeline.storage import ScoringStorage
from src.domains.vision.services import schedule_image_processing

storage = ScoringStorage()
rel = storage.save_upload(Path('src/domains/vision/tests/fixtures/12.jpg').read_bytes(), '12.jpg')
job_id = schedule_image_processing(
    user_uuid=uuid4(),
    input_path=rel,
    target_type='air_pistol',
    caliber_hint='9mm',
)
print(f'enqueued: {job_id}')
"
```

Once `qcluster` picks the task up, the job's `status` moves
`queued → running → succeeded`. The 3 deliverables land under
`<storage_root>/jobs/<job_id>/`; their relative paths are stored on the
`ScoringJob` row alongside the parsed result JSON.

To run synchronously (skipping qcluster) — useful in tests or one-shot
scripts:

```python
from src.domains.vision.services import process_image
result = process_image(job_id)  # runs immediately, returns the result dict
```

## Tests

```bash
# Full vision suite (uses the versioned 4-image fixtures — always runs in CI)
uv run --group test pytest src/domains/vision
```

The **geometry numerical-identity regression gate**
(`tests/test_geometry_regression.py`) needs the full 10-image train set at
`resources/train/` — local-only (per project policy, `resources/` is not
version-controlled). It skips gracefully when the local set is absent; ship
the images manually to run it.

## Layout

```
src/domains/vision/
├── __init__.py            public re-exports
├── __main__.py            standalone CLI (this README's subject)
├── apps.py                Django AppConfig
├── models.py              ScoringJob ORM model
├── ports.py               HoleDetector ABC + TargetType (the strategy seam)
├── dtos.py                DetectedHoleDTO / ScoringResultDTO / ScoringJobDTO
├── services.py            schedule_image_processing / process_image / get_job
├── geometry/              deterministic pipeline (OOP-rewritten from cv/)
├── detectors/             HoleDetector strategies + schema/prompt/client
│   ├── google_ai_studio_detector.py
│   ├── ollama_detector.py
│   ├── mock_detector.py
│   └── factory.py         name → class
├── pipeline/              PipelineRunner + deliverables + storage
├── eval/                  diagnostic-only (Jaccard, metadata.yml loader)
└── tests/
    ├── fixtures/          versioned 4-image set (12, 46, 29, 21 + _marked)
    ├── conftest.py        fixtures: versioned + local-only train images
    └── test_*.py
```

## Numerics

The geometry rewrite is **numerically identical** to cv/'s frozen output on
all 10 train images (regression test:
`tests/test_geometry_regression.py`). Cross-path identity (CLI mock vs Django
`process_image`) holds byte-for-byte — both produce the same `target_ring1_px`
and `bullseye_invert_err_px` (< 1e-12 px) for the same input.

## References

- Plan: `context/changes/cv-service-boundary/plan.md`
- Algorithm summary: `context/changes/cv-service-boundary/research-ai-detection.md`
- Architecture rules: `AGENTS.md` §1, §5 (boundaries), §6 (atomicity)
- Frozen reference: `cv/` (read-only — do not import from production code)
