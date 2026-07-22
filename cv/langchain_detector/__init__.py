"""LangChain VLM detector for the Target-o-meter hole-detection seam (Phase 3).

The live-pipeline ``HoleDetector`` strategy. Backed by a Google AI Studio
vision model (locked: ``gemini-3.5-flash-lite``) via LangChain's
``with_structured_output``. Plugs in behind ``cv.detector_base.HoleDetector``
alongside ``MockDetector`` — geometry is unchanged; only the detector call
differs.

Modules:
    detector — LangChainDetector(HoleDetector), the strategy implementation
    client   — PipelineVLMClient, spike client + analyze_array (in-memory input)
    schema   — re-export of the Step-1 Pydantic models (unchanged)
    prompt   — re-export of the Step-1 7-layer prompt builder (unchanged)

See context/changes/cv-service-boundary/research-ai-detection.md § Phase 3
Step 2 handoff.
"""
from cv.langchain_detector.detector import LangChainDetector

__all__ = ["LangChainDetector"]
