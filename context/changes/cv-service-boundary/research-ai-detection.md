---
date: 2026-07-21T18:00:00Z
researcher: krzysztofkruk
git_commit: 899921e99532a95b1cebbe090bbfb09376b0f945
branch: master
repository: target-o-meter
topic: "Phase 1 deterministic pipeline + 3-agent normalization experiment — multiring homography wins; discovery: needs fused multiring-detection + iteredge-refinement"
tags: [research, codebase, cv, llm, vlm, langchain, gemma, homography, multiring, iteredge, strategy-pattern, dependency-inversion, issf, paper-targets]
status: complete
last_updated: 2026-07-21
last_updated_by: krzysztofkruk
phase: 1 complete (deterministic pipeline + experiment); phase 2 next (combined algorithm); phase 3 pending (LangChain)
---

# Research: AI detection — deterministic pipeline + 3-agent normalization experiment

**Date**: 2026-07-21
**Researcher**: krzysztofkruk
**Git Commit**: [899921e](https://github.com/krkruk/target-o-meter/commit/899921e99532a95b1cebbe090bbfb09376b0f945)
**Branch**: master
**Repository**: [krkruk/target-o-meter](https://github.com/krkruk/target-o-meter)

> **This document is the handoff artifact for Phase 3 (LangChain implementation).** It must be self-contained — another LLM session picking up Phase 3 should not need to scroll this conversation's history.

## Research Question

User direction (Phase 1): pivot the hole-detection stage from classical CV (which hit a hard wall at score-Jaccard ≈ 0.255 across 10 prior iterations — see `research.md` and `research-blob-detection.md`) to a **vision-language model**. Use LangChain as the orchestration framework with `gemma-4-31b-it` as the model. The classical pipeline keeps the stages it has already solved (EXIF-orient, target localization, two-anchor calibration, ISSF scoring) and produces a clean 1024×1024 fronto-parallel normalized image with the bullseye at a known location. The LLM receives that normalized image and returns structured XY-coordinates of detected bullet holes plus a per-hole score.

User direction (this iteration): build the deterministic Phase-1 pipeline with a **strategy + dependency-inversion** architecture so the LLM detector can drop in later behind a stable interface. Then run a 3-agent parallel experiment to redesign the normalization (localization + warp + 1024 frame), because the first attempt produced elliptical rings instead of circles. Pick a winner, capture the discovery, and plan the next iteration.

## Summary

**Phase 1 — deterministic pipeline: COMPLETE.** Five new modules under `cv/` (detector_base, normalize, mock_detector, pipeline, run_pipeline) define the strategy seam and produce 8 files per image under `resources/train/intermediate_llm/`. Inversion math is provably correct (bullseye round-trip error ~2.3e-13 px on all 10 train images). User verified the magenta-dot visualization works.

**Phase 2 — 3-agent normalization experiment: COMPLETE.** Three parallel agents implemented independent approaches under `cv/approaches/{multiring, singleellipse, iteredge}/`:

| Agent | Approach | Status | User verdict |
|---|---|---|---|
| A — multiring | Multi-ring concentric ellipses → projective homography via circular-points method | COMPLETE | **WINNER** — best of the three |
| B — singleellipse | Single black-disc ellipse → focal-length decomposition | Aborted mid-implementation (incomplete outputs) | Not graded |
| C — iteredge | Iterative patch-against-edges via scipy.optimize.least_squares on 8-DOF homography | COMPLETE | Solid optimizer; weaker detector than multiring |

**Key discovery (user's words)**: *"In general, I believe the application lacks the minimization between the edge detected circles to adjust the perspective parameters and minimize the differences between the real picture and the estimated circles."*

**Translation**: multiring's strength is the **detection** (it correctly rejects printed logos via black-disc contrast — image 29 fixed; it finds 7-13 concentric rings). Its weakness is the **warp** (no iterative refinement; rings still come out slightly off). Iteredge's strength is the **refinement** (iteratively minimizes residuals between predicted rings and detected edges). Its weakness is the **detection** (relies on the existing `crop_to_target` + `calibrate` initialization, which fails on image 29).

**Next iteration (Phase 2.5)**: **fuse multiring's detection with iteredge's refinement.** The user's literal prescription: *"grayscale, then Gauss blur, then detect edges, threshold so the edges are visible, then finally perform matching for concentric circles. Then you attempt to match the edge detected image onto the generated rings to minimize difference between those. You tinker with the parameters, iteratively."*

**Specific fixes also required**:
- Image 12: normalization cropped out the 1-ring (outermost ring). MUST preserve the full 1-ring boundary in the 1024 frame.
- Image 21: normalization cropped out 3 of 5 slug holes. MUST preserve ALL actual holes in the 1024 frame, even if they're outside ring 1.

## User's locked-in decisions (from 10-question interview)

These answers must be respected by every subsequent iteration, including Phase 3.

### Phase-1 decisions (architecture)

| Q | Decision |
|---|---|
| Q1 — Architecture | **Strategy + dependency inversion.** Same output structure across detectors. Two LangChain strategies to wire in Phase 3: `LangChainAIStudioDetector` (primary, env-var token already present) and `LangChainOllamaDetector` (fallback, `ollama` server running locally, model `gemma4:latest`). |
| Q2 — Vision-token budget | **1120 (max detail)** for the spike; re-measure at 560 once F1 numbers exist. |
| Q3 — Image fidelity | **Plain grayscale-as-RGB** 1024×1024 of the warped crop. Test multi-channel texture-RGB only if zero-shot F1 is poor. |
| Q4 — Normalization layout | **Bullseye at (512, 512); 1-ring boundary at radius 500 px.** Symmetric padding outside. The LLM sees a known fixed geometric frame regardless of source resolution or target type. |
| Q5 — Scoring authority | **LLM scores.** Structured output includes (x, y, score) per hole. Classical ISSF line-break scoring is computed in parallel for diagnostic comparison only. |
| Q6 — Few-shot | **Zero-shot first.** Add 2-3 few-shot examples from `{6, 12}` only if F1 < 0.5. Never use `_marked.jpg` as LLM input (would teach magenta-finding, not hole-finding). |
| Q7 — Output schema | **Rich**: `{x, y, score, confidence}` per hole + top-level `{target_type, notes}`. |
| Q8 — Prompt variants | **Unified prompt** with `{target_type}` placeholder and conditional ISSF-rules block. |
| Q9 — Caliber hint | **Pass when known**; omit on mixed-caliber targets (image 31). |
| Q10 — Feasibility gate | **3-tier**: plumbing > F1≥0.5 > F1≥0.75. |

### Phase-2 decisions (experiment setup)

| Q | Decision |
|---|---|
| Approaches | **Multi-ring + single-ellipse + iterative-edge-patch.** EXPLICITLY NOT 4-corner paper-edge detection — paper target may be partially photographed. |
| Localization scope | **Rework both localization and warp+normalize.** Image 29's logo-pickup and image 6's background are localization failures. |
| Test images | **4 representatives first** (12 gold, 46 gold, 29 disaster, 21 cropped-holes); expand to all 10 for the winning approach. |
| Output spec | **Same 8 files as Phase 1 + 1 additional edge-detection diagnostic** = 9 files per image. The `_02b_detect.png` shows cropped image with edges overlaid + concentric circles/ellipses the algorithm fit. |
| Constraints | **Open deps, create new files only.** May import from `cv/blob_detect.py` but must NOT modify it. `scipy` is the main addition. |

## Phase 1 — Deterministic pipeline (COMPLETE)

### Architecture: strategy + dependency inversion

```
                ┌─────────────────────────────────────────────────┐
                │           cv/detector_base.py                   │
                │                                                 │
                │   @dataclass DetectedHole:                      │
                │       x: int (0..1024)                          │
                │       y: int (0..1024)                          │
                │       score: int (0..10)                        │
                │       confidence: float (0..1)                  │
                │                                                 │
                │   @dataclass DetectionResult:                   │
                │       holes: list[DetectedHole]                 │
                │       target_type: "air_pistol" | "precision_pistol"
                │       detector_name: str                        │
                │       notes: str | None                         │
                │       raw: dict | None                          │
                │                                                 │
                │   class HoleDetector(ABC):                      │
                │       @property name(self) -> str               │
                │       detect(self, image_1024, target_type,     │
                │                caliber_hint) -> DetectionResult │
                └─────────────────────────────────────────────────┘
                    ▲                              ▲
                    │ implements                   │ implements
        ┌───────────┴──────────┐       ┌───────────┴──────────────┐
        │ cv/mock_detector.py  │       │ cv/langchain_detector.py │
        │                      │       │ (Phase 3 — not built)    │
        │ MockDetector         │       │                          │
        │ returns fixed 5-hole │       │ LangChainAIStudioDetector│
        │ pattern: bullseye +  │       │ LangChainOllamaDetector  │
        │ 4 cardinals at d=200 │       │                          │
        └──────────────────────┘       └──────────────────────────┘
```

The seam is `HoleDetector.detect(image_1024, target_type, caliber_hint) -> DetectionResult`. Everything before this call is geometry (intake → localize → calibrate → warp → normalize to 1024); everything after is scoring + visualization. The LLM swap changes one function call.

### Files created in Phase 1

| File | Lines | Role |
|---|---|---|
| `cv/detector_base.py` | 91 | Strategy ABC + DetectedHole/DetectionResult dataclasses |
| `cv/normalize.py` | 138 | `wrap_warp`, `to_llm_square`, `norm_to_crop`, `norm_to_source`, `self_test_inversion` |
| `cv/mock_detector.py` | 65 | `MockDetector` returns fixed 5-hole pattern (bullseye + 4 cardinals at d=200) |
| `cv/pipeline.py` | 167 | `run_pipeline` orchestration + 7 viz helpers |
| `cv/run_pipeline.py` | 84 | CLI driver `uv run python -m cv.run_pipeline [ids...] --detector mock` |
| **Total** | **545** | |

### Phase 1 results (10 train images, mock detector)

All 10 images processed cleanly. Bullseye inversion self-test error: 2.3e-13 px (floating-point precision floor). User verified outputs.

The mock returns the same fixed pattern `[10, 7, 7, 7, 7]` for every image; classical scoring (computed from each image's actual `s_px/r_bull_px`) varies — diagnostic only.

### Phase 1 user feedback that triggered Phase 2

The 5-stage pipeline (`intake → crop_to_target → calibrate → warp_fronto_parallel → to_llm_square`) produced 1024×1024 images where the rings remained elliptical. Detailed per-image feedback:

- **1.jpg**: warp skewed — top of ring 4 taller than bottom. *"taking into account the middle black disk is not enough. You need to detect at least 3 circles to calibrate the orthogonal transformation."*
- **4.jpg**: outer ring distances not equidistant in pixels.
- **6.jpg**: nearly orthogonal source; should crop tighter (paper only, no background).
- **10.jpg**: warp inconsistent — top sheared too much, bottom too little.
- **12.jpg**: ★ PERFECT — gold standard.
- **19.jpg**: nothing to crop; warp reasonable; llm_input zoomed to 9/10 rings only — good for tight shots ONLY IF no actual holes are dropped.
- **21.jpg**: warp ok; **llm_input cropped out 3 holes** — unacceptable.
- **29.jpg**: ★ ABSOLUTE DISASTER — localization picked up a printed LOGO.
- **31.jpg**: warp rather good; llm_input cropped the 9x19mm shots in the top.
- **46.jpg**: ★ NEAR PERFECT — gold standard.

## Phase 2 — 3-agent normalization experiment

### Goal
Rework BOTH localization (`crop_to_target`) AND warp+normalize. Produce 9 files per image including the `_02b_detect.png` diagnostic. Test on 4 representative images: 12, 46 (gold standards) + 29, 21 (disasters).

### Three approaches

#### Approach A — Multi-ring concentric ellipses → projective homography (Agent A; **WINNER**)

Path: `cv/approaches/multiring/` (8 files, 1955 LOC, scipy dep added).

**Algorithm**:
1. **Localize**: downscale → Sobel magnitude → multi-band HoughCircles → greedily cluster by center proximity → score each cluster by `(distinct radii) × (concentricity) × log(enclosed area) × (1 + black-disc contrast)`. **The black-disc contrast term is what rejects printed logos on image 29** (logos are uniformly dark; real targets have a strong dark-disc-to-white-paper transition).
2. **Detect rings**: CLAHE → polar unwrap around init bullseye → Sobel radial profile → peak detection → sub-pixel refinement → bounded `scipy.optimize.least_squares` fit of a 4-parameter ellipse model `r(θ) = (a·b) / sqrt((b·cos(θ-α))² + (a·sin(θ-α))²) + r0`. Bounds on (a, b, r0) are critical — without them the fit converges to degenerate semi-axes (11000+ px).
3. **Homography**: each ellipse becomes a 3×3 conic matrix. The upper-left 2×2 blocks of concentric ellipses are proportional (shared axes), so average the normalized blocks to get a single image-plane metric `Q`. Circular points are the complex-conjugate roots of `A x² + 2B xy + C y² = 0` derived from `Q`. `Q^{-1/2}` whitens the metric and turns every shared-axis ellipse into a circle. **Mathematically affine** for coplanar concentric circles — sufficient for the visual success criterion but cannot recover true projective tilt.
4. **Warp + normalize**: standard.

**Strengths**:
- Image 29 (logo disaster): **completely solved**. Bullseye lands 28 px from GT centroid on the actual target.
- 7-13 rings detected per image.
- Ring eccentricity after warp: max 0.047, mean 0.016 (essentially perfect circles on gold standards).

**Weaknesses (per user feedback)**:
- Image 12: cropped out the 1-ring (over-aggressive normalization).
- Image 21: cropped out 3 of 5 slug holes — **unacceptable per user**.
- No iterative refinement — rings still come out slightly off despite clean detection.

#### Approach B — Single black-disc ellipse → focal-length decomposition (Agent B)

Path: `cv/approaches/singleellipse/` (8 files, aborted mid-implementation; outputs incomplete).

The math: tilt magnitude θ = arccos(b/a), tilt direction = major-axis angle, resolve front/back via "up is up" prior. Known to be theoretically weaker than multi-ring (under-determined; outer rings extrapolated rather than directly fitted). Agent aborted; not graded by user.

#### Approach C — Iterative patch-against-edges (Agent C)

Path: `cv/approaches/iteredge/` (10 files, 1596 LOC, scipy dep added).

**Algorithm**:
1. **Localize**: uses `cv.blob_detect.crop_to_target` + ring-pattern validation. Accepts the default only when `peaks_aligned ≥ 5` AND the ring pattern is consistent. Falls back to multi-candidate search otherwise.
2. **Edges**: Sobel magnitude + Canny binary. Per-pixel "ring-weight" `1 - |∇I · r̂|` favors tangential (ring-stroke) edges over radial (digit/hole) edges.
3. **Model**: **8-DOF homography** (justified: exact mathematical object for projective distortion; phone-camera focal length unknown; lens distortion can be partially absorbed by extra DOF).
4. **Energy**: for each of 640 predicted ring sample points (10 rings × 64 angles), map back via H⁻¹, look up the distance-transform value. Residual = min(DT, 30) for in-bounds; 1000 for out-of-bounds. Plus regularization: perspective terms penalized, affine terms anchored to init, determinant barrier.
5. **Optimizer**: `scipy.optimize.least_squares` with `method='trf'` (bounds + robust `soft_l1` loss). Coarse-to-fine across 4 stages (smoothed edge magnitude → raw DT). Safety check: revert to init if optimization makes things worse.

**Strengths**:
- All 4 images converge cleanly (92-181 iterations).
- Image 29: localizer's ring-pattern validation correctly rejects the logo; 25% data improvement after optimization.
- Image 21: wider crop keeps all 5 mock holes; optimizer corrects perspective.
- Final ring anisotropies 1.01-1.17 (median 1.03) — essentially circular.

**Weaknesses (per user preference)**:
- Detector is weaker than multiring's (relies on existing `crop_to_target` initialization rather than black-disc-contrast scoring).
- The user's verdict: multiring's detection is the right foundation; iteredge's optimizer is the right refinement — but as standalone approaches, neither is complete.

### Phase 2 results — user grading (verbatim quotes)

> *"Definitely multiring homography is the best out of all these options."*

> **12.jpg**: *"detect - you should perform a typical CV processing: grayscale, then Gauss blur, then detect edges, threshold so the edges are visible, then finally perform matching for concentric circles. Then you attempt to match the edge detected image onto the generated rings to minimize difference between those. You tinker with the parameters, iteratively. llm_input is fine although you cropped too much of the area (no longer the 1-ring, the most outer one)."*

> **21.jpg**: *"warp is satisfactory, the lanes seem to be rather equal, llm_input is a disaster, you cropped most of the image keeping only two holes visible rather than all 5 slug holes. This is not acceptable."*

> **29.jpg**: *"warp: you've correctly identified the target, which is an improvement, llm_input: perfect cropping, all the target in sight, cropped most of the background - well done."*

> **Discovery**: *"In general, I believe the application lacks the minimization between the edge detected circles to adjust the perspective parameters and minimize the differences between the real picture and the estimated circles."*

## Recommended next iteration — Phase 2.5: fused multiring + iteredge

### The discovery, technically

The user is describing classical **model-to-image registration**: define a parametric warp, predict where the ring strokes should land, measure the mismatch against detected edges, iterate. This is what iteredge does. But iteredge's *detector* is weak; multiring's *detector* is strong. **Fuse them**:

```
DETECTION (multiring)              REFINE (iteredge)              NORMALIZE (fix bugs)
─────────────────────              ──────────────────              ────────────────────
1. grayscale                       1. take multiring's H as init   1. ensure 1-ring is
2. Gaussian blur                   2. predict 10 ring strokes      INSIDE the 1024 frame
3. Canny edges                         under H^-1                  (don't crop it out)
4. HoughCircles multi-band         3. compute distance-transform   2. ensure ALL actual
5. cluster by center                  of edges                    holes are inside the
6. score by black-disc contrast    4. energy = sum of min(DT,30)  frame (don't crop out
7. reject logos (image 29 fix)        over predicted points        holes — image 21 fix)
8. bounded ellipse fit per ring    5. + regularization:           3. target_ring1_px in
9. circular-points method              perspective penalty,         [450, 500] depending
   → initial H                         affine anchor,               on hole extent
                                      determinant barrier       4. bullseye at (512, 512)
                                   6. scipy.optimize.least_squares
                                      with soft_l1 + trf
                                   7. coarse-to-fine: smoothed
                                      edges → raw edges
                                   8. safety check: revert if
                                      worse than init
```

### Concrete fixes for the user's specific complaints

| Image | User complaint | Root cause | Fix |
|---|---|---|---|
| 12 | "you cropped too much of the area (no longer the 1-ring, the most outer one)" | `target_ring1_px = 500` with `r_ring1_warped ≈ 1068.5` (image 46); after resize, ring 1 lands at radius 500, but the source's ring 1 was already clipped by the original photo. The 1024 canvas shows the clipped ring 1 at the very edge or beyond. | Use `target_ring1_px = 470` (or smaller) to leave a 30+ px margin around ring 1. Verify ring 1 is fully visible in `_04_llm_input.png` before accepting. |
| 21 | "you cropped most of the image keeping only two holes visible rather than all 5 slug holes" | Slug target anisotropy 1.66; ring 1 boundary doesn't contain all 5 shots (some are outside ring 1, which is normal — they score 0). When normalization puts ring 1 at radius 500, outside-ring-1 holes fall outside the 1024 frame. | **NEW**: detect actual hole positions (use `cv.gt.magenta_centers` on `_marked.jpg` for EVAL ONLY; in production, the user will mark hole-centers in a UI). Set `target_ring1_px` such that the outermost actual hole is at radius ≤ 490 (10 px margin). Fallback: if no hole info available, use `target_ring1_px = 420` (puts ring 1 well inside frame, with room for outside-ring shots). |
| 29 | (was the disaster; now fixed by multiring) | Multiring's black-disc contrast term correctly rejects the logo. **Keep this.** | None — already fixed. |

### Implementation plan for Phase 2.5

```
cv/approaches/fused/                       # NEW directory
    __init__.py
    localize.py                            # import from multiring (logo-rejecting)
    detect_rings.py                        # import from multiring (bounded ellipse fit)
    homography.py                          # import from multiring (circular-points initial H)
    refine.py                              # import from iteredge (energy + optimizer)
    normalize.py                           # NEW logic: adaptive target_ring1_px
    pipeline.py                            # orchestration
    run.py                                 # CLI: uv run python -m cv.approaches.fused.run
```

Test on all 10 train images (not just the 4 representatives). Success criteria:
- Image 12: `_04_llm_input.png` shows full 1-ring boundary inside the frame.
- Image 21: `_04_llm_input.png` shows all 5 slug holes inside the frame.
- Image 29: localization still finds the actual target (regression check).
- Image 46: regression check on gold standard.
- All images: bullseye inversion self-test < 0.01 px.
- All images: ring eccentricity after warp < 0.05 (mean) and < 0.10 (max).

## Code references

### Phase 1 files (deterministic pipeline — STABLE, do not modify)

- [`cv/detector_base.py`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/cv/detector_base.py) — strategy ABC + dataclasses. **The seam.**
- [`cv/normalize.py`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/cv/normalize.py) — `wrap_warp`, `to_llm_square`, `norm_to_crop`, `norm_to_source`, `self_test_inversion`, `TransformMeta` dataclass.
- [`cv/mock_detector.py`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/cv/mock_detector.py) — fixed 5-hole pattern for plumbing tests.
- [`cv/pipeline.py`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/cv/pipeline.py) — `run_pipeline` orchestration.
- [`cv/run_pipeline.py`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/cv/run_pipeline.py) — CLI.

### Phase 2 files (3-agent experiment — pick & fuse for Phase 2.5)

- [`cv/approaches/multiring/`](https://github.com/krkruk/target-o-meter/tree/899921e99532a95b1cebbe090bbfb09376b0f945/cv/approaches/multiring) — **WINNER** detection + initial H. 8 files, 1955 LOC. Key files:
  - `localize.py` — black-disc-contrast logo rejection (image 29 fix).
  - `detect_rings.py` — bounded 4-parameter ellipse fit per ring (7-13 rings detected).
  - `homography.py` — circular-points method → initial affine H.
- [`cv/approaches/iteredge/`](https://github.com/krkruk/target-o-meter/tree/899921e99532a95b1cebbe090bbfb09376b0f945/cv/approaches/iteredge) — **WINNER** refinement. 10 files, 1596 LOC. Key files:
  - `optimize.py` — energy function + `scipy.optimize.least_squares` with `trf` method.
  - `model.py` — 8-DOF homography parameterization + ring prediction.
  - `edges.py` — Canny + Sobel + ring-weight + distance transform.
- [`cv/approaches/singleellipse/`](https://github.com/krkruk/target-o-meter/tree/899921e99532a95b1cebbe090bbfb09376b0f945/cv/approaches/singleellipse) — aborted; not used.

### Existing files (do not modify; may import)

- [`cv/blob_detect.py`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/cv/blob_detect.py) — `to_gray`, `crop_to_target`, `calibrate`, `warp_fronto_parallel`, `score_holes`, `deliverable`. The "solved" classical stages.
- [`cv/gt.py`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/cv/gt.py) — `load_bgr` (EXIF-aware), `magenta_centers` (eval-only GT).
- [`cv/eval_blob.py`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/cv/eval_blob.py) — method-agnostic eval harness.

### Output directories

- `resources/train/intermediate_llm/` — Phase 1 baseline (10 images × 8 files each = 80 files).
- `resources/train/intermediate_multiring/` — Phase 2 multiring outputs (4 images × 9 files = 36 files).
- `resources/train/intermediate_iteredge/` — Phase 2 iteredge outputs (4 images × 9 files = 36 files).
- `resources/train/intermediate_fused/` — Phase 2.5 outputs (TBD; all 10 images).

### Data assets

- [`resources/train/{1,4,6,10,12,19,21,29,31,46}.jpg`](https://github.com/krkruk/target-o-meter/tree/899921e99532a95b1cebbe090bbfb09376b0f945/resources/train) — 10 base images (LLM input source).
- [`resources/train/*_marked.jpg`](https://github.com/krkruk/target-o-meter/tree/899921e99532a95b1cebbe090bbfb09376b0f945/resources/train) — 10 magenta-marked GT (eval-only).
- `resources/train/intermediate_blob/gt/*_gt.npy` — cached GT centers, shape `(N, 2)` source-px.
- [`resources/paper_targets/metadata.yml`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/resources/paper_targets/metadata.yml) — score multisets for all 46 images.

## Phase 3 preview — LangChain implementation

Phase 3 begins after Phase 2.5 (fused algorithm) produces clean 1024×1024 outputs on all 10 train images. The LLM swap is then a single new module that plugs into the existing strategy seam.

### What to build in Phase 3

```
cv/langchain_detector/__init__.py
cv/langchain_detector/aistudio.py        # LangChainAIStudioDetector
cv/langchain_detector/ollama.py          # LangChainOllamaDetector (fallback)
cv/langchain_detector/schema.py          # Pydantic models for with_structured_output
cv/langchain_detector/prompt.py          # Unified prompt builder (target_type placeholder)
cv/langchain_detector/fallback.py        # AIStudio -> Ollama retry wrapper
```

Update `cv/run_pipeline.py`'s `DETECTORS` dict to register the new strategies.

### Dependencies to add

```toml
"langchain>=0.3",
"langchain-google-genai>=2.0",   # for AI Studio
"langchain-ollama>=1.1",         # for local fallback
"pydantic>=2.0",                 # for with_structured_output
```

The token for AI Studio is already in `.env.example` as `GOOGLE_API_KEY=...` (env var). Ollama server is already running locally; model `gemma4:latest` is already pulled.

### Pydantic schema for `with_structured_output`

Per Q5/Q7 (LLM scores; rich schema):

```python
from pydantic import BaseModel, Field
from typing import Literal

class DetectedHole(BaseModel):
    x: int = Field(..., ge=0, le=1024, description="pixel x of hole center in 1024x1024 frame")
    y: int = Field(..., ge=0, le=1024, description="pixel y of hole center")
    score: int = Field(..., ge=0, le=10, description="ISSF score 0-10 (X = 10)")
    confidence: float = Field(..., ge=0.0, le=1.0, description="model's confidence 0-1")

class TargetAnalysis(BaseModel):
    holes: list[DetectedHole]
    target_type: Literal["air_pistol", "precision_pistol"]
    notes: str | None = None
```

Wrap in a top-level object (not bare `list[DetectedHole]`) — top-level objects are more reliably produced by open VLMs than bare arrays.

### Unified prompt builder (per Q8)

Single prompt with `{target_type}` placeholder and conditional ISSF-rules block. The prompt MUST teach:
- ISSF line-break rule: "a hit touching the higher-value ring line is awarded the higher value."
- The fixed geometric frame: bullseye at (512, 512), 1-ring boundary at radius 500 px, ring step ≈ 55.5 px.
- What counts as a hole vs what doesn't (ring strokes, printed digits, paper folds, shadow — all NOT holes).
- Caliber hint (when provided): expected hole radius in 1024-px units.
- Negative guidance: do NOT report ring strokes as holes; do NOT report printed ring numbers as holes.

### Serving-path details (per Q1)

- **Primary: Google AI Studio** via `langchain-google-genai`'s `ChatGoogleGenerativeAI`. Model ID `gemma-4-31b-it`. Vision-token budget 1120 (max detail per Q2). Free tier sufficient for spike.
- **Fallback: local Ollama** via `langchain-ollama`'s `ChatOllama`. Model `gemma4:latest`. Triggered when AI Studio call fails (network error, rate limit, malformed response after retry).

The fallback wrapper tries AI Studio first; on any exception, retries once; on second failure, falls back to Ollama. Records which path served each request in `DetectionResult.raw["served_by"]`.

### Gemma 4 31B-it facts (verified — see `research-llm-pivot.md` §Critical correction)

- Released Apr 2 2026, Apache 2.0, native multimodal (text + image, no audio on 31B).
- 256K context. Per-image vision-token budget: 70/140/280/560/1120.
- "Pointing" capability explicitly listed in model card.
- AI Studio URL: `aistudio.google.com/prompts/new_chat?model=gemma-4-31b-it`.
- HF canonical ID: `google/gemma-4-31B-it` (capital B; case-insensitive on AI Studio).

### Phase 3 success criteria (per Q10)

3-tier gate on the 10-image train set:
- **Plumbing success** (must pass): end-to-end pipeline runs without exceptions; structured-output parsing succeeds on ≥9/10 images; >0 TP across train set.
- **Encouraging** (proceed to planning): mean F1 ≥ 0.50 OR score-Jaccard ≥ 0.50 (≥2× classical baseline of 0.255).
- **Resounding success** (fast-track planning): mean F1 ≥ 0.75 AND score-Jaccard ≥ 0.75 on at least 7/10 images.

Run each image 3× and report mean ± std (LLM outputs are stochastic).

## Architecture decisions to preserve across all phases

1. **Strategy + dependency inversion.** `HoleDetector` is the only seam. Swapping detectors never touches geometry.
2. **Same `DetectionResult` schema across all detectors** (mock, LangChain-AIStudio, LangChain-Ollama, future DL).
3. **Magenta GT is eval-only.** NEVER pass `_marked.jpg` to the LLM (would teach magenta-finding). Few-shot examples use `(base_image, JSON_of_centers)` only.
4. **Classical scoring always computed in parallel** as a diagnostic, never authoritative (Q5: LLM scores are authoritative).
5. **Open deps, create new files only.** `cv/blob_detect.py`, `cv/gt.py`, `cv/eval_blob.py` are stable. Add deps via `uv add`.
6. **Inversion math must always round-trip.** Self-test `bullseye_invert_err_px < 0.01` is the regression gate.
7. **Per-image outputs preserve the 9-file convention** (Phase 2+) including the `_02b_detect.png` diagnostic.

## Risk register (additions)

| # | Risk | Source | Mitigation |
|---|---|---|---|
| 32 | Normalization crops out actual holes (image 21 root cause) | Phase 2 user feedback | Adaptive `target_ring1_px`: detect hole extent (via magenta GT in eval, via UI marking in production) and shrink ring-1 px to leave margin |
| 33 | Normalization crops out the 1-ring boundary (image 12 root cause) | Phase 2 user feedback | Enforce `target_ring1_px ≤ 470` default; verify ring 1 visible in `_04_llm_input.png` |
| 34 | Multiring's circular-points H is mathematically affine; cannot recover true projective tilt | Multiring agent's honest assessment | Phase 2.5: fuse with iteredge's 8-DOF optimizer to recover the projective terms |
| 35 | Single-ellipse approach abandoned mid-experiment | Agent B aborted | Re-attempt only if multiring+iteredge fusion fails; single-ellipse is theoretically weaker regardless |
| 36 | Ring-eccentricity after multiring warp can be 0.047 max (image 46) — not perfectly circular | Phase 2 multiring report | Iteredge refinement should drive this below 0.02 |
| 37 | Localization may pick up printed logos in non-train images (image 29 generalized) | Phase 1 disaster | Multiring's black-disc-contrast scoring is the fix; verify on held-out images 32-46 in Phase 2.5 |

## Open questions

1. **Should Phase 2.5's fused approach detect ALL 10 rings, or focus on the 3 the user specified (outer, black/white boundary, middle)?** The user's prescription mentions 3, but multiring detected 7-13. More rings = more constraints = better optimization, but also more failure modes. Default: detect as many as possible (≥3), use all in the optimization.
2. **Should the iterative refinement optimize the full 8-DOF homography, or a constrained rotation+tilt (5 DOF)?** Iteredge chose 8-DOF with regularization. The user's prescription doesn't specify. Default: 8-DOF with regularization (matches iteredge's reasoning: phone-camera focal length unknown).
3. **For image 21 specifically, what's the fallback when no magenta GT is available to size the frame?** In production, the user will UI-mark hole centers. In the spike, we have magenta GT. The pipeline should accept an optional `hole_centers_hint` parameter; when absent, fall back to a conservative `target_ring1_px = 420`.
4. **When should Phase 2.5 expand to all 10 train images?** Per user: after the 4-image test passes the criteria. Specifically: image 12 must keep 1-ring visible; image 21 must keep all 5 holes visible.
5. **Should Phase 3 (LangChain) wait for Phase 2.5 to fully converge, or run in parallel on Phase-2 outputs?** Per user's original plan: Phase 2.5 first, then Phase 3 in another LLM session. The LLM detector needs clean 1024×1024 inputs to do its best work.

## Related research

- [`research.md`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/context/changes/cv-service-boundary/research.md) — iterations 1-8 (classical). Best score-Jaccard 0.255.
- [`research-blob-detection.md`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/context/changes/cv-service-boundary/research-blob-detection.md) — iterations 9-10 (matched filter). Best F1 0.26.
- [`research-llm-pivot.md`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/context/changes/cv-service-boundary/research-llm-pivot.md) — LLM-pivot proposal + 10-question interview answers. This document is the implementation-detail companion to that proposal.
- [`frame.md`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/context/changes/cv-service-boundary/frame.md) — `/10x-frame` artifact that redirected classical detection from luminance to texture.
