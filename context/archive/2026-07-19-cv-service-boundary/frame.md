# Frame Brief: CV detection algorithm — feature extraction, not pipeline architecture

> Framing step before /10x-plan. This document captures what is *actually*
> at issue, separated from what was initially assumed.

## Reported Observation

The classical 5-stage CV pipeline (homography → ring geometry → morphological isolation → watershed → radial scoring) as implemented in [`cv/detect.py`](../../cv/detect.py) scores mean score-Jaccard **0.089 vs PRD ≥0.90**, with **0/46 images** meeting the bar and only **2/46** getting the right hole count. The user finds the prior research's conclusion ("fundamental blocker — holes in the black target are nearly invisible in luminance") unsatisfying and has asked for an alternative algorithm under a pure-grayscale constraint.

## Initial Framing (preserved)

- **User's stated cause or approach** (from prior `research.md`): Bullet holes that land inside the black portion of the target are nearly invisible in the luminance channel. 79% of shots land in the 8/9/10 rings (the black disc), and there is no consistent brightness signature separating "dark hole inside dark ink."
- **User's proposed direction** (from prior `research.md`): Pivot Stage 3 to edges (Canny + HoughCircles on the edge map), add template-matching for ring geometry, surface a capture-condition gate as a product requirement. **User rejected this direction** as not satisfying; instead requested: propose an alternative algorithm, grayscale-only, and "interview me relentlessly on the detection aspects if necessary."
- **Pre-dispatch narrowing** (Step 1.5 answers — what the user knows from manually counting every hole):
  - *Hole visibility*: "Subtle yet visible clearly. You can see a difference between the hole and the background. Effectively, you can recognize it by detecting edges/features."
  - *Strongest distinguishing feature*: **Texture / fibers** (torn paper fibers around the hole; printed ink is smooth).
  - *Lighting*: **Oblique overhead** — holes cast a shadow on one side.
  - *Why grayscale*: Color is noise — chroma carries no signal.

## Dimension Map

The observation (0.089 Jaccard) could originate at any of these dimensions:

1. **Luminance-blob extraction** (what the spike actually tried) — find dark blobs via Otsu/morphology/HoughCircles on raw luminance. Fails when hole interior matches ink luminance.
2. **Texture / local-variance extraction** — find high-local-variance regions where printed ink is smooth; torn paper fibers elevate the signal. **Not attempted by the spike.** ← user-identified
3. **Shadow-gradient / depth cue** — under oblique lighting, holes have asymmetric darkening (bright lip on lit side, dark shadow on far side). **Not attempted by the spike.** ← user-identified
4. **Edge-density** — hole boundary produces a ring of high gradient magnitude. Spike ran HoughCircles on blurred luminance, not on a Canny edge map.
5. **Spike under-tuning** — HoughCircles params, black-hat kernel sizes, lighting normalization never properly explored. Maybe 0.089 reflects effort, not approach.
6. **Stage-2 / scoring eval artifact** — bullseye was biased by hole clusters; even correctly-detected holes would be scored wrong. Some of the 0.089 may be eval error.
7. **Pipeline-architecture mismatch** — no per-stage heuristic works; need a model-based statistical fit.

User's answers in Step 1.5 rule IN dimensions 2 and 3, rule OUT dimension 1 as primary, leave 4–7 open.

## Hypothesis Investigation

