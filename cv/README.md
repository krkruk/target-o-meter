# cv/ — ISSF target bullet-hole detection spike

This is an **exploratory spike** for the computer-vision module described in
the PRD (§ Computer vision module for ISSF target hole detection). It is
intentionally:

- **Django-independent** — no imports from `target_o_meter.settings` or any
  Django model. The module is structured to be extractable as a standalone
  package in a future iteration.
- **Kept separate from the Django app** under `cv/` (not inside any Django
  app directory) so the dependency graph stays one-way (Django may import
  `cv`; `cv` never imports Django).

## Layout

- `cv/__init__.py` — empty package marker.
- `cv/detect.py` — the 5-stage pipeline:
  1. Perspective normalization / target localization (bbox crop; no full
     homography — see findings).
  2. Ring geometry extraction (largest dark circular blob → bullseye).
  3. Morphological isolation of bullet holes (HoughCircles + black-hat fallback).
  4. Watershed de-clustering of overlapping holes.
  5. Radial scoring with the ISSF line-break rule (subtract bullet radius).
- `cv/eval.py` — eval harness that runs `detect.py` on every image listed in
  `resources/paper_targets/metadata.yml` and reports per-image hit-count error
  and multiset-Jaccard score fidelity.

## How to run

From the repo root:

```bash
uv run python -m cv.eval
```

The pipeline can also be called directly:

```python
from cv.detect import detect
result = detect("resources/paper_targets/29.jpg", caliber="22lr")
print(result["scores"], result["total"])
```

## Status

**Spike only.** Numbers are far below the PRD ≥90% target — see the spike
report for failure-mode analysis and the recommended next iteration.
