"""Phase 3 LLM spike (Step 1) — standalone harness, decoupled from the fused CV pipeline.

Modules:
    schema    — Pydantic TargetAnalysis / Hole (structured output)
    prompt    — 7-layer system-prompt builder
    client    — Gemma 4 31B-it on Google AI Studio via LangChain
    metadata  — metadata.yml + fused result.json loaders
    compare   — score-multiset Jaccard + misalignment flags
    run       — CLI: uv run python -m cv.phase3_spike.run 12 46 29 21

See context/changes/cv-service-boundary/research-ai-detection.md § Phase 3.
"""