| Hypothesis | Evidence | Verdict |
| --- | --- | --- |
| **D1: Luminance-blob (spike's actual approach)** — holes are undetectable in raw grayscale | Empirical probe: `luminance` patch SNR on #19 = 2.78× but population SNR = 1.00× (i.e. indistinguishable from ink in aggregate). Spike confirms: 0.089 Jaccard using this feature. | **STRONG** — confirms spike failed here, but this is a feature choice, not a dataset property |
| **D2: Texture / local variance (user-identified)** — torn paper fibers raise local std above smooth ink | Empirical probe: `local_std_k25` population SNR = **4.57× avg**, patch SNR = **24.84× on #19** (best of any method). `local_std_k15` patch SNR = 16.31× avg. Literature: "industrial defect inspection on dark surfaces uses local-entropy/Gabor as canonical answer" — independently corroborates. | **STRONG** — primary feature for the reframed algorithm |
| **D3: Shadow-gradient (user-identified)** — oblique lighting gives directional signal | Empirical probe: `shadow_grad` (horizontal Sobel) patch SNR = **13.43× on #19**; `sobel_mag` patch SNR = 14.34× on #19. Nearly as strong as full Sobel → most gradient signal IS directional (cast-shadow + fiber-tear asymmetry), not isotropic. | **STRONG** — independent secondary feature; complementary to texture |
| **D4: Edge-density / HoughCircles on edge map** — spike's research-rec, dismissed by user as bundled with capture-condition reframes | Empirical probe: `canny` and `canny_clahe` at standard thresholds (50/150) collapse to 0.00× SNR (median edge pixel = 0 in ink region) — broken thresholds. Literature: "CLAHE + Canny + HoughCircles is canonical for crater detection" — crater analogy fits bullet hole exactly. | **WEAK as standalone** (broken at default thresholds); **MEDIUM as post-stage** on a texture-map input |
| **D5: Spike under-tuning** — could the original 5-stage pipeline be saved with better params? | Empirical probe shows the spike's chosen feature (luminance blob) has a fundamental SNR ceiling (1.00× population on #19). No amount of Hough parameter tuning rescues a feature that doesn't carry the signal. | **NONE** — param tuning cannot fix wrong feature |
| **D6: Stage-2 / scoring eval artifact** — bullseye biased → wrong ring assignment → low Jaccard | Spike did detect *some* holes (mean n_pred ~6.7 vs mean n_true ~9.2); the count error alone explains most of the Jaccard deficit. Bullseye bias is a real second-order effect but not the dominant cause. | **WEAK** — secondary effect; addressing D2/D3 is necessary and sufficient to test |
| **D7: Pipeline-architecture mismatch** — need model-based fit | Empirical probe shows per-pixel feature extraction already isolates holes with 4–25× SNR. The pipeline architecture is sound; only Stage 3's feature choice was wrong. | **NONE** — no evidence; architecture vindicated |

## Narrowing Signals

Decisive observations that narrowed the hypothesis space:

- User's manual-count experience: holes are visible, the strongest cue is texture. (Rules out D1 as a dataset property; rules in D2.)
- User's report of oblique-overhead lighting. (Rules in D3.)
- Empirical probe measurement: `local_std_k25` = **24.84× patch SNR on image #19** (the spike's hardest failure case) — confirms the signal exists in pure grayscale when texture is extracted. (Rules out D5, D7.)
- Empirical probe measurement: `shadow_grad` = **13.43× patch SNR on #19** — independent confirmation via directional gradient, not just texture. (Rules in D3.)
- Literature agent (no knowledge of probe results) independently identified industrial defect inspection + crater detection as the right analogs, with local-entropy/Gabor as canonical features. (Independent cross-system confirmation.)

## Cross-System Convention

The bullet-hole-in-black-ink problem is **isomorphic to two well-studied CV problems**:

1. **Industrial defect inspection on dark homogeneous surfaces** (steel strip, paper, fabric) — pinhole/scratch detection. Canonical answer: texture-energy measures (local std, Gabor, GLCM, local entropy). The published bullet-hole-scoring literature under-explores this; the defect-inspection literature is the right source.
2. **Crater detection in planetary imagery** — circular depressed features with asymmetric lit/shadow rims under oblique solar illumination. Canonical answer: signed-gradient toward light direction + HoughCircles on the gradient edge map.

The spike's framing (luminance threshold + morphology) matches the *target-scoring* literature, which is the wrong literature. The reframed approach matches defect-inspection and crater-detection, which is the right literature.

## Reframed (or Confirmed) Problem Statement

> **The actual problem to plan around is**: Stage 3 of the pipeline must extract a **texture / local-variance / scale-band feature** instead of a luminance-blob feature. The rest of the pipeline (Stage 1 localization, Stage 2 ring geometry, Stage 4 watershed de-clustering, Stage 5 radial scoring with line-break rule) is structurally correct as specified and needs no architectural change.

The empirical probe shows that on image #19 — the densest bullseye-stacking case, the spike's hardest failure — `local_std_k25` produces **24.84× SNR** between hole and ink regions. `sobel_mag` and `shadow_grad` independently confirm via directional-gradient features (14× and 13× respectively). The spike never extracted any of these features; it ran HoughCircles on Gaussian-blurred luminance and black-hat morphology, both of which assume a luminance differential that doesn't exist on this dataset.

The original framing ("fundamental blocker") confused a feature-extraction failure for a dataset property. Addressing the actual problem (replace Stage 3's feature space) should let the rest of the pipeline produce dramatically better results without any architectural pivot. **The user's instinct that "a better algorithm exists" was correct; the better algorithm lives one stage deeper than the original pipeline architecture assumed.**

This is a reframe, not a confirmation: the prior research's recommended direction (pivot to edges + template-match + capture-condition gate as product requirement) is **superseded**. The user does not need to compromise on the ≥90% NFR or impose capture constraints; the algorithmic fix has not been exhausted.

## Confidence

**HIGH** — three independent lines of evidence converge:

1. User's domain knowledge (manual hole-counting) identified texture as the strongest feature and oblique lighting as a real cue.
2. Empirical measurement on representative images confirmed 4.4×–24.8× SNR for texture-based features on the spike's hardest case.
3. Literature review (run without knowledge of the empirical results) independently identified texture-energy / local-entropy / Gabor as the canonical approach via the industrial-defect-inspection analog.

## What Changes for /10x-plan

The plan should be about **replacing Stage 3's feature extraction**, not redesigning the pipeline architecture or amending the PRD. Concretely:

1. The `cv/detect.py:290-345` (`_stage3_morph`) implementation should be rewritten to compute one or more of: local standard deviation (`boxFilter` trick), local entropy (`skimage.filters.rank.entropy` or manual implementation), Gabor texture energy, or DoG at caliber scale — pure grayscale, no new principled dependencies.
2. The I/O contract `cv.detect.detect(image_path, caliber, target_type) -> dict` is unchanged; downstream stages (Stage 4 watershed, Stage 5 radial scoring) consume the same hole-mask output.
3. The eval harness `cv/eval.py` is the regression gate — re-run after the Stage 3 rewrite and compare aggregate metrics.
4. The four primary candidate features, ranked by evidence: (a) `local_std` with kernel ≈ 2× hole radius, (b) `sobel_mag` / directional Sobel for shadow asymmetry, (c) Gabor texture energy (rotation-invariant sum), (d) DoG at caliber scale. The plan should pick a primary and a fallback, not implement all four.
5. The PRD NFR (≥90% fidelity) **remains the target** — no capture-condition gate, no PRD amendment, no scope cut is needed unless the Stage 3 rewrite also fails to clear the bar.

The plan should NOT redesign Stage 1 (localization is robust at 43/46), Stage 2 (template-match could improve bullseye but is a refinement, not a blocker), Stage 4 (watershed works correctly given a clean mask), or Stage 5 (line-break rule implementation is correct). It should NOT propose edges/template-match/capture-conditions as primary directions — those were the rejected reframes.

## References

- **Source files**:
  - [`cv/detect.py:290-345`](https://github.com/krkruk/target-o-meter/blob/8d9d9c38538b76556ba883b0fee523f31218b18a/cv/detect.py#L290-L345) — `_stage3_morph`, the stage to rewrite.
  - [`cv/detect.py:51-170`](https://github.com/krkruk/target-o-meter/blob/8d9d9c38538b76556ba883b0fee523f31218b18a/cv/detect.py#L51-L170) — `detect()` contract, unchanged.
  - [`cv/eval.py`](https://github.com/krkruk/target-o-meter/blob/8d9d9c38538b76556ba883b0fee523f31218b18a/cv/eval.py) — regression gate.
- **Prior research**: [`context/changes/cv-service-boundary/research.md`](research.md) — original 5-stage spike + findings. The "fundamental blocker" conclusion in §Summary and §Top Blockers is **superseded** by this Frame Brief; the dataset characterization, eval harness, and per-stage code refs remain valid.
- **Empirical evidence**:
  - Probe script: [`cv/feature_probe.py`](../../cv/feature_probe.py) (added during this frame investigation).
  - 55 feature-map PNGs in `/tmp/feature_maps/` (e.g. `29_local_std_k25.png`, `19_local_std_k25.png`) — open to eyeball.
  - Zone overlays + raw measurements in `/tmp/feature_probe_cache/measurements.json`.
- **Literature**: independent literature review (returned by investigation sub-agent) covering 17 methods across texture-based, edge/gradient, morphological, and domain-analog categories. Top-3 candidates: local entropy, Gabor bank, DoG at caliber scale. Cross-domain analogs: industrial defect inspection (steel/paper/fabric) and crater detection (planetary imagery).
- **Investigation tasks**: two parallel sub-agents — empirical feature-signal probe + texture-detection literature review. Both completed, both blind to each other's results, both converged on texture/shadow as the answer.
