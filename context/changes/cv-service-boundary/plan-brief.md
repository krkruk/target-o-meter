# Vision domain: AI hole-detection pipeline (production home) — Plan Brief

> Full plan: `context/changes/cv-service-boundary/plan.md`
> Research: `context/changes/cv-service-boundary/research-ai-detection.md`
> Frame (superseded on the algorithm; kept for dataset characterization): `context/changes/cv-service-boundary/frame.md`

## What & Why

Move the completed Phase-3 LLM hole-detection pipeline out of the `cv/` research sandbox into `src/domains/vision/` as a self-contained, OOP-rewritten Django domain — its production home. The research discovered and locked the algorithm (deterministic fused geometry → Google `gemini-3.5-flash-lite` VLM, mean score-Jaccard 0.638 on 10 images); this plan graduates that working code into the DDD layout, ships a standalone CLI plus a Django/q2 production path, and adds the never-built **Ollama** peer strategy. `cv/` becomes reference-only — zero runtime imports.

## Starting Point

`src/domains/vision/` is empty Django scaffolding (`ports.py`, `dtos.py`, `services.py` are docstring-only). The working algorithm lives in `cv/approaches/full_pipeline/` (+ ~4,900 LOC of stable transitive geometry across `cv/approaches/{fused,iteredge,multiring}`, `cv/{blob_detect,gt,detector_base}`, `cv/{phase3_spike,langchain_detector}`). The `HoleDetector` strategy seam and `detect(image_1024, target_type, caliber_hint, target_ring1_px)` contract already exist and are locked. `langchain-ollama` is already a dependency; `python-dotenv` is the only new dep.

## Desired End State

`uv run python -m src.domains.vision 12 46 29 21 --detector google` runs standalone (loads `.env`, writes 3 files/image + `_summary.json`, prints Jaccard); `--detector ollama` hits local `gemma4:latest`; `--detector mock` needs no API. In production, `services.schedule_image_processing(...)` enqueues a q2 task that runs the same pipeline, storing result JSON + 3 deliverable paths on a `ScoringJob` row (binaries on FileSystemStorage). The domain has zero `cv/` imports (grep-gated), preserves geometry numerics exactly (invert err < 1e-12 px on 10 images), and passes the import-linter domain-isolation contract.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
|---|---|---|---|
| Boundary: domain ↔ cv/ geometry | Full stack copy into the domain, self-contained (no cv/ runtime imports) | User directive: cv/ is reference-only; port + adapter on top | Plan |
| Architecture | Port + adapter; domain owns contracts (ports/dtos) + implementation in subpackages | DDD-correct per AGENTS.md; cv/ dependency eliminated entirely | Plan |
| OOP restructuring depth | Full OOP rewrite of all copied procedural code (one class → one file → matching name) | User-mandated convention (captured in lessons.md) | Plan |
| Django integration | Models + q2 task + standalone CLI (both paths share services code) | User flagged Django/q2 as in-scope complexity | Plan |
| Ollama role | Peer strategy (explicit `--detector ollama`, no failover) | Simplest; matches the strategy + DI pattern from research | Plan |
| Ollama model | `gemma4:latest` default, env-configurable (OLLAMA_MODEL/OLLAMA_HOST) | User directive; require OLLAMA_MODEL in .env.example | Plan |
| Env loading | python-dotenv in `__main__`; extend `.env.example` | Matches user's ".env vars be present" warning | Plan |
| Storage | Django FileSystemStorage for I/O; DB = metadata only | AGENTS.md §1 (DB stores metadata only) | Plan |
| Layout | Root contract files (ports/dtos/services/models) + internal subpackages (geometry/detectors/pipeline/eval) | AGENTS.md contract honored; internals namespaced | Plan |
| Eval tooling | Port Jaccard/metadata helpers as diagnostic/test-only (not production path) | Production has no GT; keep the fidelity gate available | Plan |
| Geometry gate | Numerical-identity regression test on 10 images (invert err < 1e-12 + frozen r1@1024 table) | Full OOP rewrite must be proven non-drifting | Plan |
| BFF scope | Vision domain only; BFF orchestration router is a follow-up change | Clean domain boundary; BFF crosses into other domains | Plan |
| Locked model | `gemini-3.5-flash-lite` (Google AI Studio) | Research Step-1: mean Jaccard 0.799 vs Gemma's 0.430 | Research |
| Schema/prompt | Port the 7-layer prompt + Pydantic TargetAnalysis verbatim | The load-bearing 0.799 artifact; no wording changes | Research |

## Scope

**In scope:**
- Port + OOP-rewrite ~4,900 LOC of geometry + detector + pipeline into `src/domains/vision/` (27 cv/ modules mapped to ~30 domain classes).
- New `OllamaDetector` peer strategy (`langchain-ollama`, `gemma4:latest`).
- `ScoringJob` model + migration + `services` (q2 enqueue/task) + FileSystemStorage adapter.
- Standalone `__main__` CLI (python-dotenv, 3 detectors, eval reporting).
- Numerical-identity regression suite (10 images) + unit/integration tests.
- Naming convention in `lessons.md`; `.env.example` extended.

