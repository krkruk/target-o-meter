"""Schema re-export.

The Phase 3 Step 2 detector uses the SAME Pydantic schema as the Step-1 spike
(per the handoff: "RE-EXPORT cv.phase3_spike.schema — same Pydantic models").
The locked model already parses ``TargetAnalysis`` cleanly on the 4-image set;
no schema changes for Step 2.
"""
from cv.phase3_spike.schema import Hole, TargetAnalysis

__all__ = ["Hole", "TargetAnalysis"]
