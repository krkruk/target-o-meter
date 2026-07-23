# Vision domain: AI hole-detection pipeline (production home) Implementation Plan

## Overview

Graduate the completed Phase-3 LLM hole-detection pipeline out of the `cv/` research sandbox into `src/domains/vision/` as a self-contained, OOP-rewritten Django domain. The domain becomes the production home for the algorithm discovered in `research-ai-detection.md`: deterministic geometry (localize → rings → rectify → refine → warp → normalize) feeding a vision-language detector (Google AI Studio `gemini-3.5-flash-lite` by default; a new peer **Ollama** strategy `gemma4:latest`). Two invocation paths share the same services code: a standalone `__main__` CLI and a Django/q2 production path (models + async task). `cv/` becomes a historical code reference only — the domain has zero runtime imports from it.

## Current State Analysis

- **`src/domains/vision/` is empty scaffolding** — `services.py`, `ports.py`, `dtos.py` are docstring-only; `models.py` has only Django's stub comment; `apps.py` registers the app. The `.importlinter` enforces inter-domain independence (vision ↮ core/identity) but does NOT forbid importing `cv/`.
- **The research code is complete and stable.** `cv/approaches/full_pipeline/` (573-LOC `pipeline.py` + 197-LOC `run.py`) integrates fused geometry + the live LLM detector behind the `HoleDetector` seam, writing 3 files/image. Mean score-Jaccard 0.638 on the 10-image train set (~71% of PRD 0.90; the residual is off-by-one scoring + stochastic over-report, both out of scope for this port — see *What We're NOT Doing*).
- **Dependency closure mapped: 27 cv/ modules, ~4,903 LOC** (see `research-ai-detection.md` + the closure table below). Third-party deps already in `pyproject.toml`: `cv2` (opencv-headless), `numpy`, `scipy`, `pydantic`, `pyyaml`, `pillow`, `langchain`, `langchain-google-genai`, `langchain-ollama`. Only `python-dotenv` is new.
- **The `HoleDetector` seam + `DetectedHole`/`DetectionResult` dataclasses** already exist in `cv/detector_base.py` and are the stable contract; `detect()` already carries `target_ring1_px`.
- **Ollama was never built** — research Phase-1 Q1 named `LangChainOllamaDetector` as a planned fallback; Step-1 Q7 deferred it. `langchain-ollama>=1.1.0` is already a dependency.

### Closure inventory (what gets ported — grouped by destination subpackage)

| cv/ source | LOC | → domain class/module |
|---|---|---|
| `cv/detector_base.py` | 101 | `ports.py` (HoleDetector), `detectors/{detected_hole,detection_result}.py` |
| `cv/mock_detector.py` | 71 | `detectors/mock_detector.py` |
| `cv/gt.py` (load_bgr only) | ~40 | `geometry/image_loader.py` |
| `cv/blob_detect.py` (to_gray, calibrate, score_holes) | ~250 of 683 | `geometry/classical_stages.py` |
| `cv/approaches/multiring/localize.py` | 422 | `geometry/target_localizer.py` |
| `cv/approaches/multiring/detect_rings.py` | 375 | `geometry/ring_detector.py` |
| `cv/approaches/multiring/homography.py` | 305 | `geometry/circular_points_rectifier.py` |
| `cv/approaches/iteredge/model.py` | 128 | `geometry/homography_model.py` |
| `cv/approaches/iteredge/edges.py` | 127 | `geometry/edge_potential.py` |
| `cv/approaches/iteredge/warp.py` | 67 | `geometry/warp_projector.py` |
| `cv/approaches/iteredge/normalize.py` | 147 | `geometry/{coordinate_frame,normalizer}.py` |
| `cv/approaches/fused/refine.py` | 616 | `geometry/homography_refiner.py` |
| `cv/approaches/fused/adaptive_frame.py` | 125 | `geometry/adaptive_frame_sizer.py` |
| `cv/approaches/full_pipeline/pipeline.py` (stages 1–8) | ~330 of 573 | `geometry/geometry_pipeline.py` |
| `cv/phase3_spike/schema.py` | 79 | `detectors/schema.py` |
| `cv/phase3_spike/prompt.py` | 115 | `detectors/prompt.py` |
| `cv/phase3_spike/client.py` + `cv/langchain_detector/client.py` | 198 | `detectors/vlm_client.py` |
| `cv/langchain_detector/detector.py` | 91 | `detectors/google_ai_studio_detector.py` |
| (new) | — | `detectors/ollama_detector.py` |
| `cv/phase3_spike/viz.py` | 110 | `pipeline/deliverable_renderer.py` |
| `cv/phase3_spike/metadata.py` (caliber half) | ~40 of 95 | `pipeline/caliber_taxonomy.py` |
| `cv/approaches/full_pipeline/pipeline.py` (stages 9–end) | ~240 of 573 | `pipeline/pipeline_runner.py` |
| `cv/phase3_spike/compare.py` | 71 | `eval/score_comparison.py` |
| `cv/phase3_spike/metadata.py` (yml half) | ~55 of 95 | `eval/metadata_loader.py` |

`cv/approaches/singleellipse/`, `cv/detect.py`, `cv/eval*.py`, `cv/normalize.py` (the Phase-1 one), `cv/pipeline.py`, `cv/run_*.py`, `cv/tmp/`, and all approach `run.py`/`pipeline.py` runners are **NOT in the closure** — confirmed by import trace; do not copy.

### Key Discoveries:

- **Three redundant transform/normalizer variants exist** (`cv/normalize.py`, `multiring/normalize.py`, `iteredge/normalize.py` as `IterEdgeTransformMeta`) — only the iteredge one is in the closure. The plan unifies the inverse-method quadruple (`norm_to_crop`/`crop_to_source`/`norm_to_source`/`self_test_inversion`) behind one `CoordinateFrame`.
- **The `cal` dict** (`cx, cy, s_px, r_bull_px, r_bw_px, ok, shape`) is the de-facto inter-stage contract; it becomes a typed `Calibration` dataclass early (high-leverage, unblocks clean class signatures).
- **Numerical identity is the regression gate.** The OOP rewrite must copy the math verbatim into methods — bullseye invert err is < 1e-12 px today and must stay there.
- `gemini-3.5-flash-lite` ignores `temperature` (Risk #44, still open); the Ollama path shares this constraint. Reproducibility relies on structured output + prompt, not sampling.

## Desired End State

`src/domains/vision/` is a self-contained Django domain that:

1. **Runs standalone** via `uv run python -m src.domains.vision 12 46 29 21 --detector google` (loads `.env`, calls Google AI Studio, writes 3 files/image + `_summary.json` to a `--out` dir), or `--detector ollama` (local `gemma4:latest`), or `--detector mock` (no API calls, plumbing).
2. **Runs in production** via `services.schedule_image_processing(...)` (enqueues a q2 task) → `services.process_image(job_id)` (runs `PipelineRunner`, stores result JSON + output paths on a `ScoringJob` row; PNG deliverables on `FileSystemStorage`).
3. **Has zero runtime imports from `cv/`** — verified by a grep gate in CI.
4. **Preserves geometry numerics exactly** — regression test on all 10 train images asserts bullseye invert err < 1e-12 px AND the frozen r1@1024 table (1→394, 4→394, 6→394, 10→394, 12→333, 19→394, 21→371, 29→394, 31→321, 46→394).
5. **Honors AGENTS.md** — domain is pure Python (no HTTP; BFF wiring is a follow-up), DTOs cross boundaries, import-linter domain isolation passes.

## What We're NOT Doing

- **Closing the Jaccard gap to 0.90.** The 0.262 residual is off-by-one ring scoring + stochastic over-report on clusters/pasties (research § "Gap-to-PRD analysis"). Addressed by future prompt tuning / hybrid scoring / majority voting — separate changes.
- **BFF orchestration router.** The upload endpoint + `transaction.atomic` orchestration across identity/core/vision (AGENTS.md §6.2) is a follow-up change. This plan exposes `schedule_image_processing()` as vision's public seam only.
- **Held-out validation (images 32–46).** Risk #43 (Layer-3 ecc-scaled affine bounds untested on real data) stays open.
- **Modifying `cv/`.** The `cv/` tree is reference-only and stays untouched (frozen at commit `76f6fc4`).
- **Few-shot, confidence calibration, ensemble/majority voting.** Open research questions; not needed for the port.
- **Production reproducibility for the temperature-ignoring model** (Risk #44) — documented, not solved here.

## Implementation Approach

**Port + adapter, fully self-contained, OOP-rewritten.** The domain owns its geometry code (copied from `cv/`, restructured into classes per the one-class-per-file rule captured in `lessons.md`). The `HoleDetector` ABC is the strategy port in `ports.py`; concrete strategies live in `detectors/`. The pipeline orchestrator (`PipelineRunner`) composes geometry + a detector; both the CLI and the q2 task call the same `services` entry points. The math is copied verbatim into methods — the OOP rewrite changes structure, not numerics; the regression test is the proof.

Package layout (root contract files + internal subpackages):

```
src/domains/vision/
├── __init__.py            public re-exports
├── apps.py                Django AppConfig (exists)
├── models.py              ScoringJob ORM model
├── ports.py               HoleDetector ABC + TargetType + GeometryPort
├── dtos.py                DetectedHoleDTO, ScoringResultDTO, ScoringJobDTO (Pydantic)
├── services.py            schedule_image_processing(), process_image()
├── test_utils.py          seeders for tests
├── __main__.py            standalone CLI
├── geometry/              deterministic geometry (OOP-rewritten from cv/)
├── detectors/             HoleDetector strategies + schema/prompt/client
├── pipeline/              PipelineRunner + deliverables + storage
├── eval/                  diagnostic/test-only (ScoreJaccard, MetadataLoader)
└── tests/                 pytest-bdd unit + integration
```

## Critical Implementation Details

- **Math is copied verbatim, not rederived.** Every geometry class method that performs a numerical operation must lift the exact expression from its cv/ source (cited in each class's docstring). The regression gate (Phase 2) is non-negotiable; if invert err drifts above 1e-12 px, the rewrite is wrong, not the gate.
- **`cal` dict → `Calibration` dataclass first.** Almost every geometry class reads `cal["s_px"]` etc. Defining `Calibration` in Phase 1 (before any geometry class) lets every class accept/return typed objects instead of dicts, and avoids a retrofit pass later.
- **Constants travel with their class.** The load-bearing tuning tables (`DEFAULT_SCHEDULE`, `DEFAULT_PERSPECTIVE_BOUND`, `SKIP_REFINE_ECC_THRESHOLD`, `AFFINE_LOCK_ECC_THRESHOLD`, `SV_RATIO_*`, `M2_ANISO_GATE_THRESHOLD`, `DEFAULT_MARGIN_FACTOR`, etc. — see research § "Final tuned parameters") become module-level constants on the file that owns the class that uses them. Do not centralize them.
- **q2 concurrency cap.** AGENTS.md §2 caps the queue at max 3 concurrent tasks — a deployment/config concern, not code; called out here so the implementer does not add in-process throttling.
- **`__main__` must not require Django.** The CLI loads `.env` via python-dotenv and runs the pipeline pure-Python. The Django models/q2 path is only invoked through `services` when Django is configured. `__main__` therefore must not import `models.py` at module top (lazy import inside the Django-specific branch, or guard on `django.apps.apps.ready`).

## Phase 1: Foundation, conventions & test harness

### Overview

Lay the contract surface, capture the naming convention, extend env config, and stand up the regression-test fixture so Phase 2 has its gate ready before any geometry is rewritten.

### Changes Required:

#### 1. Naming convention in `lessons.md`

**File**: `context/foundation/lessons.md`

**Intent**: Already appended (this session): the "One class per file, matching filename" rule, with the `ports.py`/`dtos.py` contract-collection exception noted. No further edit unless review adjusts wording.

**Contract**: New section present under `## One class per file, matching filename`.

#### 2. Env + dependency config

**File**: `.env.example`

**Intent**: Document every env var the domain reads. `GOOGLE_API_KEY` exists; add the Ollama pair the new strategy reads.

**Contract**:
```
GOOGLE_API_KEY=key_i_need_some_computer_vision_skills_and_im_too_dumb_to_implement_an_algorithm_of_my_own
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=gemma4:latest
```

**File**: `pyproject.toml`

**Intent**: Add the one new runtime dep the CLI needs to auto-load `.env`. Managed via `uv add python-dotenv`.

**Contract**: `python-dotenv` appears in `[project.dependencies]`.

#### 3. Root contract files: ports + dtos + Calibration

**File**: `src/domains/vision/ports.py`

**Intent**: Define the strategy seam (the port collaborators depend on) and the shared literal type. Lift `HoleDetector` ABC + `TargetType` verbatim from `cv/detector_base.py:71-101` and `:20`.

**Contract**: `TargetType = Literal["air_pistol","precision_pistol"]`; `class HoleDetector(ABC)` with abstract `name` property and `detect(image_1024, target_type, caliber_hint=None, target_ring1_px=None) -> DetectionResult`. No cv/ import.

**File**: `src/domains/vision/dtos.py`

**Intent**: Define the Pydantic DTOs that cross the domain boundary to the BFF (AGENTS.md §5), separate from the internal dataclasses. These are mapped from `DetectionResult` in `services`.

**Contract**: `DetectedHoleDTO(x:int, y:int, score:int, confidence:float, caliber:str|None)`; `ScoringResultDTO(holes:list[DetectedHoleDTO], target_type:TargetType, notes:str|None, detector_name:str)`; `ScoringJobDTO(job_id:UUID, status:str, ...)` (finalized in Phase 5 when the model exists).

**File**: `src/domains/vision/geometry/calibration.py`

**Intent**: Replace the untyped `cal` dict with a typed value object before any geometry class is written, so all downstream signatures are clean from the start.

**Contract**: `@dataclass Calibration(shape, cx, cy, s_px, r_bull_px, r_bw_px, ok: bool)` with the exact key set the cv/ `cal` dict carries (`cv/approaches/full_pipeline/pipeline.py:361-368`). Add a `from_dict()` classmethod for the one place (blob_detect fallback) that still produces a dict.

#### 4. Detector dataclasses + MockDetector

**File**: `src/domains/vision/detectors/detected_hole.py`, `src/domains/vision/detectors/detection_result.py`

**Intent**: Port the two internal result dataclasses (one class per file per the convention) from `cv/detector_base.py:23-68`. Each keeps its `to_dict()`.

**Contract**: `DetectedHole(x,y,score,confidence=1.0,caliber=None)` and `DetectionResult(holes, target_type, detector_name, notes=None, raw=None)` — fields unchanged (the Phase-3-Step-2 contract the locked model already speaks).

**File**: `src/domains/vision/detectors/mock_detector.py`

**Intent**: Port `MockDetector` from `cv/mock_detector.py` verbatim — returns the fixed 5-hole pattern. This is the plumbing detector the regression test and CLI `--detector mock` use, so it must exist before Phase 2.

**Contract**: `class MockDetector(HoleDetector)`; `name == "mock"`; `detect()` accepts and ignores `target_ring1_px`; returns the fixed bullseye + 4 cardinals pattern.

#### 5. Regression-test fixture harness

**File**: `src/domains/vision/tests/conftest.py` (and `tests/__init__.py`)

**Intent**: Give Phase 2 its gate on day one. Expose fixtures that locate the 10 train images + `metadata.yml` + the frozen expected-metrics table, without coupling tests to cv/.

**Contract**: fixtures `train_images() -> list[Path]` (ids 1,4,6,10,12,19,21,29,31,46 under `resources/train/`), `marked_paths() -> dict[str,Path]` (`<id>_marked.jpg`, used by `AdaptiveFrameSizer`), and `FROZEN_RING1 = {1:394,4:394,6:394,10:394,12:333,19:394,21:371,29:394,31:321,46:394}` (from research § "Final per-image results"). The actual assertion test lands in Phase 2 once `GeometryPipeline` exists; this fixture only sets up paths/constants.

### Success Criteria:

#### Automated Verification:
- `uv run ruff check src/domains/vision` passes (new files lint clean).
- `uv run lint-imports` passes (domain isolation unaffected — vision imports nothing from core/identity).
- `uv run pytest src/domains/vision/tests -k mock_detector` passes (MockDetector returns the 5-hole pattern; trivial unit test).
- Grep gate: `rg -n "^import cv|^from cv\b" src/domains/vision` returns no matches.

#### Manual Verification:
- `.env.example` contains all three vars; `uv run python -c "import dotenv"` succeeds.
- `MockDetector().detect(...)` returns 5 holes with the documented scores `[10,7,7,7,7]`.

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 2: Geometry port + numerical-identity gate

### Overview

Port and OOP-rewrite the deterministic geometry (the stable Phase-1→2.5 stack) into `vision/geometry/`, assemble `GeometryPipeline`, and prove the rewrite is numerically identical to the frozen cv/ output on all 10 train images. This is the largest and highest-risk phase.

The order below matches the pipeline stage sequence (each class cites its cv/ source). Math is lifted verbatim into methods; only the structure (free functions → class methods, dict args → typed args) changes.

### Changes Required:

#### 1. Image intake + grayscale

**File**: `src/domains/vision/geometry/image_loader.py`

**Intent**: EXIF-aware image load (the pipeline's stage 1 intake). Lifted from `cv/gt.py::load_bgr`.

**Contract**: `class ImageLoader` with static-ish `load_bgr(path) -> np.ndarray` (BGR uint8, EXIF-oriented). Only the load_bgr portion of `cv/gt.py` is ported (magenta GT helpers stay in eval/ later, or are dropped — see Phase 4).

**File**: `src/domains/vision/geometry/classical_stages.py`

**Intent**: The "solved classical" primitives the pipeline still uses: grayscale conversion, the black-disc calibration fallback, and ISSF line-break scoring (diagnostic-only, computed in parallel with the LLM score). Lifted from `cv/blob_detect.py` — only `to_gray`, `calibrate`, `score_holes` (the three symbols `full_pipeline/pipeline.py` imports), not the matched-filter/hole-detection dead code.

**Contract**: `class ImageGrayscaler` (`to_gray(bgr)`), `class BlackDiscCalibrator` (`calibrate(crop) -> Calibration`, using `Calibration.from_dict`), `class IssfScorer` (`score_holes(holes, calibration) -> list[int]`). Constants `RING_STEPS_BW_TO_BULL=3`, `BULLET_RADIUS_MM` travel here.

#### 2. Localization + ring detection

**File**: `src/domains/vision/geometry/target_localizer.py`

**Intent**: Stage 2 localize — the multiring black-disc-contrast detector that rejects printed logos (the img-29 fix). Lifted from `cv/approaches/multiring/localize.py` (422 LOC, the public `crop_to_target` + the `_find_concentric_circles_cluster` collaborator).

**Contract**: `class TargetLocalizer` with `crop_to_target(gray, expand_rings=1.30) -> (crop, bbox, init)`. The `init` dict (`cx_crop, cy_crop, s_px_init, r_bw_px_init, r_bull_px_init`) is preserved as-is for now (consumed by `RingDetector` and `BlackDiscCalibrator`); a follow-up could type it but is out of scope.

**File**: `src/domains/vision/geometry/ring_detector.py`

**Intent**: Stage 3 detect rings — bounded 4-parameter ellipse fit per ring (7–13 rings). Lifted from `cv/approaches/multiring/detect_rings.py` (375 LOC: `detect_rings` + `_polar_unwrap` + `_fit_ring_ellipse`).

**Contract**: `class RingDetector` with `detect(gray_crop, init=None) -> RingDetection`, where `RingDetection` is a small dataclass `{rings: list[dict], edges: np.ndarray, clahe: np.ndarray}` (the existing return shape; downstream code reads `r["cx"], r["semi_a"]` etc., preserved).

#### 3. Initial homography (circular points)

**File**: `src/domains/vision/geometry/circular_points_rectifier.py`

**Intent**: Stage 4 initial H — the affine rectifier via the image of the circular points. Lifted from `cv/approaches/multiring/homography.py` (305 LOC). `projective_refine=False` is mandatory (research § architecture decision).

**Contract**: `class CircularPointsRectifier` with `compute(rings, projective_refine=False) -> RectificationResult` (`{H, H_inv, Q, center, circular_points, center_drift, used_projective}`). All helper functions (`ellipse_to_conic`, `conic_2x2_block`, `average_shared_metric`, `matrix_inverse_sqrt`, `circular_points_from_Q`, `estimate_center_drift_projective`) become private methods.

#### 4. Homography model + edge potential

**File**: `src/domains/vision/geometry/homography_model.py`

**Intent**: The 8-DOF parametrization + ring-point prediction used by the refiner. Lifted from `cv/approaches/iteredge/model.py` (128 LOC).

**Contract**: `class HomographyModel` with `params_to_H`, `H_to_params`, `affine_init_params(M2, t_xy)`, `apply_H_to_points(H, pts)`, `ring_points_warped(...)`, `sample_potential(potential, pts)`. Constant `HOMOGRAPHY_DOFS=8`.

**File**: `src/domains/vision/geometry/edge_potential.py`

**Intent**: Canny/Sobel edges + ring-tangency weighting → the distance-transform potential the optimizer snaps to. Lifted from `cv/approaches/iteredge/edges.py` (127 LOC).

**Contract**: `class EdgePotential` with `sobel_magnitude`, `canny_edges`, `edge_distance_transform`, `enhance_ring_edges(gray, cx, cy, s_px) -> dict`.

#### 5. Differential refinement (the 5-layer defense)

**File**: `src/domains/vision/geometry/homography_refiner.py`

**Intent**: Stage 5 — the 8-DOF homography refinement with the 5-layer orthogonality defense (skip / lock-affine / ecc-scaled bounds / SV-ratio penalty / post-refinement gates). Lifted from `cv/approaches/fused/refine.py` (616 LOC — the largest single port). The inlined `make_residual_fn` (with iteredge's off-by-one + crop/warped-frame bugs already fixed) is a private method. **All load-bearing constants travel here** (`DEFAULT_SCHEDULE`, `DEFAULT_PERSPECTIVE_BOUND=1e-4`, `SKIP_REFINE_ECC_THRESHOLD=1.02`, `AFFINE_LOCK_ECC_THRESHOLD=1.10`, `AFFINE_BOUND_BASE=0.10`, `SV_RATIO_THRESHOLD=1.05`, `SV_RATIO_WEIGHT=1e3`, `CORNER_RATIO_*`, `M2_ANISO_GATE_THRESHOLD=1.10`).

**Contract**: `class FusedHomographyRefiner` with `refine(gray_crop, calibration, affine_init_params, affine_M2, affine_t, warped_out_center, s_warped, r_bull_warped, *, stage_callback=None, perspective_bound, edge_band_mask, corner_gate_enable=True, mean_ring_eccentricity) -> RefinementResult` (`{final_H, final_cost, n_iterations, converged, n_stages, reverted_to_init, revert_reason, defense_layer}`). Signature mirrors the cv/ `refine_homography` exactly so the math lifts verbatim.

#### 6. Adaptive frame + warp + normalize

**File**: `src/domains/vision/geometry/adaptive_frame_sizer.py`

**Intent**: Stage 6 sizing — GT-hole-extent-aware `margin_factor` so the whole warp canvas fits 1024 with no content crop. Lifted from `cv/approaches/fused/adaptive_frame.py` (125 LOC).

**Contract**: `class AdaptiveFrameSizer` with `margin_factor(bbox, H_opt, cx_crop, cy_crop, r_ring1_warped, gt_marked_path=None) -> (float, info_dict)`. Constants `DEFAULT_MARGIN_FACTOR=1.30`, `HOLE_MARGIN_FACTOR=1.10`, `MAX_MARGIN_FACTOR=2.50`.

**File**: `src/domains/vision/geometry/warp_projector.py`

**Intent**: Apply the refined homography. Lifted from `cv/approaches/iteredge/warp.py` (67 LOC).

**Contract**: `class WarpProjector` with `compute_output_shape(H, src_shape, cx, cy, r_ring1, margin_factor) -> (w, h, H_full)` and `apply_warp(src, H_full, out_size, border_value=245.0)`.

**File**: `src/domains/vision/geometry/coordinate_frame.py` + `src/domains/vision/geometry/normalizer.py`

**Intent**: Stage 7 — fit the entire warp canvas into 1024×1024 (bullseye at (512,512); no content crop) + the exact-analytical inverse chain. Lifted from `cv/approaches/iteredge/normalize.py` (147 LOC). Unifies the `IterEdgeTransformMeta` dataclass + the inverse quadruple into `CoordinateFrame`; `Normalizer` owns `normalize_to_1024`.

**Contract**: `@dataclass CoordinateFrame(bbox, H_full_inv, scale, tx, ty, cx_crop, cy_crop, size, r_ring1_warped, bullseye_warped)` with `norm_to_crop`, `crop_to_source_xy`, `norm_to_warped`, `warped_to_crop`, `norm_to_source`, `self_test_inversion() -> float`. `class Normalizer` with `normalize_to_1024(warped, H_full, bullseye_warped, bbox, r_ring1_warped, cx_crop, cy_crop, target_ring1_px) -> (image_1024, CoordinateFrame)`.

#### 7. GeometryPipeline orchestrator

**File**: `src/domains/vision/geometry/geometry_pipeline.py`

**Intent**: Compose stages 1–8 of `cv/approaches/full_pipeline/pipeline.py:241-453` (intake → localize → rings → H_init → refine → warp → normalize) into one class that returns the LLM-ready image + geometry. The cv/ pipeline-level helpers (`_is_plausible_cal`, `_elliptical_band_mask`, `_mean_ring_eccentricity`, `_warped_ring_metrics`) become private methods here.

**Contract**: `class GeometryPipeline` with `run(image_path, *, target_type, gt_marked_path=None, projective_refine_init=False, debug=False) -> GeometryResult`, where `GeometryResult` is a dataclass `{bgr, gray, image_1024, target_ring1_px, coordinate_frame, calibration, refinement, frame_info, metrics, debug_artifacts}`. The `target_ring1_px` computed at `pipeline.py:430` is threaded through to the detector. The `debug` flag surfaces the 14-file diagnostics (gated, mirroring cv/).

#### 8. Numerical-identity regression test

**File**: `src/domains/vision/tests/test_geometry_regression.py`

**Intent**: The proof the rewrite didn't drift. Runs `GeometryPipeline` on all 10 train images (MockDetector is NOT needed here — geometry is detector-independent) and asserts the frozen metrics.

**Contract**: For each train image id, assert (a) `coordinate_frame.self_test_inversion() < 1e-12`, (b) `geometry_result.target_ring1_px` equals `FROZEN_RING1[id]` (±0, exact), (c) `refinement.mean_ring_eccentricity` within 1e-9 of the frozen ecc table (research § "Final per-image results"), (d) the defense-layer classification matches (skip for 1/6/10/12/19/21/29; lock_affine for 4/31/46). Parametrize over the 10 ids via the Phase-1 fixture.

### Success Criteria:

#### Automated Verification:
- `uv run pytest src/domains/vision/tests/test_geometry_regression.py` passes on all 10 images (the load-bearing gate).
- `uv run ruff check src/domains/vision/geometry` passes.
- Grep gate: `rg -n "^import cv|^from cv\b" src/domains/vision` returns no matches.

#### Manual Verification:
- Run `GeometryPipeline` on img 12 (gold standard); visually confirm `image_1024` matches `resources/train/intermediate_fused_all10/12_04_llm_input.png` byte-for-byte (or within cv2 imencode noise).
- Confirm defense-layer classifications match the research table.

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 3: Detector strategies (Google AI Studio + Ollama)

### Overview

Port the locked LLM detector and build the new Ollama peer strategy, both behind the `HoleDetector` seam. Both reuse the same Pydantic schema + 7-layer prompt (the locked Step-1 artifacts).

### Changes Required:

#### 1. Pydantic schema + prompt builder

**File**: `src/domains/vision/detectors/schema.py`

**Intent**: Port the `with_structured_output` schema verbatim from `cv/phase3_spike/schema.py` (79 LOC) — the locked contract the model already parses cleanly.

**Contract**: `class Hole(BaseModel)` (`x,y,score,confidence,caliber` with the exact Field constraints/descriptions) and `class TargetAnalysis(BaseModel)` (`holes, target_type, notes`). Field descriptions unchanged (they are load-bearing prompt-steering).

**File**: `src/domains/vision/detectors/prompt.py`

**Intent**: Port the 7-layer system-prompt builder verbatim from `cv/phase3_spike/prompt.py` (115 LOC) — the single most load-bearing artifact of the 0.799 Step-1 result. No wording changes.

**Contract**: `class SystemPromptBuilder` (or module functions `build_system_prompt(target_type, target_ring1_px, ring_step_px, primary_caliber)` + `build_user_text()`). `_CANONICAL_CALIBERS` constant travels here. Output strings byte-identical to cv/.

#### 2. VLM client base + array encoding

**File**: `src/domains/vision/detectors/vlm_client.py`

**Intent**: Port + unify the client from `cv/phase3_spike/client.py` + `cv/langchain_detector/client.py` (198 LOC combined). The base owns the base64 PNG-encode of the in-memory 1024 array and the message construction; subclasses bind a specific LangChain chat model.

**Contract**: `class VLMClient` (abstract base) with `analyze_array(image, target_type, target_ring1_px, ring_step_px, primary_caliber) -> tuple[TargetAnalysis, dict]` (builds SystemMessage + HumanMessage, invokes `self._structured`, returns parsed result + timing meta). `analyze(path)` delegates via read+decode. Subclasses set `self._structured` in `__init__` by constructing the bound chat model + `.with_structured_output(TargetAnalysis)`.

#### 3. GoogleAIStudioDetector

**File**: `src/domains/vision/detectors/google_ai_studio_detector.py`

**Intent**: Port `LangChainDetector` from `cv/langchain_detector/detector.py`, renamed to reflect the binding. Backed by `langchain_google_genai.ChatGoogleGenerativeAI`; locked model `gemini-3.5-flash-lite`. Reads `GOOGLE_API_KEY` from env; raises a clear RuntimeError if absent.

**Contract**: `class GoogleAIStudioDetector(HoleDetector)` with `__init__(self, model="gemini-3.5-flash-lite", temperature=1.0)`, `name -> "google-gemini-3.5-flash-lite"`, `detect(...)` mapping `TargetAnalysis` → `DetectionResult` (the exact mapping at `cv/langchain_detector/detector.py:67-91`, including the `raw` dict with model/ring-step/calibers/served_by). Uses a `GoogleStudioVLMClient(VLMClient)` subclass (one file, co-located or as `google_ai_studio_client.py` if the one-class rule demands separation).

#### 4. OllamaDetector (NEW)

**File**: `src/domains/vision/detectors/ollama_detector.py`

**Intent**: The never-before-built peer strategy. Backed by `langchain_ollama.ChatOllama`; default model `gemma4:latest` (env-configurable). Same schema + same prompt + same `analyze_array` path as the Google detector — it is a true peer, not a fallback. Reads `OLLAMA_HOST` (default `http://localhost:11434`) and `OLLAMA_MODEL` (default `gemma4:latest`) from env.

**Contract**: `class OllamaDetector(HoleDetector)` with `__init__(self, model=None, host=None)` defaulting from env (falling back to the documented defaults), `name -> "ollama-<model>"`, `detect(...)` identical mapping to the Google detector. Uses `OllamaVLMClient(VLMClient)` that constructs `ChatOllama(model=..., base_url=...).with_structured_output(TargetAnalysis)`. If `ChatOllama.with_structured_output` behaves differently for the local model, surface the discrepancy in `raw["served_by"]` but do not silently change the schema.

#### 5. Detector unit tests (mocked LLMs)

**File**: `src/domains/vision/tests/test_google_detector.py`, `src/domains/vision/tests/test_ollama_detector.py`

**Intent**: Verify the `TargetAnalysis → DetectionResult` mapping, env handling, and error messages without making live API calls.

**Contract**: Mock the LangChain `invoke` (Google) / `ChatOllama` (Ollama) to return a canned `TargetAnalysis`; assert the detector returns the expected `DetectionResult` (x/y/score/confidence/caliber carried through, `raw["model"]` set, `name` correct). Assert missing `GOOGLE_API_KEY` / unreachable Ollama raises the documented error. Assert env override of `OLLAMA_MODEL`/`OLLAMA_HOST` is respected.

### Success Criteria:

#### Automated Verification:
- `uv run pytest src/domains/vision/tests/test_google_detector.py src/domains/vision/tests/test_ollama_detector.py` passes (mocked — no network).
- `uv run ruff check src/domains/vision/detectors` passes.
- The schema/prompt modules produce byte-identical output to cv/ (snapshot test comparing `build_system_prompt(...)` to a frozen string).

#### Manual Verification:
- `OllamaDetector` constructs without error when `ollama serve` is running locally and `gemma4:latest` is pulled.

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 4: Pipeline runner + deliverables + eval tooling

### Overview

Wire geometry + detector into `PipelineRunner`, port the magenta-dot deliverable renderer and caliber taxonomy, and port the Jaccard/metadata eval tooling as diagnostic-only (used by the CLI's eval flag + tests, never by the q2 production path).

### Changes Required:

#### 1. Deliverable renderer + caliber taxonomy

**File**: `src/domains/vision/pipeline/deliverable_renderer.py`

**Intent**: Port the magenta-dot drawing (radius ∝ caliber, 70% of hole) + faint canonical ring frame + score labels from `cv/phase3_spike/viz.py` (110 LOC). Lifted verbatim — the marker sizing is part of the user's Step-2 spec.

**Contract**: `class DeliverableRenderer` with `draw_magenta_holes(image_1024_gray, holes, target_type, target_ring1_px, with_score=True) -> np.ndarray (BGR)`. Helpers `px_per_mm`, `marker_radius_px` become methods. Constants `_RING1_RADIUS_MM`, `_CALIBER_DIAMETER_MM`, `_MARKER_DIAMETER_FRACTION=0.70` travel here.

**File**: `src/domains/vision/pipeline/caliber_taxonomy.py`

**Intent**: Port the caliber normalization + diameter lookup (the half of `cv/phase3_spike/metadata.py` that the renderer needs) so the renderer does not depend on eval tooling.

**Contract**: `class CaliberTaxonomy` with `normalize(c) -> str` (`METADATA_CALIBER_ALIASES` map: `9x19→9mm`, `slug→12-gauge`) and `diameter_mm(c) -> float`. The diameter table is the single source of truth (shared with the renderer if needed, or duplicated as a module constant — prefer one home in `caliber_taxonomy.py`).

#### 2. PipelineRunner

**File**: `src/domains/vision/pipeline/pipeline_runner.py`

**Intent**: Port stages 9–end of `cv/approaches/full_pipeline/pipeline.py:455-572`: after geometry produces `image_1024`, call `detector.detect(...)`, invert holes to crop/source frames, compute classical scores (diagnostic), and write the 3 deliverables (`<id>_llm_input.png`, `<id>_marked.png`, `<id>_result.json`). The 14-file Phase-2.5 diagnostics stay gated behind `debug=True`.

**Contract**: `class PipelineRunner` with `__init__(self, detector: HoleDetector)` and `run(image_path, *, target_type, caliber_hint, out_dir, debug=False, gt_marked_path=None) -> dict` (the `result_dict` shape at `pipeline.py:486-542`). Composes `GeometryPipeline` + `detector` + `DeliverableRenderer`. The `target_ring1_px` flows geometry → detector (the sanctioned Step-2 subtlety #1).

#### 3. Eval tooling (diagnostic-only)

**File**: `src/domains/vision/eval/score_comparison.py`

**Intent**: Port `cv/phase3_spike/compare.py` (71 LOC) — score-multiset Jaccard, exact-count-match, per-score breakdown, misalignment flags. Used by the CLI's eval table + the regression report; NOT imported by `services` or `models`.

**Contract**: module functions (not a class — these are pure functions, acceptable under the convention) `score_multiset`, `score_jaccard`, `exact_count_match`, `per_score_breakdown`, `misalignment_flags`. Signatures unchanged.

**File**: `src/domains/vision/eval/metadata_loader.py`

**Intent**: Port the metadata.yml half of `cv/phase3_spike/metadata.py` (55 LOC) — `load_metadata`, `primary_caliber_for`, `gt_hits_for`. Diagnostic/test-only.

**Contract**: `class MetadataLoader` with the repo-relative path to `resources/paper_targets/metadata.yml` and the three accessors. The `load_fused_result`/`ring1_px_for` helpers are NOT ported (they read cv/ intermediate output that no longer exists in the domain).

#### 4. Pipeline integration test

**File**: `src/domains/vision/tests/test_pipeline_runner.py`

**Intent**: End-to-end with the mock detector (no API calls) — proves geometry + detector + renderer + 3-file output compose.

**Contract**: Run `PipelineRunner(MockDetector())` on img 12; assert 3 files written; assert `_result.json` parses; assert `count == 5`; assert classical scores computed; assert `_marked.png` exists and is non-empty.

### Success Criteria:

#### Automated Verification:
- `uv run pytest src/domains/vision/tests/test_pipeline_runner.py` passes (mock detector, no network).
- `uv run ruff check src/domains/vision/pipeline src/domains/vision/eval` passes.
- Grep gate: `rg -n "^import cv|^from cv\b" src/domains/vision` returns no matches.

#### Manual Verification:
- Run `PipelineRunner` on img 12 with MockDetector; open `<out>/12_marked.png` — magenta dots + ring frame + score labels render correctly.

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 5: Django production path (models + q2 + services + storage)

### Overview

Add the ORM model, the FileSystemStorage-backed I/O, and the `services` entry points (q2 enqueue + task body). The BFF router that calls `schedule_image_processing` is explicitly out of scope (follow-up change).

### Changes Required:

#### 1. ScoringJob model + migration

**File**: `src/domains/vision/models.py`

**Intent**: Persist pipeline-job metadata only (AGENTS.md §1: DB stores metadata; binaries on storage). The q2 task reads its input path and writes its result/paths back here.

**Contract**: `class ScoringJob(models.Model)` with: `id` (UUIDField, primary key, the cross-domain safe key per AGENTS.md §5), `user_uuid` (UUIDField, indexed — owner identity, NOT a FK to another domain), `status` (CharField choices: queued/running/succeeded/failed), `input_path` (CharField — storage path to uploaded image), `target_type` (CharField: air_pistol/precision_pistol), `caliber_hint` (CharField null=True), `result` (JSONField null=True — the DetectionResult + classical scores), `llm_input_path`/`marked_image_path`/`result_json_path` (CharField null=True — storage paths to the 3 deliverables), `error` (TextField null=True), `created_at`/`updated_at`/`completed_at` (DateTimeField). No FKs to other domains.

**File**: `src/domains/vision/migrations/0001_initial.py`

**Intent**: Generated via `uv run python src/manage.py makemigrations vision`.

**Contract**: Creates the `vision_scoringjob` table.

#### 2. Storage adapter

**File**: `src/domains/vision/pipeline/storage.py`

**Intent**: Wrap Django's FileSystemStorage (hashed-path bucketing per AGENTS.md §1) so the pipeline reads inputs and writes the 3 deliverables through it in production, while the CLI path writes to a local `--out` dir directly. Keep the pipeline storage-agnostic: it works against a path-like interface.

**Contract**: `class ScoringStorage` with `save_upload(upload) -> str` (returns the stored input path), `deliverable_dir(job_id) -> Path`, `write_deliverable(job_id, name, data) -> str`. Built on `django.core.files.storage.FileSystemStorage`. The CLI bypasses this and passes an `out_dir` Path to `PipelineRunner` directly.

#### 3. services.py — the public seam

**File**: `src/domains/vision/services.py`

**Intent**: The domain's public API the BFF will call (AGENTS.md §6.2 — BFF wraps the call in `transaction.atomic()`). Two functions: enqueue (called synchronously by the BFF) and the task body (run by q2).

**Contract**:
- `schedule_image_processing(*, user_uuid, input_path, target_type, caliber_hint) -> str` — creates a `ScoringJob(status="queued")`, enqueues `process_image` on django-q2, returns `job.id`. Atomic.
- `process_image(job_id)` — the q2 task body. Loads the `ScoringJob`, builds the detector from config (default `GoogleAIStudioDetector`), runs `PipelineRunner.run(...)` writing deliverables via `ScoringStorage`, stores the result JSON + paths on the job, sets `status="succeeded"` (or `failed` + error on exception). Maps `DetectionResult` → `ScoringResultDTO` for the stored JSON.
- `get_job(job_id, user_uuid) -> ScoringJobDTO` — read accessor enforcing owner-only access (raises if `user_uuid` mismatches; AGENTS.md §2 roles).

#### 4. DTO finalization + mapping

**File**: `src/domains/vision/dtos.py` (extend), `src/domains/vision/services.py`

**Intent**: Finalize `ScoringJobDTO` now that the model exists, and centralize `DetectionResult → ScoringResultDTO` mapping in services (DTOs cross boundaries; dataclasses stay internal).

**Contract**: `ScoringJobDTO(job_id, status, target_type, caliber_hint, result: ScoringResultDTO|None, error, created_at, completed_at)`. A private `_to_result_dto(DetectionResult) -> ScoringResultDTO` in services.

#### 5. Production integration test

**File**: `src/domains/vision/tests/test_services_q2.py`

**Intent**: Verify the enqueue→task→storage flow with the mock detector (no API calls, no live q2 worker — call `process_image` synchronously).

**Contract**: Create a `ScoringJob`, call `process_image(job_id)` directly (mock detector), assert status=succeeded, 3 deliverable paths set, `result` JSON parses with `count==5`. Assert `get_job` enforces owner-only (raises on user_uuid mismatch).

### Success Criteria:

#### Automated Verification:
- `uv run python src/manage.py makemigrations vision` produces only `0001_initial` (no unexpected models).
- `uv run python src/manage.py migrate` applies cleanly.
- `uv run pytest src/domains/vision/tests/test_services_q2.py` passes (uses `pytest-django`, mock detector).
- `uv run ruff check src/domains/vision/models.py src/domains/vision/services.py` passes.

#### Manual Verification:
- In a Django shell, `schedule_image_processing(...)` enqueues a job and `process_image` runs it end-to-end with the mock detector, writing the 3 deliverables under the storage bucket.

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 6: Standalone CLI (`__main__`)

### Overview

Ship the runnable standalone application: `uv run python -m src.domains.vision ...`. Loads `.env`, builds the chosen detector, runs `PipelineRunner` per image, prints the Jaccard eval table (diagnostic), writes `_summary.json`.

### Changes Required:

#### 1. Detector factory

**File**: `src/domains/vision/detectors/factory.py`

**Intent**: Centralize detector construction by name so the CLI and (later) the BFF share one mapping. Honors the "explicit choice, no failover" decision.

**Contract**: `class DetectorFactory` with `build(name: str, **kwargs) -> HoleDetector` supporting `"google"` → `GoogleAIStudioDetector`, `"ollama"` → `OllamaDetector`, `"mock"` → `MockDetector`; raises `ValueError` on unknown. Reads env defaults inside each detector, not the factory.

#### 2. `__main__.py`

**File**: `src/domains/vision/__main__.py`

**Intent**: The standalone entrypoint. argparse mirrors the cv/ `run.py` surface but points at the domain. Loads `.env` via python-dotenv BEFORE importing the detectors (so `GOOGLE_API_KEY` is present when `GoogleAIStudioDetector` constructs). Must NOT require Django (no `django.setup()`); pure-Python path only.

**Contract**:
- CLI: `python -m src.domains.vision [ids...] --detector {google,ollama,mock} --target-type ... --caliber ... --out ... --no-gt --debug --eval`
- Defaults: ids=[12,46,29,21], detector=google, target_type=air_pistol, out=`resources/train/intermediate_vision`, caliber per-image from `MetadataLoader` when `--eval` and not overridden.
- Flow: load_dotenv() → build detector via factory → per image: `PipelineRunner.run(...)` → collect result → if `--eval`: compute `score_jaccard` vs metadata.yml, print per-image table + mean. → write `_summary.json`.
- Prints `GOOGLE_API_KEY`/`OLLAMA_*` presence (not values) at start.

### Success Criteria:

#### Automated Verification:
- `uv run python -m src.domains.vision 12 --detector mock --out /tmp/vision_cli_test` exits 0 and writes 3 files + `_summary.json`.
- `uv run pytest src/domains/vision/tests/test_cli.py` passes (smoke test invoking `__main__` with `--detector mock` via subprocess or `runpy`, asserting files).
- Grep gate: `rg -n "^import cv|^from cv\b" src/domains/vision` returns no matches.

#### Manual Verification:
- `uv run python -m src.domains.vision 12 46 29 21 --detector google` runs end-to-end against Google AI Studio and prints a mean Jaccard in the ~0.6–0.8 range (matching the research's 0.638–0.799).
- `--detector ollama` runs against a local `ollama serve` + `gemma4:latest` without import errors (fidelity is expected lower — documented, not gated).

**Implementation Note**: After completing this phase and all automated verification passes, pause here for manual confirmation from the human that the manual testing was successful before proceeding to the next phase.

---

## Phase 7: Final verification & guardrails

### Overview

Run the full regression suite, confirm the architectural invariants (no cv/ imports, domain isolation), and confirm the production + CLI paths agree on numerics.

### Changes Required:

#### 1. No-cv-import guardrail test

**File**: `src/domains/vision/tests/test_no_cv_imports.py`

**Intent**: A CI-grep gate as a test so the invariant cannot regress silently. Scans the domain source tree for any `cv`/`cv.approaches`/etc. import.

**Contract**: `test_no_runtime_cv_imports()` walks `src/domains/vision/**/*.py`, parses imports, asserts none start with `cv`. Excludes the `tests/` fixtures if any deliberately reference cv/ for comparison (none expected).

#### 2. Full regression + integration pass

**Intent**: Run everything together as the final gate.

**Contract**: `uv run pytest src/domains/vision` (all tests green), `uv run ruff check src/domains/vision`, `uv run lint-imports`, `uv run python src/manage.py check`.

### Success Criteria:

#### Automated Verification:
- `uv run pytest src/domains/vision` — full suite green.
- `uv run ruff check .` — clean.
- `uv run lint-imports` — domain isolation contract holds.
- `uv run python src/manage.py migrate --check` — no drift.
- `rg -n "^import cv|^from cv\b" src/domains/vision` — empty.

#### Manual Verification:
- Cross-path numerics: a `--detector mock` run via the CLI and a `process_image` call via Django shell produce identical `target_ring1_px` + invert err on img 12.
- Final review of the plan's *What We're NOT Doing* list with the user to confirm scope held.

---

## Testing Strategy

### Unit Tests:
- MockDetector pattern (Phase 1).
- Detector `TargetAnalysis → DetectionResult` mapping + env handling, mocked LLMs (Phase 3).
- Schema/prompt snapshot equivalence to cv/ (Phase 3).
- Eval functions: Jaccard/count-match edge cases (empty multisets, perfect match, count mismatch) (Phase 4).

### Integration Tests:
- `PipelineRunner` end-to-end with mock detector (Phase 4).
- `services.process_image` + storage + model round-trip with mock detector (Phase 5).
- CLI smoke test (Phase 6).

### Regression Tests (the load-bearing gate):
- Geometry numerical identity on all 10 train images: invert err < 1e-12 px, frozen r1@1024 table, frozen ecc table, frozen defense-layer classification (Phase 2).

### Manual Testing Steps:
1. Visual: `image_1024` for img 12 byte-matches the cv/ fused output.
2. Visual: `_marked.png` magenta dots + ring frame + scores render.
3. Live: `--detector google` on the 4-image set produces mean Jaccard ~0.6–0.8.
4. Live: `--detector ollama` runs without import/runtime errors against local `ollama serve`.
5. Django shell: `schedule_image_processing` → `process_image` round-trip writes 3 deliverables + updates the job.

## Performance Considerations

- The geometry pipeline is CPU-bound (~seconds/image for the scipy refine); unchanged from cv/. No optimization needed for the port — fidelity first.
- q2 cap (max 3 concurrent tasks, AGENTS.md §2) is a deployment config; no in-process throttling in code.
- LLM cost: `gemini-3.5-flash-lite` ~50 output tokens/target (~6,600 targets/day to spend $1 paid); free tier covers spike + early production (Risk #47, documented). Ollama is local/free but slower and lower-fidelity.

## Migration Notes

- **No data migration** — this is greenfield for the vision domain (`ScoringJob` table is new, empty). `0001_initial` creates it.
- **`cv/` is NOT removed.** It stays as the frozen historical reference (commit `76f6fc4`). A future change may delete it once the domain is proven in production; out of scope here.
- **No BFF changes** — `schedule_image_processing` is exposed but not yet called by any router. The follow-up BFF change wires the upload endpoint + `transaction.atomic` orchestration.

## References

- Related research: `context/changes/cv-service-boundary/research-ai-detection.md` (§ "The hole-detection algorithm (summary)", § "Phase 3 Step 2 (COMPLETE)", § "Final tuned parameters", § "Final per-image results")
- Frame brief (superseded on the algorithm question by the research; kept for dataset characterization): `context/changes/cv-service-boundary/frame.md`
- Source reference (frozen): `cv/approaches/full_pipeline/{pipeline.py,run.py}` and the closure listed in *Current State Analysis*.
- Naming convention: `context/foundation/lessons.md` § "One class per file, matching filename"
- Architecture rules: `AGENTS.md` §1, §5 (boundaries), §6 (atomicity), §6.1 (import-linter)

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Foundation, conventions & test harness

#### Automated
- [x] 1.1 `.env.example` extended with OLLAMA_HOST/OLLAMA_MODEL; python-dotenv in pyproject.toml — 217bafd
- [x] 1.2 `uv run ruff check src/domains/vision` clean (ports.py, dtos.py, geometry/calibration.py, detectors/*) — 217bafd
- [x] 1.3 `uv run lint-imports` passes (domain isolation unaffected) — 217bafd
- [x] 1.4 `uv run pytest src/domains/vision/tests -k mock_detector` passes — 217bafd
- [x] 1.5 Grep gate: `rg -n "^import cv|^from cv\b" src/domains/vision` empty — 217bafd

#### Manual
- [x] 1.6 `.env.example` has all three vars; `python -c "import dotenv"` succeeds; MockDetector returns [10,7,7,7,7]

### Phase 2: Geometry port + numerical-identity gate

#### Automated
- [x] 2.1 `uv run pytest src/domains/vision/tests/test_geometry_regression.py` passes on all 10 images — 7276fcb
- [x] 2.2 `uv run ruff check src/domains/vision/geometry` clean — 7276fcb
- [x] 2.3 Grep gate: `rg -n "^import cv|^from cv\b" src/domains/vision` empty — 7276fcb

#### Manual
- [x] 2.4 GeometryPipeline img-12 image_1024 byte-matches cv/ fused output; defense-layer classifications match research table

### Phase 3: Detector strategies (Google AI Studio + Ollama)

#### Automated
- [x] 3.1 `uv run pytest src/domains/vision/tests/test_google_detector.py src/domains/vision/tests/test_ollama_detector.py` passes (mocked) — 37948df
- [x] 3.2 `uv run ruff check src/domains/vision/detectors` clean — 37948df
- [x] 3.3 Schema/prompt snapshot test byte-identical to cv/ — 37948df

#### Manual
- [x] 3.4 OllamaDetector constructs against local `ollama serve` + `gemma4:latest`

### Phase 4: Pipeline runner + deliverables + eval tooling

#### Automated
- [x] 4.1 `uv run pytest src/domains/vision/tests/test_pipeline_runner.py` passes (mock detector) — e5e08f5
- [x] 4.2 `uv run ruff check src/domains/vision/pipeline src/domains/vision/eval` clean — e5e08f5
- [x] 4.3 Grep gate: `rg -n "^import cv|^from cv\b" src/domains/vision` empty — e5e08f5

#### Manual
- [x] 4.4 PipelineRunner img-12 `_marked.png` renders magenta dots + ring frame + score labels

### Phase 5: Django production path (models + q2 + services + storage)

#### Automated
- [x] 5.1 `makemigrations vision` produces 0001_initial only — 9cf2cee
- [x] 5.2 `uv run python src/manage.py migrate` applies cleanly — 9cf2cee
- [x] 5.3 `uv run pytest src/domains/vision/tests/test_services_q2.py` passes (mock detector) — 9cf2cee
- [x] 5.4 `uv run ruff check src/domains/vision/models.py src/domains/vision/services.py` clean — 9cf2cee

#### Manual
- [x] 5.5 Django shell: schedule_image_processing → process_image writes 3 deliverables under storage bucket

### Phase 6: Standalone CLI (`__main__`)

#### Automated
- [x] 6.1 `uv run python -m src.domains.vision 12 --detector mock --out /tmp/vision_cli_test` exits 0, writes 3 files + _summary.json — ee2368c
- [x] 6.2 `uv run pytest src/domains/vision/tests/test_cli.py` passes — ee2368c
- [x] 6.3 Grep gate: `rg -n "^import cv|^from cv\b" src/domains/vision` empty — ee2368c

#### Manual
- [ ] 6.4 `--detector google` on 4-image set prints mean Jaccard ~0.6–0.8; `--detector ollama` runs without errors

### Phase 7: Final verification & guardrails

#### Automated
- [x] 7.1 `uv run pytest src/domains/vision` full suite green — aa11083
- [x] 7.2 `uv run ruff check .` clean — aa11083
- [x] 7.3 `uv run lint-imports` holds — aa11083
- [x] 7.4 `uv run python src/manage.py migrate --check` no drift — aa11083
- [x] 7.5 `rg -n "^import cv|^from cv\b" src/domains/vision` empty (guardrail test green) — aa11083

#### Manual
- [ ] 7.6 Cross-path numerics: CLI mock run vs Django process_image agree on target_ring1_px + invert err (img 12); NOT-doing list reviewed with user
