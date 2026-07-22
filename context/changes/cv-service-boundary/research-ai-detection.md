---
date: 2026-07-21T18:00:00Z
researcher: krzysztofkruk
git_commit: 76f6fc46d25d0fae8f94e0c28dea053dd26aaafa
branch: master
repository: target-o-meter
topic: "Phase 1 deterministic pipeline + Phase 2 3-agent experiment + Phase 2.5 fused differential refinement + Phase 3 Step 1 LLM spike — pre-processing/normalization/homography STABLE; LLM integration spike COMPLETE (Gemini 3.5 Flash Lite wins); Step 2 (full_pipeline integration) pending"
tags: [research, codebase, cv, llm, vlm, langchain, gemma, gemini, flash-lite, homography, multiring, iteredge, fused, differential-refinement, strategy-pattern, dependency-inversion, issf, paper-targets, structured-output, pydantic]
status: complete
last_updated: 2026-07-22
last_updated_by: krzysztofkruk
phase: 1 complete; 2 complete (multiring wins); 2.5 complete (fused pipeline stable on 10/10 images); 3 Step 1 complete (LLM spike — Gemini 3.5 Flash Lite selected, mean Jaccard 0.799 on 4-image set); 3 Step 2 pending (full_pipeline integration — see § Phase 3 Step 2 handoff at end of document)
last_updated_note: "Added Phase 3 Step 1 (LLM spike) findings: Gemma 4 31B-it vs Gemini 3.1/3.5 Flash Lite comparison, locked model, 7-layer prompt, per-hole caliber schema, misalignment analysis, full Step-2 restore spec"
---

# Research: AI detection — deterministic pipeline + 3-agent normalization experiment + fused differential refinement