**Out of scope:**
- Closing the Jaccard gap to 0.90 (off-by-one + stochastic over-report — future prompt tuning/hybrid scoring/voting).
- BFF orchestration router (upload endpoint + transaction.atomic across identity/core/vision).
- Held-out validation (images 32–46); Risk #43 stays open.
- Modifying or deleting `cv/`.
- Few-shot, confidence calibration, ensemble/majority voting; production reproducibility for the temperature-ignoring model (Risk #44).

## Architecture / Approach

```
src/domains/vision/
├── ports.py        HoleDetector ABC + TargetType       (strategy seam)
├── dtos.py         DetectedHoleDTO/ScoringResultDTO/ScoringJobDTO  (Pydantic, to BFF)
├── services.py     schedule_image_processing() / process_image()   (public seam + q2 task)
├── models.py       ScoringJob (UUID, status, paths, result JSON)
├── __main__.py     standalone CLI (dotenv; --detector google|ollama|mock)
├── geometry/       ImageLoader, TargetLocalizer, RingDetector, CircularPointsRectifier,
│                   HomographyModel, EdgePotential, FusedHomographyRefiner,
│                   AdaptiveFrameSizer, WarpProjector, CoordinateFrame, Normalizer,
│                   BlackDiscCalibrator/IssfScorer, GeometryPipeline, Calibration
├── detectors/      GoogleAIStudioDetector, OllamaDetector (NEW), MockDetector,
│                   VLMClient, schema, prompt, DetectorFactory
├── pipeline/       PipelineRunner, DeliverableRenderer, CaliberTaxonomy, ScoringStorage
└── eval/           ScoreJaccard + MetadataLoader  (diagnostic/test-only)
```

Both the CLI and the q2 task call `PipelineRunner` (geometry → detector → 3-file output). Math is copied verbatim from cv/ into class methods; the regression test is the proof of non-drift.

## Phases at a Glance

| Phase | What it delivers | Key risk |
|---|---|---|
| 1. Foundation & conventions | lessons rule, .env, ports/dtos/Calibration, MockDetector, test fixtures | Low — contract surface only |
| 2. Geometry port + gate | OOP-rewritten geometry + numerical-identity regression on 10 images | **Highest** — rewrite could drift numerics; gate is non-negotiable |
| 3. Detector strategies | Google (port) + Ollama (new) + schema/prompt/client, mocked tests | Ollama `with_structured_output` parity with local model |
| 4. Pipeline runner + deliverables | PipelineRunner + DeliverableRenderer + eval tooling (diagnostic) | Medium — 3-file output parity with cv/ |
| 5. Django production path | ScoringJob model + migration + q2 services + storage | q2 + Django setup correctness; ORM boundary |
| 6. Standalone CLI | `__main__.py` (dotenv, factory, eval table, _summary.json) | CLI must not require Django |
| 7. Final verification | Full suite + import-linter + no-cv grep guardrail | Scope creep / missed cv/ import |

**Prerequisites:** `GOOGLE_API_KEY` (in `.env.example`); local `ollama serve` + `gemma4:latest` only for the Ollama manual check; the 10 train images + `metadata.yml` under `resources/`.

**Estimated effort:** Large — ~4,900 LOC ported + OOP-rewritten across 7 phases. Roughly 4–6 focused sessions: Phase 2 (geometry) is the multi-session core; Phases 3–6 are roughly one session each; Phase 1 and 7 are short.

## Open Risks & Assumptions

- **OOP rewrite could drift geometry numerics** (Phase 2) — mitigated by the numerical-identity regression test; if it fails, the math wasn't copied verbatim.
- **`gemma4:latest` Ollama model tag + `with_structured_output` parity unverified** — the research never ran Ollama; first live run is exploratory and lower-fidelity than Gemini (documented, not gated).
- **`gemini-3.5-flash-lite` ignores temperature (Risk #44)** — run-to-run variance is real (img 29 returned 9/6/8 holes across runs); reproducibility relies on prompt + structured output, not sampling.
- **`__main__` must avoid importing Django models at module top** — lazy import inside the Django branch, else the CLI breaks when Django isn't configured.
- **`cal` dict → `Calibration` dataclass retrofit** — done early (Phase 1) to avoid a sweep; the one dict-producer (blob_detect fallback) gets a `from_dict`.

## Success Criteria (Summary)

- Geometry numerical-identity regression passes on all 10 train images (invert err < 1e-12 px, frozen r1@1024 table).
- `uv run python -m src.domains.vision 12 46 29 21 --detector google` runs end-to-end and prints mean Jaccard ~0.6–0.8; `--detector ollama` and `--detector mock` both run.
- `services.schedule_image_processing` → `process_image` round-trip writes 3 deliverables + a succeeded `ScoringJob` (mock detector).
- `rg "^import cv" src/domains/vision` is empty and `uv run lint-imports` holds.
