---
date: 2026-07-21T12:00:00Z
researcher: krzysztofkruk
git_commit: 899921e99532a95b1cebbe090bbfb09376b0f945
branch: master
repository: target-o-meter
topic: "LLM-pivot feasibility for ISSF hole detection — LangChain + Gemma 4 31B-it, classical pipeline keeps localization/calibration/scoring, LLM replaces only hole detection"
tags: [research, codebase, cv, llm, vlm, langchain, gemma, structured-output, issf, paper-targets, hole-detection, spike]
status: complete
last_updated: 2026-07-21
last_updated_by: krzysztofkruk
iteration: 11 (LLM pivot — proposed, not yet implemented)
prior_best: "iter 10 (matched filter) F1=0.26 / score-Jaccard=0.26 on train; iter 7/8 (DoG) Jaccard=0.255"
last_updated_note: "Phase 1 deterministic pipeline complete; 3-agent normalization experiment complete; multiring homography wins; discovery captured in research-ai-detection.md"
---

# Research: LLM-pivot feasibility for ISSF hole detection

**Date**: 2026-07-21T12:00:00Z
**Researcher**: krzysztofkruk
**Git Commit**: [899921e](https://github.com/krkruk/target-o-meter/commit/899921e99532a95b1cebbe090bbfb09376b0f945)
**Branch**: master
**Repository**: [krkruk/target-o-meter](https://github.com/krkruk/target-o-meter)

## Research Question

Per user direction: pivot the hole-detection stage from classical CV (which hit a hard wall at score-Jaccard ≈ 0.255 across 10 iterations — see `research.md` and `research-blob-detection.md`) to a **vision-language model**. Use **LangChain** as the orchestration framework with **`gemma-4-31b-it`** as the model. The classical pipeline keeps the stages it has already solved (EXIF-orient, target localization, two-anchor calibration, ISSF scoring) and produces a clean **1024×1024 fronto-parallel normalized image** with the bullseye at a known location. The LLM receives that normalized image and returns **structured XY-coordinates** of detected bullet holes. A fine-tuned prompt teaches the model the ISSF scoring rules. Deliverables: a feasibility verdict on whether this approach can clear the PRD's ≥0.90 fidelity bar where classical CV could not, plus 10 in-depth questions for the user to align on before planning.

**Scope (per user)**: this is research — a feasibility test. The overall solution architecture will be decided in `/10x-plan`. Playground is `./cv`, package manager is `uv`, scripts should be kept afterwards.

## Summary

**Three headline findings before the proposed flow:**

1. **`gemma-4-31b-it` is a real, current model.** Released April 2, 2026 (~3.5 months ago). Apache 2.0. Native multimodal (text + image, no audio on the 31B variant), 256K context, configurable per-image vision-token budget (70/140/280/560/1120). The model card explicitly lists **"pointing"** as a capability alongside OCR, object detection, document parsing. Available on Google AI Studio (free tier, API key), Ollama (`ollama pull gemma4`, needs ~16–32 GB VRAM at Q4), Vertex AI, HF Inference Providers, NVIDIA NIM. LangChain has first-class packages for all three primary paths (`langchain-google-genai`, `langchain-google-vertexai`, `langchain-ollama` — the last at v1.1.0 released Apr 7 2026).

2. **~80 % of the classical pipeline is reusable as-is.** EXIF-aware load (`cv/gt.py:19-24`), circularity-weighted target localization (`cv/blob_detect.py:45-82`), two-anchor radial calibration (`cv/blob_detect.py:253-294`), ISSF line-break scoring (`cv/blob_detect.py:604-614`), the magenta-GT parser (`cv/gt.py:81-109`), and the entire method-agnostic eval harness (`cv/eval_blob.py`) can all be lifted untouched. The only classical stages that need work are: (a) the fronto-parallel warp — defined at `cv/blob_detect.py:305-327` but currently **dead code** (grep confirms zero callers) and missing the translation vector `t` in its return, making inversion impossible without a one-line fix; and (b) the new resize-and-pad step to produce the 1024×1024 LLM input, which does not exist yet.

3. **The LLM replaces one function call.** In the current driver `cv/blob_detect.py:641-667` (`run_one`), the call `holes = detect_holes(crop, cal)` is the single seam. Everything before it is geometry; everything after it is scoring and visualization. Swapping `detect_holes` for an LLM call changes ~5 lines of orchestration and adds one new module (`cv/llm_detect.py`). This is exactly the kind of isolated seam the F-02 boundary was designed to enable ([`roadmap.md:75-87`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/context/foundation/roadmap.md#L75-L87)).

**The recommended serving path for the spike is `langchain-google-genai` against Google AI Studio** — free tier, immediate, no GPU required. Switch to `langchain-ollama` only if/when latency or cost at production volume justifies self-hosting.

## Critical correction: Gemma 4 is real (and current)

The agent's prior assumed Gemma stopped at Gemma 3 (Mar 2025). That assumption is **18 months out of date**. Verified facts:

| Property | Value | Source |
|---|---|---|
| Release date | **Apr 2, 2026** | [Launch blog](https://blog.google/innovation-and-ai/technology/developers-tools/gemma-4/) |
| Family | E2B / E4B / 12B Unified / 26B A4B MoE / **31B Dense** | HF model card |
| 31B params | 30.7B total, dense, 60 layers, sliding-window 1024 | HF model card |
| Modalities (31B) | **Text + Image** (no audio on 31B) | HF model card |
| Vision encoder | ~550M params, native (not SigLIP bolt-on) | HF model card |
| Context window | **256K tokens** | HF model card |
| Per-image vision-token budget | **70 / 140 / 280 / 560 / 1120** (configurable) | HF model card |
| Pointing capability | **Explicitly listed** alongside OCR, detection, parsing | HF model card "Image Understanding" |
| Structured output | **Native JSON + function calling + system instructions** | Launch blog |
| License | **Apache 2.0** (changed from custom Gemma license) | Launch blog + HF model card footer |
| Quantized variants | `unsloth/gemma-4-31B-it-GGUF`, `…-qat-GGUF`, `…-NVFP4`, `google/gemma-4-31B-it-qat-q4_0-gguf` | HF search |
| Hardware fit | Unquantized BF16 fits a single 80 GB H100; quantized fits consumer GPUs | Launch blog |

**Model ID casing**: user wrote `gemma-4-31b-it` (lowercase b). Hugging Face canonical is `google/gemma-4-31B-it` (capital B); Google AI Studio URL uses lowercase (`?model=gemma-4-31b-it`). Same model, case-insensitive resolution.

**Why this matters for the spike**: the originally-feared blocker (model doesn't exist, must fall back to Gemma 3 27B-it at 128K context with fixed 256-token vision budget) does not apply. Gemma 4 31B-it is a strictly more capable model with 2× the context and up to 4.4× the per-image vision budget of Gemma 3.

**PaliGemma2 is NOT a better fit.** It is fine-tuned on Gemma 2 (18 months older than Gemma 4) for referring-expression segmentation. Useful only after we have labeled data and want to fine-tune for sub-pixel precision. For a zero-/few-shot spike, Gemma 4 31B-it is strictly better.

## Reusable code audit

### Lift as-is (production-grade, no changes)

| Asset | Location | Role in the LLM pivot |
|---|---|---|
| `load_bgr` | [`cv/gt.py:19-24`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/cv/gt.py#L19-L24) | EXIF-aware BGR load — fixes the silent rotation on images 15/30/31 (EXIF `Orientation=6`). Sole entry point for any image. |
| `to_gray` | [`cv/blob_detect.py:41-42`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/cv/blob_detect.py#L41-L42) | One-line BGR→gray. |
| `crop_to_target` | [`cv/blob_detect.py:45-82`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/cv/blob_detect.py#L45-L82) | Circularity-weighted localization. Returns `(crop, bbox)` with `bbox = (x0, y0, w, h)` in source px — **the crop is full-resolution, so inversion is `+ (x0, y0)`** (already done at [`cv/run_eval.py:32`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/cv/run_eval.py#L32)). |
| `blackdisc_center` | [`cv/blob_detect.py:226-250`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/cv/blob_detect.py#L226-L250) | Bullseye estimate (closing-kernel-filled disc centroid + anisotropy ellipse). Internal to `calibrate`. |
| `calibrate` | [`cv/blob_detect.py:253-294`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/cv/blob_detect.py#L253-L294) | The two-anchor radial-profile calibration. Returns `{cx, cy, r_bw_px, r_bull_px, s_px, anisotropy, major_dir, semi_a, semi_b, ok}`. **This is the SOLVED geometry layer.** |
| `score_holes` | [`cv/blob_detect.py:604-614`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/cv/blob_detect.py#L604-L614) | ISSF line-break scoring `score = clamp(10 − ⌈(d − r − r_bull)/s⌉, 0, 10)`. Needs `(x, y, r)` per hole; we'll synthesize a fixed `r ≈ 0.15·s_px` from calibration when the LLM only returns `(x, y)`. |
| `deliverable` | [`cv/blob_detect.py:617-638`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/cv/blob_detect.py#L617-L638) | Magenta-overlay visualization. Reusable verbatim for side-by-side comparison plots — feed it the LLM's predictions (after coordinate inversion). |
| `magenta_centers` | [`cv/gt.py:81-109`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/cv/gt.py#L81-L109) | GT parser. Returns `list[(x, y)]` in source-image px. **Eval-only** — never feed `_marked.jpg` to the LLM (the model would learn to find magenta dots, not bullet holes). |
| `match_centers` / `evaluate_image` / `score_jaccard` / `tolerance_px` | [`cv/eval_blob.py:28-103`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/cv/eval_blob.py#L28-L103) | The entire spatial eval harness. Method-agnostic. Returns precision/recall/F1/count-err/mean-center-err-px/score-Jaccard. **Zero changes needed** — just feed it the LLM's inverted predictions. |

### Adapt (small, surgical changes)

| Asset | Location | What to change | Why |
|---|---|---|---|
| `warp_fronto_parallel` | [`cv/blob_detect.py:305-327`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/cv/blob_detect.py#L305-L327) | Return `(warped, {"M2": M2, "t": t, "out_center": ...})` instead of `(warped, M2, out_center)`. Translation `t` is already computed at line 322 — just include it in the return. | Currently **dead code** (grep confirms zero callers). The LLM pivot is its first consumer. Inverse needs `t`; matrix `M2` alone is insufficient. `M2` is positive-definite so inversion is `p_crop = M2⁻¹ · (p_warped − t)`. |
| `score_holes` | [`cv/blob_detect.py:604-614`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/cv/blob_detect.py#L604-L614) | Make the per-hole radius `r` optional (use `0.15·s_px` when missing). One-line change. | LLM returns only `(x, y)`, no radius. The line-break subtraction is small relative to `s_px`, so a fixed estimate is fine for scoring. |
| `run_one` | [`cv/blob_detect.py:641-667`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/cv/blob_detect.py#L641-L667) | Either fork as `run_one_llm` or add a `detector="llm"\|"classic"` switch. Steps 1–4 stay; step 5 (`detect_holes`) becomes the LLM call; step 6 onwards stays. | Single seam. |

### Build (new code)

| Asset | Purpose | Sketch |
|---|---|---|
| `to_llm_square(gray_warped, size=1024)` | Resize longest side to `size`, then symmetric `cv2.copyMakeBorder` to land at `size × size`. Returns `(image_1024, meta)` with `meta = {resize_scale, pad_top, pad_left, orig_h, orig_w}`. | ~20 LOC. |
| `normalized_to_source(xy_norm, crop_meta, warp_meta, llm_meta)` | Inverse chain: un-pad → un-resize → un-warp → un-crop. Order matters (last forward = first inverse). | ~10 LOC. |
| `cv/llm_detect.py` | LangChain orchestration. Constructs the message (image + prompt), invokes the model with `with_structured_output(TargetAnalysis)`, returns `list[(x, y)]` in 1024-space. | ~80–150 LOC depending on prompt length. |
| `cv/issf_geometry.py` (optional) | Promote the dormant `ISSF_RADII_MM` table (currently duplicated 7× across `cv/tmp/probe_ring_calibration_v*.py`) to one shared module. Useful for prompt construction. | ~15 LOC. |

### Discard (replaced by LLM)

- `detect_holes` (matched-filter, [`cv/blob_detect.py:477-601`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/cv/blob_detect.py#L477-L601)) — the whole point of the pivot.
- `cv/detect.py` (iter 1–8 OLD pipeline) — already superseded; only `TARGET_CARD_MM` constant worth extracting if needed.

### Dependencies to add to `pyproject.toml`

Current `pyproject.toml` has 7 deps (Django stack + numpy + opencv-headless + pillow + pyyaml). Add:

```toml
"langchain>=0.3",
"langchain-google-genai>=2.0",   # or langchain-ollama>=1.1 for local
"pydantic>=2.0",
```

`.env.example` already has `GOOGLE_API_KEY=...` placeholder — the spike's only secret.

## The proposed ideal pipeline (7 stages)

```
┌─ source JPEG (arbitrary resolution, possibly EXIF-rotated)
│
│  1. INTAKE & EXIF-ORIENT           load_bgr()                          [LIFT]
│     → upright BGR
│  2. LOCALIZE TARGET                crop_to_target(gray)                 [LIFT]
│     → crop + bbox (x0,y0,w,h) in source px
│  3. CALIBRATE                      calibrate(crop)                      [LIFT]
│     → {cx, cy, r_bw_px, r_bull_px, s_px, anisotropy, major_dir, ...}
│  4. ORTHOGONALIZE                  warp_fronto_parallel(crop, cal)      [ADAPT — return t]
│     → warped gray + {M2, t, out_center}   (affine fronto-parallel, no rotation)
│  5. NORMALIZE TO 1024×1024         to_llm_square(warped)                [BUILD]
│     → image_1024 + {resize_scale, pad_top, pad_left}
│
│  ─── LLM SEAM ───────────────────────────────────────────────────────
│
│  6. LLM HOLE DETECTION             llm_detect(image_1024, target_type, [BUILD]
│                                    caliber_hint=None, few_shot=...)
│     via LangChain + Gemma 4 31B-it + with_structured_output(TargetAnalysis)
│     → list[(x_norm, y_norm)] in 1024-space (+ optional confidence per hole)
│
│  ─── END SEAM ───────────────────────────────────────────────────────
│
│  7a. INVERSE COORDINATES           normalized_to_source(xy, ...)        [BUILD]
│      → list[(x_src, y_src)] in source-image px
│  7b. SCORE                         score_holes(holes_src, cal)          [LIFT/ADAPT]
│      → list[int] (0..10)
│  7c. EVALUATE (in spike)           eval_blob.evaluate_image(...)        [LIFT]
│      → {precision, recall, F1, score_jaccard, ...}
│  7d. VIZ (optional)                deliverable(crop, cal, holes, scores)[LIFT]
│      → side-by-side magenta-overlay PNG for human inspection
└─ artifacts: <id>_llm_input.png, <id>_llm_result.json, <id>_llm_deliverable.png
```

**Default choices baked into this proposal** (each is open to revision — see the 10 questions):

- **Serving path**: `langchain-google-genai` against Google AI Studio (free tier). Rationale: zero infra, instant on, sufficient for a 10-image feasibility spike.
- **Vision-token budget**: 1120 (max detail). Rationale: bullet holes are small low-contrast features — analogous to OCR which Google's docs route to the high end of the budget range.
- **Image fidelity to LLM**: plain RGB 1024×1024 of the warped crop, no overlay. Background outside the visible target area: neutral (245, 245, 245) — matches the existing `warpAffine` border fill.
- **Coordinate frame for LLM output**: raw pixel coordinates in 1024-space (i.e. `x ∈ [0, 1024], y ∈ [0, 1024]`). The Pydantic schema enforces `Field(..., ge=0, le=1024)`.
- **Normalization layout**: bullseye mapped to (512, 512); 1-ring boundary at radius 500 px (so ring 1 sits just inside the frame); the calibrated `s_px` after resize determines all inner ring radii. This means **the LLM sees a known, fixed geometric frame regardless of source-image resolution or target type**.
- **Scoring authority**: classical `score_holes` only. The LLM may *also* be asked to score (per its ISSF-rules prompt) but its scores are diagnostic, not authoritative.
- **Few-shot**: zero-shot first, on image 46 only. If zero-shot fails, add 2–3 few-shot examples built from `(base_image, magenta-derived JSON centers)` — never show `_marked.jpg` directly.
- **Output schema**:

  ```python
  class Hole(BaseModel):
      x: int = Field(..., ge=0, le=1024)
      y: int = Field(..., ge=0, le=1024)
      confidence: float = Field(..., ge=0.0, le=1.0)

  class TargetAnalysis(BaseModel):
      holes: list[Hole]
      target_type: Literal["air_pistol", "precision_pistol"]
      notes: str | None = None
  ```

- **Target type handling**: pass `target_type` as a prompt hint so the model knows ring-spacing context. The geometric normalization is identical for both types (1024×1024 frame, bullseye at center, 1-ring at radius 500).
- **Feasibility gate**: stage the spike. (1) Plumbing validation on image 46 zero-shot. (2) If plumbing works, scale to all 10 train images zero-shot. (3) Add few-shot. (4) Compare to classical baseline (score-Jaccard 0.255) and to PRD bar (0.90). Report which gate the LLM pivot passes.

## The 10 in-depth questions

Each question lists **why it matters**, **my default**, and **the alternatives**. Pick a default or override.

### Q1 — Serving path for the spike?

**Why**: determines infrastructure cost, latency, and which LangChain package gets imported.
**Default**: Google AI Studio via `langchain-google-genai` (free tier, API key from `.env`, immediate).
**Alternatives**:
- (b) `langchain-ollama` with `ollama pull gemma4` — local, free, full control; needs a GPU box with ≥16 GB VRAM at Q4 / ≥80 GB at BF16.
- (c) Vertex AI Model Garden via `langchain-google-vertexai` — pay-per-token, GCP auth, suitable for production scale-up.
- (d) Hugging Face Inference Providers (Novita, Featherless) — per-provider pricing, HF token.

### Q2 — Vision-token budget per image?

**Why**: 16× range (70 → 1120); cost and latency scale linearly. Bullet holes are small and low-contrast — analogous to OCR, which Google routes to the high end of the range. But higher budgets may not improve localization past a threshold, and at production scale the cost compounds across uploads.
**Default**: 1120 for the spike (max detail); re-measure at 560 once we have F1 numbers.
**Alternatives**: 560 (middle), 280 (low — likely too coarse for sub-ring localization).

### Q3 — Image fidelity fed to the LLM?

**Why**: the LLM can see the raw grayscale crop, a contrast-enhanced version, or a multi-channel stack (e.g. RGB where R = raw, G = local-std texture map, B = DoG at bullet scale). The prior research's SNR probe ([`research-blob-detection.md`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/context/changes/cv-service-boundary/research-blob-detection.md)) established local-std carries 2.3–3.7× SNR at holes. Pre-baking that signal into the image might help the LLM; or it might confuse a model trained on natural RGB.
**Default**: plain grayscale-as-RGB 1024×1024 of the warped crop. Test the multi-channel texture-RGB variant only if zero-shot F1 is poor.
**Alternatives**:
- (b) Grayscale + CLAHE contrast enhancement.
- (c) 3-channel stack: (raw, local-std normalized, DoG-at-bullet-scale).
- (d) Plain grayscale (1 channel) directly.

### Q4 — Normalization layout for the 1024×1024 frame?

**Why**: determines how the LLM interprets distances from the bullseye. Critical for both prompt design and post-hoc scoring.
**Default**: bullseye at (512, 512); **1-ring boundary at radius 500 px**; symmetric padding outside. This fixes the geometry: regardless of source resolution or target type, ring 1 is always at radius 500, and `s_px` after resize gives `500/9 ≈ 55.5 px` per ring step (10 rings).
**Alternatives**:
- (b) Bullseye at (512, 512); 1-ring at radius 450 (leaves a 50-px margin for paper-grain context).
- (c) Bullseye at (512, 512); 1-ring at radius 500 but preserve aspect ratio with letterbox padding (introduces black bars; tells the LLM nothing useful).
- (d) Don't normalize geometrically — just resize the crop to 1024×1024 ignoring bullseye position (forces the LLM to localize the bullseye itself; likely hurts).

### Q5 — Scoring authority: LLM, classical, or both?

**Why**: the user said "we'll need to fine tune the prompt to teach the LLM about the hit scoring according to ISSF rules." Two interpretations: (i) the LLM scores directly using the rules in the prompt; (ii) we score classically from LLM-provided (x, y) and the rules-in-prompt is just to help the model reason about what a "hit" is.
**Default**: classical scoring authoritative; LLM scores are diagnostic only. The classical ISSF line-break rule is exact arithmetic given correct (x, y) — no reason to delegate it to a model.
**Alternatives**:
- (b) LLM scores authoritatively; classical scores are a sanity-check.
- (c) Both authoritative; surface disagreements for human review (best fidelity but doubles complexity).
- (d) LLM provides only `(x, y)`, never asked to score.

### Q6 — Few-shot strategy?

**Why**: with 10 train images and a 256K context window, few-shot is essentially free. But each few-shot example uses ~1120 vision tokens + a JSON response — 3 examples ≈ 4500 extra tokens per call. Also: if we use train images as few-shot, we can't fairly eval on them (data leak). User said the markings + rough score estimates are available — strong signal few-shot is expected.
**Default**: zero-shot first (image 46 only) to validate plumbing. If F1 < 0.5 on image 46, add 2 few-shot examples (drawn from images {6, 12} — images 19/29/31 are edge cases not suitable for prototypical examples; image 46 itself is the eval target). Evaluate few-shot performance on the held-out images only.
**Alternatives**:
- (b) Few-shot from the start (3 examples: one 22lr, one 9x19, one slug).
- (c) One-shot only (single example).
- (d) Zero-shot throughout — cleanest measurement of model capability.

### Q7 — Output schema fields beyond `(x, y)`?

**Why**: more fields = more structured-output burden on the model. Fewer fields = less diagnostic data.
**Default**: `{x, y, confidence}` per hole + top-level `{target_type, notes}`. The `confidence` field lets us threshold / sort predictions in eval; `target_type` acts as a sanity-check that the model read the prompt; `notes` captures any qualitative observation.
**Alternatives**:
- (b) Minimal: `list[{"x": int, "y": int}]` only.
- (c) Rich: `{x, y, radius, confidence, ring_estimate, score_estimate}` — pushes more reasoning to the model.
- (d) Bare `list[list[int]]` (a list of `[x, y]` pairs).

### Q8 — Per-target-type prompt variants, or unified prompt?

**Why**: the two supported target types have very different ring scales (8 mm vs 25 mm steps) and different black-disc sizes (rings 7–10 vs rings 5–10). The PRD supports both. A unified prompt must explain both; per-type prompts can be tighter.
**Default**: unified prompt with a `{target_type}` placeholder and a conditional block ("You are scoring a 10 m Air Pistol target: ring step = 8 mm…"). The geometric normalization (Q4) is identical for both, so the LLM's job description doesn't change.
**Alternatives**:
- (b) Two prompts, dispatched by target_type.
- (c) Single prompt, target_type passed only as a JSON hint with no explanatory text.

### Q9 — Caliber hint to the LLM?

**Why**: bullet-hole radius varies 3× across calibers (22lr Ø 5.7 mm vs slug Ø ~18 mm). Telling the LLM the expected hole size may help it reject ring-line false positives (the classical pipeline's worst failure mode — see iter 9/10 register). But it may also over-constrain and cause the model to miss genuinely odd-shaped holes.
**Default**: pass `caliber_hint` as an optional string in the prompt when known; omit on mixed-caliber targets (#31). Phase 1 spike: include the hint on single-caliber train images.
**Alternatives**:
- (b) Always omit — let the model figure out hole scale from the image.
- (c) Always include, with a multi-select for mixed targets.
- (d) Include as a structured output field instead — ask the LLM to *infer* the caliber.

### Q10 — Feasibility success criterion?

**Why**: defines what "worth planning" means. The PRD bar is score-Jaccard ≥ 0.90 (currently 0.255 classical, 0.26 iter-10 best). A spike needs a gate.
**Default**: gate the spike on three thresholds on the 10-image train set:
  - **Plumbing success** (must pass): end-to-end pipeline runs without exceptions; structured-output parsing succeeds on ≥ 9/10 images; > 0 TP across the train set.
  - **Encouraging** (proceed to planning): mean F1 ≥ 0.50 **OR** score-Jaccard ≥ 0.50 on the train set (≥ 2× the classical baseline).
  - **Resounding success** (fast-track planning): mean F1 ≥ 0.75 **AND** score-Jaccard ≥ 0.75 on at least 7/10 images.
**Alternatives**:
- (b) Single bar: F1 ≥ 0.5 on image 46 only (the easiest case).
- (c) Match or beat classical baseline on every image.
- (d) Hit the PRD ≥ 0.90 bar (likely infeasible for a spike).

## Open questions beyond the 10

Surfaced during research; not in the user's 10-question budget but worth noting:

- **Mixed-caliber target #31**: 14 GT hits, two calibers (9×19 + 22lr). Single-caliber hint doesn't fit. Should this be in the eval set, or held out as a known-edge-case for later? Default: include in eval, report separately.
- **Slug targets (#10, #21)**: classical pipeline scored F1 = 0.00 (the 18 mm holes are off-scale for the matched filter). LLM may or may not do better. Default: include in eval; flag separately.
- **Image 19 (all 10s, only rings 8–10 visible)**: the canonical hard case for calibration (boundary at frame edge). The LLM doesn't depend on calibration directly — but scoring does. If calibration is wrong on #19, even correct hole positions score wrong. Default: include in eval; report scoring-vs-detection separately.
- **Token cost at production scale**: at the PRD's "max 3 concurrent uploads + queue" with vision-token budget 1120, cost is fine. At scale (1000+ targets/day), this needs re-measuring. Out of scope for the spike.
- **Streaming / partial-output**: LangChain's `with_structured_output` returns when the model completes. For long outputs (many holes), this may take 5–15 s. Not a blocker for the spike; relevant for UX later.
- **Reproducibility**: LLM outputs are stochastic. The spike should run each image 3× and report mean ± std on F1, not single-shot.

## Code references

### Existing — to lift or adapt
- [`cv/gt.py:19-24`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/cv/gt.py#L19-L24) — `load_bgr` (EXIF-aware).
- [`cv/gt.py:81-109`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/cv/gt.py#L81-L109) — `magenta_centers` (GT).
- [`cv/blob_detect.py:41-42`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/cv/blob_detect.py#L41-L42) — `to_gray`.
- [`cv/blob_detect.py:45-82`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/cv/blob_detect.py#L45-L82) — `crop_to_target`.
- [`cv/blob_detect.py:226-250`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/cv/blob_detect.py#L226-L250) — `blackdisc_center`.
- [`cv/blob_detect.py:253-294`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/cv/blob_detect.py#L253-L294) — `calibrate`.
- [`cv/blob_detect.py:305-327`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/cv/blob_detect.py#L305-L327) — `warp_fronto_parallel` (dead code — fix return signature).
- [`cv/blob_detect.py:604-614`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/cv/blob_detect.py#L604-L614) — `score_holes`.
- [`cv/blob_detect.py:617-667`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/cv/blob_detect.py#L617-L667) — `deliverable`, `run_one`.
- [`cv/eval_blob.py:28-103`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/cv/eval_blob.py#L28-L103) — full eval harness.
- [`cv/run_eval.py:20-41`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/cv/run_eval.py#L20-L41) — driver; replace `detect_holes` call with LLM call.
- [`cv/tmp/probe_ring_calibration_v6.py:54-57`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/cv/tmp/probe_ring_calibration_v6.py#L54-L57) — dormant `ISSF_RADII_MM` table (duplicated 7× across probes).

### To build
- `cv/llm_detect.py` — LangChain orchestration + Pydantic schema.
- `cv/normalize.py` (or extend `blob_detect.py`) — `to_llm_square`, `normalized_to_source`.
- `cv/issf_geometry.py` (optional) — promoted ring constants.

### Data assets
- [`resources/train/{1,4,6,10,12,19,21,29,31,46}.jpg`](https://github.com/krkruk/target-o-meter/tree/899921e99532a95b1cebbe090bbfb09376b0f945/resources/train) — 10 base images (LLM input source).
- [`resources/train/*_marked.jpg`](https://github.com/krkruk/target-o-meter/tree/899921e99532a95b1cebbe090bbfb09376b0f945/resources/train) — 10 magenta-marked GT (eval-only).
- `resources/train/intermediate_blob/gt/*_gt.npy` — cached GT centers, shape `(N, 2)` source-px.
- [`resources/paper_targets/metadata.yml`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/resources/paper_targets/metadata.yml) — score multisets for all 46 images.
- [`resources/train/intermediate_v9/46_crop.png`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/resources/train/intermediate_v9) — pre-localized crop (reference for what the LLM should see).
- [`resources/train/intermediate_v9/46_result.json`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/resources/train/intermediate_v9/46_result.json) — output schema to mirror.

## Architecture insights

### The seam was designed for exactly this
[`context/foundation/roadmap.md:75-87`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/context/foundation/roadmap.md#L75-L87) — F-02 (cv-service-boundary) was deliberately scoped as the **boundary**, not the algorithm. The roadmap note: *"the underlying detection fidelity is downstream (this foundation establishes the seam, not the accuracy)."* The "Unknowns" line at [`roadmap.md:84`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/context/foundation/roadmap.md#L84) explicitly lists "classical OpenCV pipeline vs. pretrained DL model vs. hybrid" as the open approach question. **An LLM pivot is exactly the kind of swap the seam was built to enable — no contract change required, no Django-coupling added.**

### Why the LLM pivot is plausible where classical failed
The classical wall (~0.255 score-Jaccard across 10 iterations) is **not** a tuning problem — it's a *signal* problem. The prior research established:
- **Luminance-based detection fails on the black disc** (the rings are dark ink, the holes inside the 8/9/10 rings are dark, no consistent brightness signature — see `research.md` Stage 3 analysis).
- **Texture-based detection (local-std) carries 2.3–3.7× SNR at holes** but classical verifiers (matched filter + Hessian blobness) cannot reliably separate holes from ring lines and printed digits at the same scale (see iter 9/10 in `research-blob-detection.md`).

An LLM with native image understanding and a high vision-token budget is not constrained to a single feature channel. It can reason about *context*: a dark circular feature inside ring 9 with ragged edges and a slight tonal dip is a bullet hole; a dark stroke forming part of the digit "9" is not. This is precisely the discrimination task at which VLMs excel and classical feature pipelines fail.

### Why the LLM pivot is not guaranteed to work
- **VLM sub-pixel precision is unverified for this task.** The model card lists "pointing" as a capability but does not specify accuracy. No public benchmark exists for ISSF target hole localization.
- **Variable-length list output** on open-weights models is occasionally malformed. Pydantic parsing + retries mitigate but don't eliminate.
- **Quantization degrades structured output.** If we eventually serve via Ollama at Q4, fidelity drops. BF16 or Q8 is safer for the spike.
- **The black-disc contrast problem doesn't disappear.** If the model can't visually distinguish a hole inside ring 10 from the surrounding black ink, no amount of prompting recovers it. The fronto-parallel warp helps (orthogonal view makes hole edges sharper); CLAHE pre-processing may help; but the fundamental signal limit could persist.

### Why scoring stays classical
The ISSF line-break rule is exact arithmetic: `score = clamp(10 − ⌈(d − r − r_bull)/s⌉, 0, 10)`. Given correct `(x, y)` and calibrated `s`, `r_bull`, `cx`, `cy`, the score is determined. Delegating arithmetic to an LLM adds noise without upside. The prompt-included ISSF rules are *context for hole identification* ("a hit touching the higher-value ring line is awarded the higher value"), not a request to compute the score.

### Magenta GT is sacred for eval
The `_marked.jpg` images must NEVER be passed to the LLM, even as few-shot. If the model sees magenta dots in input, it learns to detect magenta dots — which trivially generalizes to "look for magenta" rather than the actual task ("look for bullet holes"). Few-shot examples must be `(base_image, JSON_centers_extracted_from_marked)` — the LLM sees a clean target, the magenta-derived centers appear only in the assistant's response as the "right answer".

## Historical context (from prior changes)

- [`context/changes/cv-service-boundary/research.md`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/context/changes/cv-service-boundary/research.md) — iterations 1–8 (5-stage classical pipeline, then pivot to texture-based Stage 3). Best result: DoG at bullet scale, mean Jaccard 0.255 on the 10-image train set. **Established the luminance invisibility blocker on the black disc.**
- [`context/changes/cv-service-boundary/research-blob-detection.md`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/context/changes/cv-service-boundary/research-blob-detection.md) — iterations 9 (SimpleBlobDetector) and 10 (matched-filter + Hessian-blobness verifier). Best result: F1=0.26 / score-Jaccard=0.26 on train; image 46 perfect (F1=1.00). **Established the classical ceiling and validated the texture-signal reframe.**
- [`context/changes/cv-service-boundary/frame.md`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/context/changes/cv-service-boundary/frame.md) — the `/10x-frame` artifact that redirected from luminance to texture. The LLM pivot inherits its conclusion (the texture signal IS there) and asks: *can an LLM do natively what no classical feature pipeline could do on top of texture?*
- [`context/foundation/roadmap.md:118-129`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/context/foundation/roadmap.md#L118-L129) — S-02 is "the wedge slice": *"if the ≥90% bar cannot be met, the roadmap must be resequenced (manual-scoring fallback, scope cut, or product rethink)."* This LLM pivot is the most promising attempt yet to clear the bar before that resequencing becomes necessary.
- [`context/foundation/prd.md:111`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/context/foundation/prd.md#L111) — the binding NFR: *"The application detects bullet holes with ≥90% fidelity compared to manual scoring."* **The PRD is approach-neutral** — no language constrains the detector to classical CV or forbids an LLM/VLM approach. The LLM pivot satisfies the same I/O contract.
- [`context/foundation/lessons.md`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/context/foundation/lessons.md) — single Railpack/Django entry; nothing CV- or LLM-relevant. **If the LLM pivot succeeds, the structured-output-on-open-VLMs pattern is a strong candidate for the first CV lesson entry.**

## Risk register (additions)

| # | Risk | Source | Mitigation |
|---|---|---|---|
| 23 | VLM sub-pixel precision insufficient for ring-boundary scoring | Inference from model card (pointing capability listed but no accuracy claim) | Use 1120 token budget; validate F1 + mean-center-err-px on the spike; if center-err > 0.3·s_px, scoring-by-radius becomes unreliable |
| 24 | Variable-length list output malformed on open-weights model | LangChain structured-output general knowledge | Pydantic v2 schema + retry-on-parse-failure; constraint `len(holes) ≤ 20` (max shots in dataset is 14) |
| 25 | Quantization degrades structured output if served via Ollama at Q4 | LangChain + VLM literature | Spike via AI Studio (full precision); re-measure if/when moving to local Ollama |
| 26 | LLM hallucinates holes on ring lines / digits (same failure mode as classical matched-filter) | Symmetry with iter 9/10 failure mode | Few-shot examples that explicitly contrast holes with ring noise; post-filter predicted centers by calibrate-derived ring geometry (a "hole" predicted at radius `r_bull + k·s ± ε` exactly on a ring stroke is suspicious) |
| 27 | Magenta GT leak into few-shot examples | Architectural insight above | Strict policy: few-shot = (base_image, JSON); never `_marked.jpg` |
| 28 | Coordinate inversion bug (pad → resize → warp → crop ordering) | Inference from §7 audit | Property-based test: forward then inverse should be identity to within `sqrt(2)` px; verify on all 10 train images before evaluating LLM output |
| 29 | `warp_fronto_parallel` affine insufficient for high-tilt images (anisotropy > 1.3) | Prior research (image 19 anisotropy = 1.62) | Accept for spike; if F1 on image 19 is anomalously low, consider full perspective homography from concentric circles |
| 30 | Per-image LLM cost compounds at scale (1120 vision tokens × every upload × concurrent users) | Inference from token-budget docs | Out of scope for spike; revisit at S-02 planning |
| 31 | LLM stochasticity makes eval non-reproducible | LangChain defaults (temperature=1.0, top_p=0.95) | Run each train image 3× in the spike; report mean ± std; pin temperature (e.g. 0.2) for production |

## Related research

- [`context/changes/cv-service-boundary/research.md`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/context/changes/cv-service-boundary/research.md) — iterations 1–8 (classical). Best Jaccard 0.255.
- [`context/changes/cv-service-boundary/research-blob-detection.md`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/context/changes/cv-service-boundary/research-blob-detection.md) — iterations 9–10 (matched filter). Best F1 0.26 / Jaccard 0.26.
- [`context/changes/cv-service-boundary/frame.md`](https://github.com/krkruk/target-o-meter/blob/899921e99532a95b1cebbe090bbfb09376b0f945/context/changes/cv-service-boundary/frame.md) — `/10x-frame` artifact; texture reframe that the LLM pivot inherits.

## External sources

- [Gemma 4 launch blog](https://blog.google/innovation-and-ai/technology/developers-tools/gemma-4/) — Apr 2 2026; confirms model family, modalities, Apache 2.0 license.
- [Gemma 4 31B-it HF model card](https://huggingface.co/google/gemma-4-31B-it) — capabilities table, vision-token budgets (70/140/280/560/1120), pointing capability listed.
- [Gemma 4 Technical Report](https://arxiv.org/abs/2607.02770) — arXiv 2607.02770, Gemma Team, 2026.
- [Ollama README](https://github.com/ollama/ollama) — confirms `ollama pull gemma4` / `ollama run gemma4`.
- [`langchain-ollama` on PyPI](https://pypi.org/project/langchain-ollama/) — v1.1.0 released Apr 7 2026 (postdates Gemma 4 launch).
- [PaliGemma 2 announcement](https://huggingface.co/blog/paligemma2) — Dec 5 2024; built on Gemma 2, intended for fine-tuning, not a better zero-shot fit than Gemma 4.
