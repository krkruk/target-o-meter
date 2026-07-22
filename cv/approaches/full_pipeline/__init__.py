"""Full pipeline = fused geometry + live LLM detector (Phase 3 Step 2).

A copy of the STABLE Phase-2.5 fused pipeline (``cv/approaches/fused/``) with
the MockDetector swapped for the locked LLM detector (``gemini-3.5-flash-lite``)
behind the existing ``HoleDetector`` seam. Geometry is byte-for-byte the fused
pipeline; only the detector call differs.

Per-image outputs (the user's Step-2 spec — EXACTLY 3 files, not the 14-file
Phase-2.5 manifest):
  <id>_llm_input.png   1024×1024 normalized orthogonal image (the LLM input)
  <id>_marked.png      llm_input + magenta dots (∝ caliber, 70% of hole)
                       + faint canonical ring frame + score labels
  <id>_result.json     LLM structured output (x, y, score, confidence, caliber)
                       + target_type + notes + ring geometry

CLI:
  uv run python -m cv.approaches.full_pipeline.run 12 46 29 21
  uv run python -m cv.approaches.full_pipeline.run 12 46 29 21 \\
      --detector langchain --model gemini-3.5-flash-lite
  uv run python -m cv.approaches.full_pipeline.run 12 --debug   # + 14-file diag

See context/changes/cv-service-boundary/research-ai-detection.md § Phase 3
Step 2 handoff.
"""