**Date**: 2026-07-21
**Researcher**: krzysztofkruk
**Git Commit**: [76f6fc4](https://github.com/krkruk/target-o-meter/commit/76f6fc46d25d0fae8f94e0c28dea053dd26aaafa)
**Branch**: master
**Repository**: [krkruk/target-o-meter](https://github.com/krkruk/target-o-meter)

> **Status snapshot (the uniform view of where we are)**:
>
> | Phase | Status | Deliverable |
> |---|---|---|
> | 1 — deterministic pipeline + strategy seam | **COMPLETE** | `cv/{detector_base, normalize, mock_detector, pipeline, run_pipeline}.py` — 545 LOC, 10/10 train images, bullseye invert err 2.3e-13 px |
> | 2 — 3-agent normalization experiment | **COMPLETE** | `cv/approaches/{multiring, singleellipse, iteredge}/` — multiring wins detection; iteredge wins refinement |
> | 2.5 — fused multiring+iteredge differential refinement | **COMPLETE** | `cv/approaches/fused/` — 1544 LOC, 5 modules, 10/10 images clean, all holes visible, no elongation |
> | 3 — LangChain LLM integration + prompt tuning | **Step 1 COMPLETE; Step 2 PENDING** | **Step 1**: `cv/phase3_spike/` — 757 LOC, 8 modules, standalone harness on 4 images. **Model selected**: `gemini-3.5-flash-lite` (mean Jaccard 0.799, free-tier). **Step 2** (pending): copy `intermediate_fused` into `intermediate_full_pipeline`, wire detector behind `HoleDetector` seam, 3 deliverables/image — see § Phase 3 Step 2 handoff at end of document |
>
> **Phase 2.5 closers (the four user-verified success criteria)**:
> - Image 12: full 1-ring boundary inside frame ✓
> - Image 21: all 5 slug holes inside frame ✓
> - Image 29: localization finds actual target (logo rejected) ✓
> - Image 46: regression check on gold standard ✓
>
> **This document is the handoff artifact for Phase 3 (LangChain implementation).** It must be self-contained — another LLM session picking up Phase 3 should not need to scroll this conversation's history. **Phase 3 Step 1 (LLM spike) is COMPLETE** — its findings + the full Step-2 restore spec are in § Phase 3 Step 1 (COMPLETE) and § Phase 3 Step 2 handoff (PENDING) at the end of this document.

## Research Question

User direction (Phase 1): pivot the hole-detection stage from classical CV (which hit a hard wall at score-Jaccard ≈ 0.255 across 10 prior iterations — see `research.md` and `research-blob-detection.md`) to a **vision-language model**. Use LangChain as the orchestration framework with `gemma-4-31b-it` as the model. The classical pipeline keeps the stages it has already solved (EXIF-orient, target localization, two-anchor calibration, ISSF scoring) and produces a clean 1024×1024 fronto-parallel normalized image with the bullseye at a known location. The LLM receives that normalized image and returns structured XY-coordinates of detected bullet holes plus a per-hole score.

User direction (Phase 2): build the deterministic Phase-1 pipeline with a **strategy + dependency-inversion** architecture so the LLM detector can drop in later behind a stable interface. Then run a 3-agent parallel experiment to redesign the normalization (localization + warp + 1024 frame), because the first attempt produced elliptical rings instead of circles. Pick a winner, capture the discovery, and plan the next iteration.

User direction (Phase 2.5): *"further tune the multiring implementation with differential fitting to adjust the projection. Refine the deterministic approach keeping the AI integration for later."* Fuse multiring's detection with iteredge's refinement. Iteratively tune the parameters across multiple regression-fix rounds until pre-processing + normalization + homography are stable across all 10 train images.

## Summary

**Phase 1 — deterministic pipeline: COMPLETE.** Five new modules under `cv/` (detector_base, normalize, mock_detector, pipeline, run_pipeline) define the strategy seam and produce 8 files per image under `resources/train/intermediate_llm/`. Inversion math is provably correct (bullseye round-trip error ~2.3e-13 px on all 10 train images). User verified the magenta-dot visualization works.

**Phase 2 — 3-agent normalization experiment: COMPLETE.** Three parallel agents implemented independent approaches under `cv/approaches/{multiring, singleellipse, iteredge}/`:

| Agent | Approach | Status | User verdict |
|---|---|---|---|
| A — multiring | Multi-ring concentric ellipses → projective homography via circular-points method | COMPLETE | **WINNER** — best of the three |
| B — singleellipse | Single black-disc ellipse → focal-length decomposition | Aborted mid-implementation (incomplete outputs) | Not graded |
| C — iteredge | Iterative patch-against-edges via scipy.optimize.least_squares on 8-DOF homography | COMPLETE | Solid optimizer; weaker detector than multiring |

**Phase 2.5 — fused differential refinement: COMPLETE.** Five modules under `cv/approaches/fused/` fuse multiring's detection with iteredge's refinement, then add a 5-layer orthogonality defense + adaptive warp sizing. Final state on 10/10 train images:
- All holes visible (farthest hole lands at ≤ 465/512 px from bullseye in 1024 frame)
- All rings unskewed (M2 anisotropy ≤ 1.033, rotation 0°)
- All corner projections healthy (corner-radius ratio ≤ 1.42)
- Bullseye inversion self-test < 1e-12 px on every image

**Phase 3 — LangChain + prompt tuning: PENDING.** Pre-processing, normalization, and homography are **done**. Phase 3 is a single new module (`cv/langchain_detector/`) that plugs into the existing `HoleDetector` seam, plus prompt iteration against the F1 / score-Jaccard gates defined below.

## User's locked-in decisions (from interviews)

These answers must be respected by every subsequent iteration, including Phase 3.

### Phase-1 decisions (architecture)

| Q | Decision |
|---|---|
| Q1 — Architecture | **Strategy + dependency inversion.** Same output structure across detectors. Two LangChain strategies to wire in Phase 3: `LangChainAIStudioDetector` (primary, env-var token already present) and `LangChainOllamaDetector` (fallback, `ollama` server running locally, model `gemma4:latest`). |
| Q2 — Vision-token budget | **1120 (max detail)** for the spike; re-measure at 560 once F1 numbers exist. |
| Q3 — Image fidelity | **Plain grayscale-as-RGB** 1024×1024 of the warped crop. Test multi-channel texture-RGB only if zero-shot F1 is poor. |
| Q4 — Normalization layout | **Bullseye at (512, 512); 1-ring boundary at a tuned radius.** The Phase-2.5 work dropped the fixed `ring1_px = 500` convention — the warp's content extent now determines the scale (see § Phase 2.5 — Adaptive warp sizing). |
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

### Phase-2.5 decisions (5-question initial interview)

| Q | Decision | Rationale |
|---|---|---|
| Q1 — Optimization parameterization | **8-DOF homography** | Recovers true projective tilt; phone-camera focal length unknown. Default for the refinement kernel. |
| Q2 — Ring constraints | **All detected rings (7-13)** | Maximum constraints → strongest fit. soft_l1 + per-ring residuals handle outliers. |
| Q3 — Hole source for `_05_llm_predict.png` (final product) | **MockDetector 5-hole pattern** | Phase 2.5 focuses on geometry; real hole detection is Phase 3's job. |
| Q4 — Intermediate-image cadence | **Per coarse-to-fine stage** (4 PNGs + 1 horizontal strip per image) | Easy to spot overshoots without flooding disk. |
| Q5 — Frame sizing | **Adaptive, hole-extent-aware** | Initially `target_ring1_px`; later replaced entirely by warp-content-fit (see § Tuning round 2). |

### Phase-2.5 decisions (3-question tuning round 1 — image 46 frame sizing)

| Q | Decision |
|---|---|
| Q1 — Conflict policy when holes poke beyond ring 1 | **Use MAX warped radius (not RMS) for the outermost ring** — fixes elliptical-ring-far-side clipping. |
| Q2 — Margin between outermost element and frame edge | **50 px** (was 10). Comfortable visual margin. |
| Q3 — `target_ring1_px` floor | **Raised to 460** (was 350). Ring 1 always dominates the frame. *(Later obsoleted by tuning round 2 — see below.)* |

### Phase-2.5 decisions (5-question tuning round 2 — image 12 normalization regression)

User direction: *"Why don't you use the warp image instead [of cropping it]?"* — normalization was choosing `target_ring1_px` and cropping content from the warp to enforce it. Backwards.

| Q | Decision | What changed |
|---|---|---|
| Q1 — Normalization policy | **Fit the ENTIRE warp canvas into 1024 — `scale = 1024 / max(out_w, out_h)`** | Eliminates the regression root cause. No content ever cropped between warp and LLM input. |
| Q2 — Warp sizing | **Adaptive `margin_factor` based on GT hole extent** | Default 1.30; auto-enlarged when holes extend beyond ring 1; capped at 2.50. |

### Phase-2.5 decisions (5-question tuning round 3 — image 1 perspective overshoot)

User direction: *"picture 1 is still overshot … the fitting is overshot which causes warp/llm_input to be highly sheared and distorted."* Root cause: perspective bound `±1e-2` was sized for iteredge's near-identity affine init, not multiring's Q⁻¹ᐟ² which places the bullseye far from crop corners. Even tiny h31 values like 6.8e-4 caused the w-factor at crop corners to flip through zero (corner-radius ratio 36.6!).

| Q | Decision | Effect |
|---|---|---|
| Q1 — Perspective bound | **±1e-4 default, ±1e-5 for orthogonal** | corner-radius ratio: 36.6 → 2.14 on image 1 |
| Q2 — Orthogonality detection | **Multiring's mean ring eccentricity < 1.05** | Triggers tighter bounds (10× stricter) |
| Q3 — Regularization | **reg_perspective raised 10× globally** | Schedule: 1e6 / 1e5 / 1e4 / 2e3 (was 1e5 / 1e4 / 1e3 / 200) |
| Q4 — Edge filter | **Elliptical band-mask on Canny edges (`band_factor=0.3`)** | Rejects digit edges, hole edges, background clutter that drove overfitting |
| Q5 — Post-refinement safety | **Corner-radius ratio gate (threshold = `max(3.0, 2.0 × init_ratio)`)** | Catastrophic distortion revert |

### Phase-2.5 decisions (5-question tuning round 4 — image 1 affine drift)

User direction: *"picture 1 is still overshot. The paper target is … elongated really heavily … although the crop image is rather orthogonal."* Root cause: even with perspective locked, the AFFINE part M2 drifted to 1.39× anisotropy — the bounds (`±1.5 × |aff_init|`) and anchor regularization (200→20 across stages) weren't strong enough.

| Q | Decision | Effect |
|---|---|---|
| Q1 — Affine policy | **Lock affine entirely (refine only h31, h32) when `ecc < 1.10`** | M2 anisotropy: 1.389 → 1.007 on image 1 |
| Q2 — Direct anisotropy penalty | **SV-ratio penalty in residual (threshold 1.05, weight 1e3)** | Drives optimizer away from anisotropic M2 even when bounds allow movement |
| Q3 — Skip refinement entirely | **When `ecc < 1.02`, return multiring's H_init unchanged** | 8/10 train images hit this; multiring's analytical rectifier is provably optimal for orthogonal sources |
| Q4 — Eccentricity-aware bounds | **For tilted sources (ecc ≥ 1.10), bounds scale: `0.10 × max(1, (ecc-1)×10)`** | Allows genuine affine refinement for tilted sources, bounded proportionally |
| Q5 — M2-aniso post-refinement gate | **Revert if M2_aniso > 1.10** | Catches visible affine elongation directly |

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

## Phase 2 — 3-agent normalization experiment (COMPLETE)

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

## Phase 2.5 — Fused multiring+iteredge differential refinement (COMPLETE)

### Goal

Fuse multiring's logo-rejecting detection + circular-points initial H with iteredge's 8-DOF differential refinement. Iteratively tune until 10/10 train images are clean (all holes visible, no elongation, no shearing).

### Architecture: `cv/approaches/fused/`

```
cv/approaches/fused/                            1544 LOC total
├── __init__.py                  (  23)  Package docstring + 14-file output manifest
├── adaptive_frame.py            ( 125)  adaptive_margin_factor() — sizes warp canvas from GT hole extent
├── refine.py                    ( 616)  refine_homography() + inlined make_residual_fn + 5-layer defense
├── pipeline.py                  ( 655)  run_pipeline() orchestrator + _warped_ring_metrics + band-mask helper
└── run.py                       ( 125)  argparse CLI
```

**Stage sequence** (one image):

```
1. intake                cv.gt.load_bgr (EXIF-aware)
2. localize              multiring.localize.crop_to_target (logo-rejecting via black-disc contrast)
3. detect rings          multiring.detect_rings.detect_rings (bounded 4-param ellipse fit per ring)
4. initial H             multiring.homography.compute_rectifying_homography (circular-points, AFFINE only —
                         perspective left to refiner). projective_refine=False is mandatory.
5. warped-ring metrics   fused.pipeline._warped_ring_metrics — derives s_warped, r_bull_warped, r_ring1_warped
                         (MAX of outermost ring) from the actual detected rings under H_init. CRITICAL: cannot
                         copy from cal dict because multiring's Q^{-1/2} rescales rings significantly.
6. differential refine   fused.refine.refine_homography — 8-DOF, 4-stage coarse-to-fine, with 5-layer
                         orthogonality defense (see below). Per-stage PNG callback for overshoot tracking.
7. adaptive warp sizing  fused.adaptive_frame.adaptive_margin_factor — enlarge margin_factor when GT holes
                         extend beyond ring 1. iteredge.warp.compute_output_shape + apply_warp.
8. normalize to 1024     iteredge.normalize.normalize_to_1024 with scale = 1024 / max(out_w, out_h).
                         FITS THE ENTIRE WARP CANVAS — never crops content (tuning-round-2 fix).
9. detect (mock)         cv.mock_detector.MockDetector (Phase 3 swaps in LangChain detectors)
10. invert + viz         9 standard PNGs + 4 per-stage intermediates + 1 horizontal strip + result.json
```

### The 5-layer orthogonality defense (the final tuned form)

The 4 tuning rounds converged on a layered defense against overshoot. Layers are applied in order; each layer's threshold comes from the user's interview answers.

```
Layer 1 — SKIP REFINEMENT ENTIRELY
  Threshold: mean_ring_eccentricity < 1.02 (multiring's detected rings)
  Action: return multiring's H_init unchanged
  Rationale: for near-frontal sources, the analytical circular-points rectifier is provably optimal;
             the optimizer can only add noise.
  Hit by: 8/10 train images (1, 6, 10, 12, 19, 21, 29, all skip)

Layer 2 — LOCK AFFINE (refine only h31, h32)
  Threshold: 1.02 ≤ mean_ring_eccentricity < 1.10
  Action: lb[:6] = ub[:6] = aff_init[:6] (with 1e-9 epsilon for scipy's strict-inequality requirement).
          Only h31, h32 are free (2 DOF).
  Rationale: prevents the M2 anisotropy drift that causes visible elongation on orthogonal sources
             (image 1 root cause, tuning round 4).
  Hit by: 2/10 train images (4, 31, 46)

Layer 3 — SCALE BOUNDS BY ECCENTRICITY
  Threshold: mean_ring_eccentricity ≥ 1.10
  Action: bounds = 0.10 × max(1, (ecc - 1) × 10) × |aff_init|. For ecc=1.20 → ±0.20; ecc=1.50 → ±0.50.
  Rationale: allows genuine affine refinement for tilted sources, bounded proportionally to tilt.
  Hit by: 0/10 train images (none tilted enough; ready for future inputs)

Layer 4 — SV-RATIO PENALTY IN RESIDUAL
  Action: add residual term `max(0, SV_max/SV_min - 1.05) × sqrt(1e3)` to make_residual_fn.
  Always active (regardless of layer).
  Rationale: drives optimizer away from anisotropic M2 even when bounds allow some movement.

Layer 5 — POST-REFINEMENT GATES (revert to init if triggered)
  Gate A: corner-radius ratio > max(3.0, 2.0 × init_ratio)
          → catches catastrophic perspective distortion (image 1 round-3 root cause)
  Gate B: M2 anisotropy > 1.10
          → catches visible affine elongation (image 1 round-4 root cause)
  Gate C: data_score final > init (existing iteredge safety check, kept)
  Always active.
```

### Adaptive warp sizing (replaces `target_ring1_px` from earlier phases)

The Phase-1/Phase-2 design chose `target_ring1_px` (the radius where ring 1 lands in the 1024 frame) and let normalization crop whatever fell outside. **Tuning round 2 (user-directed) eliminated this** — the warp's content extent now determines the 1024 scale.

```
adaptive_margin_factor(bbox, H_opt, cx_crop, cy_crop, r_ring1_warped, gt_marked_path)
  ├── default_margin_factor = 1.30
  ├── if GT available:
  │     └── project GT holes through H_opt → max_hole_r_warped
  │     └── margin_factor = max(1.30, min(max_hole_r_warped / r_ring1_warped × 1.10, 2.50))
  └── else: margin_factor = 1.30

compute_output_shape(H_opt, ..., r_ring1_warped, margin_factor)
  └── out_w = out_h = 2 × ceil(margin_factor × r_ring1_warped)
  └── H_full = T @ H_opt  (translation T centers bullseye at (out_w/2, out_h/2))

normalize_to_1024(warped, ..., target_ring1_px = r_ring1_warped × 1024 / max(out_w, out_h))
  └── scale = 1024 / max(out_w, out_h)
  └── bullseye lands at (512, 512) by construction
  └── ring 1 lands at radius = 1024 / (2 × margin_factor)  (≈ 394 for default, ≈ 256 for max 2.0)
  └── GT holes land at ≤ 1024 / (2 × 1.10) ≈ 465  (always)
```

### Per-image output manifest (14 files + 1 JSON)

| File | Producer | Purpose |
|---|---|---|
| `<id>_01_intake.png` | `cv.gt.load_bgr` | EXIF-oriented source |
| `<id>_02_crop.png` | `multiring.localize.crop_to_target` | After localization (logo-rejecting) |
| `<id>_02b_detect.png` | multiring Canny + colored ellipses | KEY DIAGNOSTIC: detected rings on edges |
| `<id>_03_warp.png` | `iteredge.warp.apply_warp` + ring overlay | Warped crop with 10-ring overlay |
| `<id>_04_llm_input.png` | `normalize_to_1024` | 1024×1024 — **ACTUAL LLM INPUT** |
| `<id>_05_llm_predict.png` | `_draw_final_product` | **FINAL PRODUCT** — 1024 + canonical ring frame + magenta holes + scores |
| `<id>_06_crop_predict.png` | `_draw_stage_projection` | Crop + ring overlay under final H + inverted holes |
| `<id>_07_source_predict.png` | `_draw_magenta_on_bgr` | Source + fully-inverted magenta dots |
| `<id>_08_stage0.png` | stage_callback at init | Pre-optimization projection (initial state) |
| `<id>_08_stage{1..4}.png` | stage_callback per coarse-to-fine stage | Per-stage refinement projection (track overshoots) |
| `<id>_08_stages_strip.png` | `np.hstack(stage_images)` | All stages concatenated horizontally |
| `<id>_result.json` | — | Structured output (calibration, refinement, defense layer, all metrics) |

### Bugs found in iteredge source (left unmodified; worked around in fused)

Per the "open deps, create new files only" rule, these were copied/inlined into `fused/refine.py` rather than fixing iteredge directly.

1. **Residual-length off-by-one** in `iteredge.optimize.make_residual_fn`. The degenerate-det early return path uses `np.full(n_pts + 9, 1e6)`; the success path concatenates `persp[2] + anchor[6] + det_res[1] + sign_res[1] = 10` reg terms, returning `n_pts + 10`. scipy raises `ValueError: could not broadcast input array from shape (649,) into shape (650,)` when the optimizer wanders into the degenerate region mid-iteration (image 21 with multiring's near-full-image crop). Fixed in `fused/refine.py:make_residual_fn` (single `out_len` constant for all paths).

2. **Crop-frame vs warped-frame conflation** of `cal["s_px"]` and `cal["r_bull_px"]` in `iteredge.optimize.optimize_homography`. The same dict values are used by `enhance_ring_edges` (CROP frame, for blur/falloff) AND by `ring_points_warped` (WARPED frame, for ring generation). Harmless for iteredge (blob_detect's affine H is near-identity so crop ≈ warped), but breaks for multiring's Q⁻¹ᐟ² H_init which rescales rings ~3×. Fixed in `fused/refine.py:refine_homography` by separating `cal["s_px"]` (crop) from explicit `s_warped`, `r_bull_warped` parameters.

3. **Perspective bound `±1e-2`** in `iteredge.optimize.optimize_homography`. Sized for iteredge's near-identity affine init. When combined with multiring's H_init (which places the bullseye at crop origin, far from corners), even tiny `h31 ≈ 6.8e-4` values cause the w-factor at crop corners to flip through zero, producing 36.6× corner-radius asymmetry (image 1 root cause, tuning round 3). Fixed in `fused/refine.py` with `DEFAULT_PERSPECTIVE_BOUND = 1e-4` and orthogonal-tightening to `1e-5`.

### Final tuned parameters (the snapshot)

```python
# fused/refine.py module-level constants

DEFAULT_SCHEDULE = [
    # (sigma_factor, reg_perspective, reg_anchor, reg_det, max_iters, pot_kind, data_weight)
    (0.55,  1e6, 200.0, 1e3, 60, "mag", 0.5),    # broad basin, strong anchor
    (0.30,  1e5, 100.0, 200.0, 60, "dt",  1.0),  # exact placement
    (0.15,  1e4,  50.0,  50.0, 50, "dt",  1.5),
    (0.08,  2e3,  20.0,  20.0, 40, "dt",  2.0),
]
DEFAULT_PERSPECTIVE_BOUND = 1e-4         # was iteredge's 1e-2

SKIP_REFINE_ECC_THRESHOLD = 1.02         # Layer 1
AFFINE_LOCK_ECC_THRESHOLD = 1.10         # Layer 2
AFFINE_BOUND_BASE = 0.10                 # Layer 3 base (scaled by ecc)

SV_RATIO_THRESHOLD = 1.05                # Layer 4
SV_RATIO_WEIGHT = 1e3

CORNER_RATIO_ABS_THRESHOLD = 3.0         # Layer 5 Gate A
CORNER_RATIO_RELATIVE_FACTOR = 2.0
M2_ANISO_GATE_THRESHOLD = 1.10           # Layer 5 Gate B

# fused/adaptive_frame.py
DEFAULT_MARGIN_FACTOR = 1.30
HOLE_MARGIN_FACTOR = 1.10                # slack beyond outermost GT hole
MAX_MARGIN_FACTOR = 2.50

# fused/pipeline.py
ELLiptical_BAND_FACTOR = 0.3             # band-mask width around each detected ring (× gmean)
ORTHOGONAL_ECC_THRESHOLD = 1.05          # below → perspective_bound tightened 10× (1e-5)
```

### Final per-image results (10/10 train images)

| img | ecc | defense layer | M2 aniso | M2 rot | corner ratio | margin | ring1 @1024 | farthest hole @1024 | inv err px |
|-----|-----|---------------|----------|--------|--------------|--------|-------------|---------------------|------------|
| 1   | 1.019 | skip | 1.007 | 0° | — | 1.30 | 394 | 375 | 2.5e-13 |
| 4   | 1.024 | lock_affine | 1.032 | 0° | 1.20 | 1.30 | 394 | 435 | 0 |
| 6   | 1.014 | skip | 1.007 | 0° | — | 1.30 | 394 | 247 | 2.5e-13 |
| 10  | 1.018 | skip | 1.015 | 0° | — | 1.30 | 394 | 393 | 0 |
| 12  | 1.010 | skip | 1.006 | 0° | — | 1.54 | 333 | 465 | 1.1e-13 |
| 19  | 1.004 | skip | 1.002 | 0° | — | 1.30 | 393 | 125 | 2.3e-13 |
| 21  | 1.013 | skip | 1.027 | 0° | — | 1.38 | 371 | 465 | 2.5e-13 |
| 29  | 1.009 | skip | 1.012 | 0° | — | 1.30 | 394 | 34 | 4.7e-13 |
| 31  | 1.035 | lock_affine | 1.033 | 0° | 1.42 | 1.60 | 321 | 465 | 3.2e-13 |
| 46  | 1.047 | lock_affine | 1.016 | 0° | 1.20 | 1.30 | 394 | 435 | 2.5e-13 |

**User verdict (closing quote for Phase 2.5)**:
> *"Perfect! All llm_inputs are clear, no holes are cropped, even in the most extreme samples. Well done. … the current pre-processing, normalizing and applying homography steps is done."*

### CLI

```bash
uv run python -m cv.approaches.fused.run 12 46 29 21           # 4-image test set
uv run python -m cv.approaches.fused.run 1 4 6 10 12 19 21 29 31 46   # all 10
uv run python -m cv.approaches.fused.run 12 --out /tmp/fused_test    # custom output dir
uv run python -m cv.approaches.fused.run 12 --no-gt                  # disable adaptive margin_factor
```

## Code references

### Phase 1 files (deterministic pipeline — STABLE, do not modify)

- [`cv/detector_base.py`](https://github.com/krkruk/target-o-meter/blob/76f6fc46d25d0fae8f94e0c28dea053dd26aaafa/cv/detector_base.py) — strategy ABC + dataclasses. **The seam.**
- [`cv/normalize.py`](https://github.com/krkruk/target-o-meter/blob/76f6fc46d25d0fae8f94e0c28dea053dd26aaafa/cv/normalize.py) — `wrap_warp`, `to_llm_square`, `norm_to_crop`, `norm_to_source`, `self_test_inversion`, `TransformMeta` dataclass.
- [`cv/mock_detector.py`](https://github.com/krkruk/target-o-meter/blob/76f6fc46d25d0fae8f94e0c28dea053dd26aaafa/cv/mock_detector.py) — fixed 5-hole pattern for plumbing tests.
- [`cv/pipeline.py`](https://github.com/krkruk/target-o-meter/blob/76f6fc46d25d0fae8f94e0c28dea053dd26aaafa/cv/pipeline.py) — `run_pipeline` orchestration.
- [`cv/run_pipeline.py`](https://github.com/krkruk/target-o-meter/blob/76f6fc46d25d0fae8f94e0c28dea053dd26aaafa/cv/run_pipeline.py) — CLI.

### Phase 2 files (3-agent experiment — fused into Phase 2.5)

- [`cv/approaches/multiring/`](https://github.com/krkruk/target-o-meter/tree/76f6fc46d25d0fae8f94e0c28dea053dd26aaafa/cv/approaches/multiring) — **WINNER** detection + initial H. 8 files, 1955 LOC. Key files:
  - `localize.py` — black-disc-contrast logo rejection (image 29 fix).
  - `detect_rings.py` — bounded 4-parameter ellipse fit per ring (7-13 rings detected).
  - `homography.py` — circular-points method → initial affine H.
- [`cv/approaches/iteredge/`](https://github.com/krkruk/target-o-meter/tree/76f6fc46d25d0fae8f94e0c28dea053dd26aaafa/cv/approaches/iteredge) — **WINNER** refinement primitives. 10 files, 1596 LOC. Key files imported by fused:
  - `optimize.py` — energy function + `scipy.optimize.least_squares` with `trf` method.
  - `model.py` — 8-DOF homography parameterization + ring prediction.
  - `edges.py` — Canny + Sobel + ring-weight + distance transform.
  - `warp.py` — `compute_output_shape` + `apply_warp`.
  - `normalize.py` — `normalize_to_1024` + `IterEdgeTransformMeta` + inverse helpers.
- [`cv/approaches/singleellipse/`](https://github.com/krkruk/target-o-meter/tree/76f6fc46d25d0fae8f94e0c28dea053dd26aaafa/cv/approaches/singleellipse) — aborted; not used.

### Phase 2.5 files (fused pipeline — STABLE, this is the pre-LLM end-state)

- [`cv/approaches/fused/`](https://github.com/krkruk/target-o-meter/tree/76f6fc46d25d0fae8f94e0c28dea053dd26aaafa/cv/approaches/fused) — 5 files, 1544 LOC. Key files:
  - `refine.py` — `refine_homography` with 5-layer orthogonality defense, inlined `make_residual_fn` (with iteredge's off-by-one and crop/warped-frame bugs fixed), `_sv_ratio` + `_corner_radius_ratio` geometric gates, `DEFAULT_SCHEDULE` (reg_perspective 10× iteredge).
  - `pipeline.py` — `run_pipeline` orchestration + `_warped_ring_metrics` (computes warped-frame ring radii from multiring's detected rings under H_init) + `_elliptical_band_mask` (Canny-band filter for noise rejection) + `_mean_ring_eccentricity` (orthogonality detector).
  - `adaptive_frame.py` — `adaptive_margin_factor` (GT-aware warp sizing, replaces the old `target_ring1_px`).
  - `run.py` — argparse CLI.

### Existing files (do not modify; may import)

- [`cv/blob_detect.py`](https://github.com/krkruk/target-o-meter/blob/76f6fc46d25d0fae8f94e0c28dea053dd26aaafa/cv/blob_detect.py) — `to_gray`, `crop_to_target`, `calibrate`, `warp_fronto_parallel`, `score_holes`, `deliverable`. The "solved" classical stages.
- [`cv/gt.py`](https://github.com/krkruk/target-o-meter/blob/76f6fc46d25d0fae8f94e0c28dea053dd26aaafa/cv/gt.py) — `load_bgr` (EXIF-aware), `magenta_centers` (eval-only GT).
- [`cv/eval_blob.py`](https://github.com/krkruk/target-o-meter/blob/76f6fc46d25d0fae8f94e0c28dea053dd26aaafa/cv/eval_blob.py) — method-agnostic eval harness.

### Output directories

- `resources/train/intermediate_llm/` — Phase 1 baseline (10 images × 8 files each = 80 files).
- `resources/train/intermediate_multiring/` — Phase 2 multiring outputs (4 images × 9 files = 36 files).
- `resources/train/intermediate_iteredge/` — Phase 2 iteredge outputs (4 images × 9 files = 36 files).
- `resources/train/intermediate_fused/` — **Phase 2.5 fused outputs, 4-image test set** (4 images × 14 files + JSON).
- `resources/train/intermediate_fused_all10/` — **Phase 2.5 fused outputs, full 10-image set** (10 images × 14 files + JSON + `_summary.json`).

### Data assets

- [`resources/train/{1,4,6,10,12,19,21,29,31,46}.jpg`](https://github.com/krkruk/target-o-meter/tree/76f6fc46d25d0fae8f94e0c28dea053dd26aaafa/resources/train) — 10 base images (LLM input source).
- [`resources/train/*_marked.jpg`](https://github.com/krkruk/target-o-meter/tree/76f6fc46d25d0fae8f94e0c28dea053dd26aaafa/resources/train) — 10 magenta-marked GT (eval-only).
- `resources/train/intermediate_blob/gt/*_gt.npy` — cached GT centers, shape `(N, 2)` source-px.
- [`resources/paper_targets/metadata.yml`](https://github.com/krkruk/target-o-meter/blob/76f6fc46d25d0fae8f94e0c28dea053dd26aaafa/resources/paper_targets/metadata.yml) — score multisets for all 46 images.

## Phase 3 preview — LangChain implementation (STILL TO BE IMPLEMENTED)

> **Phase 3 is the sole remaining work.** Pre-processing (localize → detect rings → H_init), differential refinement (refine_homography), normalization (adaptive warp sizing + 1024 fit), and visualization are DONE and STABLE on 10/10 train images. Phase 3 swaps one function call (`MockDetector` → `LangChainAIStudioDetector` / `LangChainOllamaDetector`) and tunes the prompt against the F1 / score-Jaccard gates.

### What to build in Phase 3

```
cv/langchain_detector/__init__.py
cv/langchain_detector/aistudio.py        # LangChainAIStudioDetector
cv/langchain_detector/ollama.py          # LangChainOllamaDetector (fallback)
cv/langchain_detector/schema.py          # Pydantic models for with_structured_output
cv/langchain_detector/prompt.py          # Unified prompt builder (target_type placeholder)
cv/langchain_detector/fallback.py        # AIStudio -> Ollama retry wrapper
```

Update `cv/approaches/fused/run.py`'s CLI to register the new strategies (currently hard-coded to `MockDetector`).

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
- The geometric frame: **bullseye at (512, 512); ring 1 at radius ≈ 1024 / (2 × margin_factor) (varies per image — image 12 puts ring 1 at ~333, image 29 at ~394).** The prompt can either (a) read the actual `target_ring1_px` from the per-image metadata and inject it, or (b) describe the layout qualitatively ("ring 1 is the outermost printed ring, fully visible inside the frame"). Option (b) is more robust to the variable layout introduced in Phase 2.5.
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

### Open prompt-tuning questions for Phase 3

These are the questions a Phase 3 session should open with (analogous to the 5-question interviews that drove Phase 2.5's tuning rounds):

1. **Variable-layout handling**: should the prompt include the per-image `target_ring1_px` value numerically, or describe the frame qualitatively?
2. **Few-shot examples**: which 2-3 images from `{6, 12}` to use, and should the few-shot GT be the magenta-centroid JSON or a hand-curated "ideal response"?
3. **Confidence calibration**: how to map Gemma's `confidence` field to a meaningful probability (it's known to be miscalibrated on open VLMs).
4. **Caliber-hint format**: pixels in 1024 frame, millimeters, or relative-to-ring-step?
5. **Negative-guidance strength**: how many "do NOT report X" examples to include before the prompt becomes too long / confuses the model.

## Architecture decisions to preserve across all phases

1. **Strategy + dependency inversion.** `HoleDetector` is the only seam. Swapping detectors never touches geometry.
2. **Same `DetectionResult` schema across all detectors** (mock, LangChain-AIStudio, LangChain-Ollama, future DL).
3. **Magenta GT is eval-only.** NEVER pass `_marked.jpg` to the LLM (would teach magenta-finding). Few-shot examples use `(base_image, JSON_of_centers)` only. (Phase 2.5 uses magenta GT to SIZE THE FRAME — `<id>_marked.jpg` is read by `adaptive_frame.adaptive_margin_factor`, but only the hole-center coordinates are extracted; the magenta pixels are NEVER shown to the LLM.)
4. **Classical scoring always computed in parallel** as a diagnostic, never authoritative (Q5: LLM scores are authoritative).
5. **Open deps, create new files only.** `cv/blob_detect.py`, `cv/gt.py`, `cv/eval_blob.py`, `cv/approaches/multiring/*`, `cv/approaches/iteredge/*` are stable. Add deps via `uv add`.
6. **Inversion math must always round-trip.** Self-test `bullseye_invert_err_px < 0.01` is the regression gate. Phase 2.5 achieves <1e-12 px on every image.
7. **Per-image outputs preserve the 14-file convention** (Phase 2.5): 9 standard files + 4 per-stage intermediates + 1 horizontal strip + result.json.
8. **Warp content is sacrosanct.** Normalization MUST NOT crop content from the warp (Phase 2.5 tuning-round-2 rule). The 1024 scale is `1024 / max(out_w, out_h)` — derived from warp extent, not chosen.
9. **Layered orthogonality defense.** Any future change to `refine_homography` MUST preserve the 5-layer structure (skip / lock-affine / scale-bounds / SV-penalty / post-refinement gates). Tuning round 4 showed unconstrained affine drift causes visible elongation even when perspective is locked.

## Risk register

| # | Risk | Source | Mitigation | Status |
|---|---|---|---|---|
| 32 | Normalization crops out actual holes (image 21 root cause) | Phase 2 user feedback | Phase 2.5 tuning round 2: eliminated `target_ring1_px`; normalization fits entire warp canvas into 1024 | **CLOSED** |
| 33 | Normalization crops out the 1-ring boundary (image 12 root cause) | Phase 2 user feedback | Phase 2.5 tuning round 2: warp canvas sized via adaptive `margin_factor`; ring 1 always visible at radius ≈ 1024/(2×margin) | **CLOSED** |
| 34 | Multiring's circular-points H is mathematically affine; cannot recover true projective tilt | Multiring agent's honest assessment | Phase 2.5 fused with iteredge's 8-DOF optimizer; perspective bound tightened; layered defense prevents overshoot | **CLOSED** |
| 35 | Single-ellipse approach abandoned mid-experiment | Agent B aborted | Not re-attempted; multiring+iteredge fusion succeeds | **CLOSED** |
| 36 | Ring-eccentricity after multiring warp can be 0.047 max (image 46) | Phase 2 multiring report | Phase 2.5: M2 anisotropy ≤ 1.033 across all 10 train images (was 1.389 before tuning round 4) | **CLOSED** |
| 37 | Localization may pick up printed logos in non-train images (image 29 generalized) | Phase 1 disaster | Multiring's black-disc-contrast scoring is the fix; verified on all 10 train images including image 29 | **CLOSED** |
| 38 | iteredge's `make_residual_fn` has residual-length off-by-one on degenerate-det path | Phase 2.5 implementation | Inlined corrected copy in `fused/refine.py`; iteredge source left unmodified per "create new files only" rule | **DOCUMENTED** |
| 39 | iteredge's `cal["s_px"]` conflates crop-frame and warped-frame | Phase 2.5 implementation | `fused/refine_homography` accepts explicit `s_warped`, `r_bull_warped` parameters; cal dict stays crop-frame only | **DOCUMENTED** |
| 40 | Perspective bound `±1e-2` too loose for multiring's far-from-origin bullseye | Phase 2.5 image-1 regression (tuning round 3) | Tightened to `±1e-4` default, `±1e-5` for orthogonal images (ecc < 1.05) | **CLOSED** |
| 41 | Affine terms drift to high anisotropy under loose anchor regularization | Phase 2.5 image-1 regression (tuning round 4) | Layered defense: lock affine entirely when ecc < 1.10; SV-ratio penalty in residual; M2-aniso post-refinement gate at 1.10 | **CLOSED** |
| 42 | Phase 3 prompt may not handle variable ring-1 layout | Phase 2.5 dropped fixed `ring1_px = 500` | **RESOLVED by Phase 3 Step 1**: prompt injects per-image `ring_step_px` numerically + describes ring 1 qualitatively; works on 3 models across the 4-image set | **CLOSED** |
| 43 | Held-out images 32-46 may have eccentricity > 1.10 (triggering Layer 3 ecc-scaled bounds) | None of the 10 train images exercise Layer 3 | Verify when Phase 3 expands to held-out set; Layer 3 logic is in place but untested on real data | **OPEN** |

## Open questions

### Resolved by Phase 2.5

1. ~~Should Phase 2.5's fused approach detect ALL 10 rings, or focus on the 3 the user specified?~~ **Resolved**: detect as many as multiring finds (7-13); use all in the optimization (Q2 of initial interview).
2. ~~Should the iterative refinement optimize the full 8-DOF homography, or a constrained rotation+tilt (5 DOF)?~~ **Resolved**: 8-DOF (Q1 of initial interview); constrained via the 5-layer defense rather than reduced DOF.
3. ~~For image 21 specifically, what's the fallback when no magenta GT is available to size the frame?~~ **Resolved**: `adaptive_margin_factor` returns default 1.30 when GT unavailable; user will UI-mark hole centers in production (Phase 4+).
4. ~~When should Phase 2.5 expand to all 10 train images?~~ **Resolved**: complete — all 10 pass.
5. ~~Should Phase 3 (LangChain) wait for Phase 2.5 to fully converge, or run in parallel on Phase-2 outputs?~~ **Resolved**: Phase 2.5 first; Phase 3 starts now.

### Open for Phase 3

1. ~~**Variable-layout prompt handling** — embed per-image `target_ring1_px` numerically, or describe frame qualitatively? (Risk #42)~~ **Resolved by Phase 3 Step 1**: both — qualitative ring description + numeric `ring_step_px` injected per image. Risk #42 closed.
2. **Few-shot selection** — which 2-3 images, and which GT format? *(Still open — Step 1 was zero-shot per Q9; few-shot deferred. Step 1's mean Jaccard 0.799 may make few-shot unnecessary.)*
3. **Confidence calibration** — how to map the model's `confidence` to a meaningful probability? *(Still open — Step 1 collects `confidence` but does not yet calibrate it.)*
4. ~~**Caliber-hint format** — pixels, millimeters, or relative?~~ **Resolved by Phase 3 Step 1**: free-text `str` per hole, primary caliber injected as a hint, 6 canonical forms listed in prompt. Caliber used only for magenta-dot sizing (mm → px via per-image `px_per_mm`).
5. ~~**Negative-guidance strength** — how many "do NOT report X" examples before the prompt saturates?~~ **Resolved by Phase 3 Step 1**: 6 named negatives (pasties/stickers, ring strokes, digits, folds, shadows, black disc, smudges) — the pasties clause was the load-bearing addition (img 29 root cause). No saturation observed.
6. **Held-out validation** — when to expand from 10 train images to the 32-46 held-out range; expect Layer 3 (ecc-scaled bounds) to trigger on some (Risk #43). *(Still open — Step 1 tested only 4 images; Step 2 also targets the 4-image set per user direction.)*

## Related research

- [`research.md`](https://github.com/krkruk/target-o-meter/blob/76f6fc46d25d0fae8f94e0c28dea053dd26aaafa/context/changes/cv-service-boundary/research.md) — iterations 1-8 (classical). Best score-Jaccard 0.255.
- [`research-blob-detection.md`](https://github.com/krkruk/target-o-meter/blob/76f6fc46d25d0fae8f94e0c28dea053dd26aaafa/context/changes/cv-service-boundary/research-blob-detection.md) — iterations 9-10 (matched filter). Best F1 0.26.
- [`research-llm-pivot.md`](https://github.com/krkruk/target-o-meter/blob/76f6fc46d25d0fae8f94e0c28dea053dd26aaafa/context/changes/cv-service-boundary/research-llm-pivot.md) — LLM-pivot proposal + 10-question interview answers. This document is the implementation-detail companion to that proposal.
- [`frame.md`](https://github.com/krkruk/target-o-meter/blob/76f6fc46d25d0fae8f94e0c28dea053dd26aaafa/context/changes/cv-service-boundary/frame.md) — `/10x-frame` artifact that redirected classical detection from luminance to texture.

---

## Phase 3 Step 1 — LLM spike (COMPLETE)

> **This section is the self-contained record of the Phase 3 Step 1 LLM spike.** It captures the locked decisions, the model comparison, the prompt architecture, and the per-image results. The Step-2 handoff (next section) depends on everything here.

### Scope and decoupling

Per user direction (Step-1 interview Q1): **standalone harness, decoupled from the fused CV pipeline.** The spike reads the EXISTING normalized `*_04_llm_input.png` images produced by Phase 2.5 and feeds them to the LLM. No changes to `cv/approaches/fused/`, no `langchain_detector` package yet — that is Step 2's job. The rationale (user quote): *"I want to test the LLM integration independently, ignoring CV normalization for the step. Lesser scope means easier to debug."*

### New code: `cv/phase3_spike/` (757 LOC, 8 modules)

All new files — the "open deps, create new files only" rule is respected.

| File | LOC | Role |
|---|---|---|
| `__init__.py` | 12 | Package docstring + module manifest |
| `schema.py` | 79 | Pydantic `Hole` + `TargetAnalysis` for `with_structured_output` |
| `prompt.py` | 115 | 7-layer system-prompt builder + user-turn text |
| `client.py` | 93 | `VLMSpikeClient` — LangChain + Google AI Studio, model-agnostic |
| `metadata.py` | 95 | `metadata.yml` loader, caliber normalization, fused `result.json` ring-geometry reader |
| `compare.py` | 71 | Score-multiset Jaccard + per-score breakdown + misalignment flags |
| `viz.py` | 110 | Magenta-dot drawing (radius ∝ caliber, 70% of hole) + ISSF geometry |
| `run.py` | 182 | CLI: `uv run python -m cv.phase3_spike.run 12 46 29 21 --model <id> --out <dir>` |

**Dependencies added** via `uv add`: `langchain`, `langchain-google-genai==4.3.1`, `pydantic==2.13.4`. The `GOOGLE_API_KEY` env var is read at runtime (user exports it via `~/.bashrc`; no `.env` file needed).

### Step-1 interview decisions (10 questions, answered 2026-07-22)

| Q | Decision | Rationale |
|---|---|---|
| Q1 — Scope | **Standalone harness**, decoupled from fused pipeline | Easier to debug; ignore CV normalization for this step |
| Q2 — 4-image test set | `{12, 46, 29, 21}` (2 gold + 2 prior disasters) | All four `*_04_llm_input.png` exist in `intermediate_fused/` |
| Q3 — Caliber taxonomy | **Keep `9x19` (alias `9mm`), `slug` (alias `12-gauge`), `22lr`, `.223Rem`**; free-text `str` (not enum) so variants like `9x18 Makarov` admissible | User will define the specific caliber in the UI; `.22lr ≈ .223Rem`, LLM picks one. **Caliber is per-hole** (a target may carry mixed calibers, e.g. img 31). |
| Q4 — Comparison axis | **Score-multiset Jaccard** vs `metadata.yml`; positional F1 deferred | User reviews images vs GT manually afterward |
| Q5 — Geometry injection | **Defaults first** (qualitative ring description + numeric ring step), then tune if needed | "I don't think the LLM needs an explicit description of paper target. It just knows." |
| Q6 — Scoring authority | **LLM scores compared to `metadata.yml`**; surface misalignments so user can re-check metadata | "I could make a mistake in some counts, so you will notify me about a misalignment, so I can review the metadata.yml again" |
| Q7 — Fallback | **Skip fallback** (AI Studio only, no Ollama) | Simpler; avoids quantization concerns |
| Q8 — Schema mechanics | **Pydantic v2 `with_structured_output`** | — |
| Q9 — Reproducibility | **Single shot per image, temperature 1.0 (model default)** | Google AI Studio may throttle repeated calls; detailed system prompt compensates for stochasticity |
| Q10 — API key | **`GOOGLE_API_KEY` exported in shell via `~/.bashrc`** | `.env` not set, but env var is active |

### The 7-layer system prompt (the load-bearing artifact)

Built by `cv/phase3_spike/prompt.py::build_system_prompt()`. Runtime-injected variables: `target_type`, `target_ring1_px` (numeric, per-image from fused `result.json`), `ring_step_px` (numeric, = `target_ring1_px/9`), `primary_caliber` (str, simulates UI-collected user hint).

```
SystemMessage (stable across calls):
  # 0. Critical scanning discipline          ← added in tuning round 2
     - scan ENTIRE frame edge-to-edge (corners + outside-ring-1 too)
     - missed holes are worse than false positives
  # 1. Coordinate frame & geometry
     - 1024x1024 fronto-parallel; bullseye at (512, 512); 10 concentric rings
     - ring step ≈ {ring_step_px} px; ring 1 at ≈ {target_ring1_px} px from bullseye
  # 2. What counts as a bullet hole
     - roughly circular tear, ragged edges, faint halo; size scales with caliber
     - DOUBLE HITS / GRAZING HITS possible ← added in tuning round 2
       (larger/elongated/multi-lobed tear = 2+ overlapping hits, report each)
  # 3. What is NOT a bullet hole (negatives)
     - PASTIES / REPAIR STICKERS / PATCHES ← added in tuning round 2 (img 29 root cause)
       (rectangular/oval/circular; same color as covered area or white;
        SMOOTH edges; LARGER than a real hole; black patches on black disc common)
     - ring strokes, printed digits, folds/creases, shadows, the black aim disc, smudges
  # 4. ISSF scoring (line-break rule)
     - 0..10; touching higher ring line → higher value; X = 10; outside ring 1 = 0
  # 5. Caliber inference (per hole)
     - primary caliber hint: {primary_caliber}; prefer unless clearly different
     - canonical: 22lr, .223Rem, 9mm, .45ACP, 7.62x39, 12-gauge; variants admissible
     - 22lr ≈ .223Rem in diameter, pick one; caliber for marking only, never scoring
  # 6. Target type
  # 7. Output contract (JSON per schema; holes most certain first)

HumanMessage:
  - one-line instruction + the image (base64 data URL)
  - "Scan the ENTIRE frame edge-to-edge... Watch for pasties/stickers to ignore,
     and for double/grazing hits to split."
```

**The two prompt-tuning rounds (both user-driven):**

1. **Initial prompt** (7 layers without #0 and without the pasties/double-hit clauses): mean Jaccard 0.353 (Gemma). img 12 missed the score-0 corner hole; img 29 invented holes on black pasties.
2. **Tuning round 1** — user feedback after the first Gemma run:
   - *"21 looks good. off-by-one is completely acceptable."*
   - *"29 — black stickers confuse the LLM. Improve the prompt to ignore stickers (rectangular/oval/circular, same color or white, larger than holes)."*
   - *"Inform the LLM that double hits / grazing hits are possible."*
   - *"12 — LLM missed two 9-scores and the 0-score in the bottom-right corner. Improve the prompt so the LLM scans the entire image."*
   - Added Layer #0 (scanning discipline) + pasties block in Layer #3 + double-hits block in Layer #2.
   - Result on Gemma: mean 0.430. Scan fix worked (img 12: 11→13 found, corner hole recovered). Pasties fix partially worked (img 29: 0.09→0.33, found real tens but still over-reported).
3. **Model swap** — pasties rejection was the stubborn failure on Gemma; user authorized a model comparison. See next subsection.

### Model comparison — Gemma 4 31B-it vs Gemini 3.1 Flash Lite vs Gemini 3.5 Flash Lite

All three ran on the same 4-image set `{12, 46, 29, 21}`, single shot, with the tuned prompt above. Jaccard is score-multiset vs `metadata.yml`.

| img | GT n | Gemma 4 31B-it | Gemini 3.1 Flash Lite | **Gemini 3.5 Flash Lite (LOCKED)** |
|-----|------|----------------|----------------------|------------------------|
| 12  | 13   | 0.50 (n=13)    | 0.53 (n=13)          | **0.53** (n=13)        |
| 46  | 5    | 0.43 (n=5)     | 0.43 (n=5)           | **1.00** (n=5) ✅      |
| 29  | 5    | 0.33 (n=7)     | 1.00 (n=5) ✅        | **1.00** (n=5) ✅      |
| 21  | 5    | 0.43 (n=5)     | 0.43 (n=5)           | **0.67** (n=5)         |
| **mean** | — | **0.430** | **0.597** | **0.799** |

**Headline finding — the sticker case (img 29) was the model discriminator.** The prompt's negative-guidance on pasties only landed with Gemini. Gemma described the black pasties as holes despite explicit instruction; Gemini 3.1's own note: *"The target has several black adhesive patches covering previous shots; only the visible bullet holes in the center grouping were analyzed."* Stronger VLMs resolve the same-detection-different-recognition gap that stops open-weights models.

**Locked model: `gemini-3.5-flash-lite`** (free tier; $2.5/1M output tokens paid). Justification:
- Strictly better than 3.1 on this set (mean +0.20; two images improved, none regressed).
- img 46 went from acceptable-off-by-one to **perfect** (1.00) — a real fidelity gain, not noise.
- No detection misses, no hallucinations on the 4-image set; remaining errors are purely off-by-one on ring lines (user-classified acceptable).
- Negligible per-target paid cost (~50 output tokens/target; ~6,600 targets/day to spend $1).

**Caveat — `gemini-3.5-flash-lite` ignores the `temperature` parameter.** SDK warning: *"Model uses fixed sampling defaults; the sampling parameter(s) temperature will be ignored."* Production reproducibility must rely on structured-output + prompt, not temperature pinning. Acceptable for a scoring app (the structured output is what we trust); logged in the risk register (Risk #44).

### Per-image detailed results (Gemini 3.5 Flash Lite, locked model)

**img 12 (GT n=13, LLM n=13, Jaccard 0.53, count match Y):** GT `[0,1,2,5,6,8,8,9,9,9,10,10,10]`, LLM `[0,1,3,5,6,7,7,9,9,9,9,10,10]`. All 13 holes found including the score-0 corner hole `(857,778)` and the score-1 hole `(487,62)` — the scan-entire-image fix landed. Remaining gap is pure ring-placement drift in the center cluster (two 8s scored 7, one 9 scored 3). Off-by-one, acceptable.

**img 46 (GT n=5, LLM n=5, Jaccard 1.00, count match Y):** GT `[4,5,6,6,9]`, LLM `[4,5,6,6,9]`. Perfect score multiset. Every hole placed exactly. The score-0 false positive that 3.1 produced is gone.

**img 29 (GT n=5, LLM n=5, Jaccard 1.00, count match Y):** GT `[10,10,10,10,10]`, LLM `[10,10,10,10,10]`. All 5 real tens found, zero invented holes, pasties correctly ignored. The sticker failure mode is SOLVED.

**img 21 (GT n=5, LLM n=5, Jaccard 0.67, count match Y):** GT `[5,6,6,8,9]`, LLM `[5,6,7,8,9]`. All 5 slug holes found; one off-by-one (a 6 scored 7). Acceptable per user standard.

### Misalignment flags surfaced for metadata.yml review (per Q6)

Reported neutrally — could be LLM error or user mis-count in metadata.yml. The user has not yet reviewed these; they remain OPEN.

- **img 12**: LLM found all 13. Delta is in the 8↔9↔10 cluster (LLM `[7,7,9,9,9,9,10,10]` vs GT `[8,8,9,9,9,10,10,10]`). Likely LLM scoring drift on tight cluster, but worth re-checking GT for the three 10s.
- **img 29**: clean match, no flags.
- **img 46**: clean match, no flags.
- **img 21**: one off-by-one (LLM 7 vs GT 6 on one hole). Likely LLM error, not metadata.

### Caliber taxonomy mapping (Step 1)

The schema caliber field is **free-text `str`** (variants admissible). `cv/phase3_spike/metadata.py::normalize_caliber()` maps for GT comparison:

```python
METADATA_CALIBER_ALIASES = {
    "9x19": "9mm",
    "slug": "12-gauge",  # train images 10/21/22/23/36/37/38 use 'slug'
}
# .22lr, .223Rem, .45ACP, 7.62x39 pass through unchanged
```

`viz.py::_CALIBER_DIAMETER_MM` maps caliber → bullet diameter (mm) for magenta-dot sizing:

```python
_CALIBER_DIAMETER_MM = {
    "22lr": 5.7, ".223rem": 5.56, "9mm": 9.01, ".45acp": 11.5,
    "7.62x39": 7.9, "12-gauge": 18.0,  # slug
}
_DEFAULT_DIAMETER_MM = 9.0  # fallback for unrecognized caliber strings
_MARKER_DIAMETER_FRACTION = 0.70  # "70% of the hole" per the Step-2 spec
```

### Visual deliverables (Step 1)

Per image, the harness writes 2 PNGs + 1 JSON to the output dir:
- `<id>_llm_input.png` — the evaluated 1024×1024 normalized input (copied from `intermediate_fused`).
- `<id>_marked.png` — same image + faint canonical ring frame + magenta dots (radius ∝ caliber, 70% of hole diameter) + score labels. Magenta-pixel counts scale with caliber as designed (img 21 12-gauge → ~15k px; img 29 22lr → ~2k px).
- `<id>_llm_result.json` — full structured output + comparison (holes with x/y/score/confidence/caliber, score Jaccard, per-score breakdown, misalignment flags, call meta).
- `_summary.json` — all images aggregated.

**Output dirs:**
- `resources/train/intermediate_phase3_spike/` — Gemini 3.1 Flash Lite run (kept for comparison).
- `resources/train/intermediate_phase3_spike_35/` — Gemini 3.5 Flash Lite run (locked model).

---

## Phase 3 Step 2 handoff (PENDING — implementation in a separate session)

> **RESTORE SPEC.** This section is the complete, self-contained specification for Step 2. A fresh LLM session should be able to implement Step 2 from this section + the Step-1 code under `cv/phase3_spike/` + the fused pipeline under `cv/approaches/fused/` without needing this conversation's history. **Read this whole section before starting.**

### What Step 2 is

**Live pipeline integration.** Copy the `intermediate_fused` pipeline into `intermediate_full_pipeline` and swap the `MockDetector` for the locked LLM detector. The detector plugs in behind the existing `HoleDetector` strategy seam — geometry never changes.

User direction (verbatim): *"Integrate the code with the existing normalization pipeline. For best experience, copy `intermediate_fused` code into `intermediate_full_pipeline`."*

### The 3 deliverables per image (the user's exact spec)

> User quote: *"I need you to generate only 2 pictures and 1 json file: (a) normalized, orthogonal llm_input file, (b) a file with highlighted holes with magenta dots - proportional to the caliber, say 70% of the hole; (c) save down the structured output content, you may need to request the LLM to provide you X,Y, score values as well as possible caliber."*

So per image, Step 2 writes EXACTLY three files (NOT the 14-file Phase-2.5 manifest):

| File | Content | Source |
|---|---|---|
| `<id>_llm_input.png` | Normalized orthogonal 1024×1024 image (the LLM input) | fused pipeline's `normalize_to_1024` output (same as `*_04_llm_input.png`) |
| `<id>_marked.png` | The `llm_input` + magenta dots (radius ∝ caliber, 70% of hole) + canonical ring frame + score labels | reuse `cv/phase3_spike/viz.py::draw_magenta_holes()` |
| `<id>_result.json` | The LLM's structured output (holes with x, y, score, confidence, caliber) + target_type + notes | the LLM call |

### What to build (file plan)

```
cv/langchain_detector/                     NEW PACKAGE (Step 2)
├── __init__.py                            Package docstring
├── detector.py     LangChainDetector(HoleDetector)   ← THE new strategy implementation
├── schema.py       RE-EXPORT cv.phase3_spike.schema   (or copy; same Pydantic models)
├── prompt.py       RE-EXPORT cv.phase3_spike.prompt   (same 7-layer builder)
└── client.py       RE-EXPORT cv.phase3_spike.client.VLMSpikeClient
                                          (or inline; the client is model-agnostic)

cv/approaches/full_pipeline/              NEW PACKAGE (Step 2) — copy of fused/
├── __init__.py
├── pipeline.py     copy of fused/pipeline.py, with two changes (see below)
└── run.py          copy of fused/run.py, with detector wiring + output spec
```

**The two changes to the copied `pipeline.py`:**

1. **Replace the detector.** In `fused/pipeline.py:531` the call is:
   ```python
   result: DetectionResult = detector.detect(image_1024, ...)
   ```
   This stays — the detector is passed in, not hard-coded. The change is in `run.py`: construct `LangChainDetector(model="gemini-3.5-flash-lite")` instead of `MockDetector()`.

2. **Trim the output to 3 files.** The fused pipeline writes 14 files per image (9 standard + 4 stage intermediates + 1 strip + result.json). Step 2 writes ONLY the 3 files above. Concretely, in the copied `pipeline.py`:
   - Keep stages 1–8 (intake → localize → detect rings → H_init → refine → warp → normalize to 1024 → detect). These produce `image_1024` and the ring geometry.
   - **Delete** the per-stage PNG callbacks (`stage_callback`, `_08_stage*.png`, `_08_stages_strip.png`).
   - **Delete** `_01_intake.png`, `_02_crop.png`, `_02b_detect.png`, `_03_warp.png`, `_06_crop_predict.png`, `_07_source_predict.png`.
   - **Keep** `_04_llm_input.png` (renamed `_llm_input.png`) and the LLM structured-output JSON.
   - **Replace** `_05_llm_predict.png` with `_marked.png` via `draw_magenta_holes()` from `cv/phase3_spike/viz.py`.
   - The ring geometry (`target_ring1_px`) needed by both the prompt and `draw_magenta_holes()` is already computed at `fused/pipeline.py:513` — pass it through.

**The `run.py` change:** register the detector strategies.

```python
# cv/approaches/full_pipeline/run.py
from cv.langchain_detector.detector import LangChainDetector
from cv.mock_detector import MockDetector

parser.add_argument("--detector", default="langchain",
                    choices=["langchain", "mock"])
parser.add_argument("--model", default="gemini-3.5-flash-lite",
                    help="Google AI Studio model id (locked: gemini-3.5-flash-lite)")
# ...
if args.detector == "langchain":
    detector = LangChainDetector(model=args.model)
else:
    detector = MockDetector()
```

### The LangChainDetector strategy (the new HoleDetector implementation)

Must implement `cv/detector_base.py::HoleDetector.detect()`:

```python
# cv/langchain_detector/detector.py
class LangChainDetector(HoleDetector):
    def __init__(self, model: str = "gemini-3.5-flash-lite"):
        self._client = VLMSpikeClient(model=model)

    @property
    def name(self) -> str:
        return f"langchain-{self._client.model}"

    def detect(self, image_1024, target_type, caliber_hint=None) -> DetectionResult:
        # image_1024 is uint8 grayscale (1024,1024); the client expects a path.
        # Write to a temp file OR refactor VLMSpikeClient.analyze to accept an array.
        # (Refactoring is cleaner — add an analyze_array() method.)
        analysis, meta = self._client.analyze(
            image_array=image_1024,   # NEW: accept array, not path
            target_type=target_type,
            target_ring1_px=...,       # MUST be threaded through detect()
            ring_step_px=...,          # = target_ring1_px / 9
            primary_caliber=caliber_hint,
        )
        return DetectionResult(
            holes=[DetectedHole(x=h.x, y=h.y, score=h.score,
                                confidence=h.confidence) for h in analysis.holes],
            target_type=analysis.target_type,
            detector_name=self.name,
            notes=analysis.notes,
            raw={"model": self._client.model, "calibers": [h.caliber for h in analysis.holes],
                 **meta},
        )
```

**Three integration subtleties the Step-2 session MUST handle:**

1. **`target_ring1_px` must reach the detector.** The current `HoleDetector.detect(image_1024, target_type, caliber_hint)` signature does NOT carry ring geometry. Two options:
   - (a) Extend the signature: `detect(image_1024, target_type, caliber_hint, target_ring1_px)`. Breaks the mock + requires updating `detector_base.py`.
   - (b) Compute `target_ring1_px` inside the detector from the image. NOT possible — the detector only sees the normalized image.
   - **Recommended: option (a)** — extend the signature, update `MockDetector` to accept and ignore the new arg, update `detector_base.py` ABC. The fused pipeline already computes `target_ring1_px` at `fused/pipeline.py:513`; thread it into the `detector.detect(...)` call.

2. **`VLMSpikeClient.analyze` currently takes an image PATH.** The pipeline has the image as an in-memory array. Refactor to `analyze_array(image_1024_gray, ...)` that base64-encodes the array directly (no temp file). The Step-1 `analyze(path, ...)` can delegate to `analyze_array` by reading the file.

3. **`caliber_hint` plumbing.** The fused `run.py` already has `--caliber` and passes `caliber_hint` into `run_pipeline` → `detector.detect`. Step 2 should default `--caliber` from `metadata.yml` (simulating the UI) when not provided on the CLI, OR read it per-image from metadata at runtime. The Step-1 spike injects metadata's caliber as `primary_caliber`; Step 2 should do the same for consistency.

### Step-2 success criteria (from user feedback on Step 1)

The user has effectively re-defined the success bar via Step-1 feedback. For Step 2, the bar is:

- **Plumbing success (must pass):** end-to-end `full_pipeline` runs on the 4-image set `{12, 46, 29, 21}` without exceptions; produces exactly 3 files per image; structured-output JSON parses on 4/4.
- **Detection success (user's stated standard):** "off-by-one is completely acceptable." So: correct hole COUNT per image (all 4 must match GT count); off-by-one ring-line scoring is fine; NO hallucinated holes; NO missed holes (the scan-entire-image + pasties-aware prompt must hold up in the live pipeline).
- **Not required for Step 2:** clearing the PRD 0.90 Jaccard bar (the remaining errors are off-by-one, which the user accepts); expanding beyond the 4-image set (that is a later iteration).

**Verification command (Step 2):**
```bash
uv run python -m cv.approaches.full_pipeline.run 12 46 29 21 \
    --detector langchain --model gemini-3.5-flash-lite
# Expect: 3 files/image in resources/train/intermediate_full_pipeline/
#         + mean Jaccard ≈ 0.80 (matching the Step-1 spike)
```

### Step-2 risks (carried from Step 1 + new)

| # | Risk | Source | Mitigation | Status |
|---|---|---|---|---|
| 44 | `gemini-3.5-flash-lite` ignores `temperature`; cannot pin for reproducibility | SDK warning during Step-1 run | Rely on structured-output + prompt; report run-to-run variance when expanding to 10 images | **OPEN** |
| 45 | `HoleDetector.detect()` signature lacks ring geometry; detector cannot size magenta dots or build the prompt without `target_ring1_px` | Step-2 design (signature gap) | Extend signature with `target_ring1_px`; update `detector_base.py` + `MockDetector` | **OPEN** — Step 2 must resolve |
| 46 | 14-file Phase-2.5 manifest vs 3-file Step-2 spec mismatch | User's Step-2 instruction ("only 2 pictures and 1 json file") | Copy `fused/pipeline.py` and DELETE the intermediate-output blocks; keep only `_llm_input.png`, `_marked.png`, `_result.json` | **OPEN** — Step 2 must resolve |
| 47 | Paid-tier cost at production scale ($2.5/1M output tokens) | Model selection | ~50 output tokens/target → ~6,600 targets/day to spend $1 at paid rates; free tier covers spike + early production | **DOCUMENTED** |
| 48 | Mixed-caliber targets (img 31) untested in Step 1 — only single-caliber images in the 4-set | Step-1 scope | Step 2's per-hole caliber schema handles it structurally; validate when expanding to 10 images | **OPEN** |

### Open questions for the Step-2 session

1. **Signature extension approach (Risk #45)** — extend `HoleDetector.detect()` with `target_ring1_px`, or thread geometry another way? Recommended: extend + update mock + ABC.
2. **`caliber_hint` source** — CLI `--caliber` flag only, or auto-read from `metadata.yml` per-image (simulating the UI)? Step 1 used metadata; Step 2 should match.
3. **Module layout** — new `cv/langchain_detector/` package + new `cv/approaches/full_pipeline/` package, OR fold the detector into `full_pipeline`? Recommended: separate `langchain_detector` (reusable) + `full_pipeline` (the 3-file runner).
4. **Intermediate-file deletion** — hard-delete the blocks in the copied `pipeline.py`, or gate them behind `--debug` flag? Recommended: gate behind `--debug` (keeps the diagnostics available without polluting the default output).
