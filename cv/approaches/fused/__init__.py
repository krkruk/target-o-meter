"""Fused pipeline: multiring detection + iteredge differential refinement.

Phase 2.5 of the cv-service-boundary research. Fuses:

  - multiring (DETECTION + initial affine H via circular-points method)
  - iteredge  (8-DOF differential refinement against edge distance transform)

Per-image outputs (9 standard + 4 per-stage refinement intermediates):
  <id>_01_intake.png         EXIF-oriented source
  <id>_02_crop.png           after localization (multiring, logo-rejecting)
  <id>_02b_detect.png        multiring detected ellipses on top of Canny edges
  <id>_03_warp.png           warped crop with ring overlay (post-refinement)
  <id>_04_llm_input.png      1024x1024 normalized (actual LLM input)
  <id>_05_llm_predict.png    FINAL PRODUCT: 1024 + ring overlay + magenta holes + scores
  <id>_06_crop_predict.png   crop + inverted holes + final ring overlay
  <id>_07_source_predict.png source + fully-inverted holes
  <id>_08_stage{1..4}.png    per-stage refinement projection (track overshoots)
  <id>_08_stages_strip.png   all 4 stages concatenated horizontally
  <id>_result.json           structured output

CLI:
  uv run python -m cv.approaches.fused.run 12 46 29 21
"""
