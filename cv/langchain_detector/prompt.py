"""Prompt re-export.

The Phase 3 Step 2 detector uses the SAME 7-layer system prompt as the Step-1
spike (per the handoff: "RE-EXPORT cv.phase3_spike.prompt — same 7-layer
builder"). The prompt is the load-bearing artifact of the locked Step-1 result
(mean Jaccard 0.799); no prompt changes for Step 2.
"""
from cv.phase3_spike.prompt import build_system_prompt, build_user_text

__all__ = ["build_system_prompt", "build_user_text"]
