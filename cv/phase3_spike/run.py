"""Phase 3 LLM spike — standalone harness (Step 1).

Decoupled from the fused CV pipeline. Reads the EXISTING normalized
``<id>_04_llm_input.png`` images produced by Phase 2.5, feeds each to
Gemma 4 31B-it via LangChain with structured output, and compares the
returned scores against metadata.yml (score-multiset Jaccard + per-score
breakdown + misalignment flags).

Step 2 will adapt this into the live pipeline.

Usage:
    uv run python -m cv.phase3_spike.run 12 46 29 21
    uv run python -m cv.phase3_spike.run 12 --out /tmp/phase3
"""
from __future__ import annotations

import argparse
import collections
import json
import shutil
import statistics
from pathlib import Path

import cv2

from cv.phase3_spike.client import VLMSpikeClient
from cv.phase3_spike.compare import (
    exact_count_match,
    misalignment_flags,
    per_score_breakdown,
    score_jaccard,
    score_multiset,
)
from cv.phase3_spike.metadata import (
    gt_hits_for,
    load_metadata,
    primary_caliber_for,
    ring1_px_for,
)
from cv.phase3_spike.viz import draw_magenta_holes

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_FUSED = _REPO_ROOT / "resources" / "train" / "intermediate_fused"
_DEFAULT_OUT = _REPO_ROOT / "resources" / "train" / "intermediate_phase3_spike"
_DEFAULT_IDS = ["12", "46", "29", "21"]
_DEFAULT_TARGET_TYPE = "air_pistol"


def run_one(client: VLMSpikeClient, stem: str, fused_dir: Path, meta: dict, out_dir: Path) -> dict:
    """Run the LLM on one image and compare against metadata.yml.

    Also copies the evaluated llm_input into ``out_dir`` and draws magenta
    dots (proportional to caliber, 70% of hole) for visual comparison.
    """
    img_path = fused_dir / f"{stem}_04_llm_input.png"
    if not img_path.exists():
        raise FileNotFoundError(f"Missing normalized LLM input: {img_path}")

    entry = meta.get(stem) or {"hits": [], "caliber": None}
    gt_hits = gt_hits_for(entry)
    gt_cal = primary_caliber_for(entry)
    r1px = ring1_px_for(stem, fused_dir)
    ring_step = (r1px / 9.0) if r1px else None

    analysis, call_meta = client.analyze(
        image_path=img_path,
        target_type=_DEFAULT_TARGET_TYPE,
        target_ring1_px=r1px if r1px is not None else 394.0,
        ring_step_px=ring_step,
        primary_caliber=gt_cal,
    )

    llm_scores = [h.score for h in analysis.holes]
    llm_ms = score_multiset(llm_scores)
    gt_ms = score_multiset(gt_hits)
    jac = score_jaccard(llm_ms, gt_ms)
    caliber_dist = collections.Counter(h.caliber for h in analysis.holes)

    # ---- Visual deliverables: copy llm_input + draw magenta-dot overlay ----
    # The LLM input is grayscale; copy it as-is for reference, then render the
    # predicted holes as magenta dots (radius ∝ caliber, 70% of hole).
    r1px_for_draw = r1px if r1px is not None else 394.0
    holes_dump = [h.model_dump() for h in analysis.holes]
    llm_input_gray = cv2.imread(str(img_path), cv2.IMREAD_GRAYSCALE)
    if llm_input_gray is not None:
        # Copy the evaluated llm_input for side-by-side review.
        shutil.copy2(img_path, out_dir / f"{stem}_llm_input.png")
        # Magenta-dot overlay on the same normalized image.
        marked = draw_magenta_holes(
            image_1024_gray=llm_input_gray,
            holes=holes_dump,
            target_type=_DEFAULT_TARGET_TYPE,
            target_ring1_px=r1px_for_draw,
        )
        cv2.imwrite(str(out_dir / f"{stem}_marked.png"), marked)

    return {
        "image": f"{stem}.jpg",
        "llm_input": str(img_path),
        "target_type": _DEFAULT_TARGET_TYPE,
        "target_ring1_px": r1px,
        "ring_step_px": ring_step,
        "primary_caliber_hint": gt_cal,
        "gt_caliber_raw": entry.get("caliber"),
        "gt_n_holes": len(gt_hits),
        "llm_n_holes": len(analysis.holes),
        "gt_hits_sorted": gt_hits,
        "llm_scores": llm_scores,
        "score_jaccard": round(jac, 4),
        "exact_count_match": exact_count_match(llm_ms, gt_ms),
        "per_score": per_score_breakdown(llm_ms, gt_ms),
        "misalignment_flags": misalignment_flags(llm_ms, gt_ms),
        "caliber_distribution": dict(sorted(caliber_dist.items())),
        "notes": analysis.notes,
        "call_meta": call_meta,
        "holes": [h.model_dump() for h in analysis.holes],
    }


def _print_table(results: list[dict]) -> None:
    print(
        f"\n{'img':>5} {'gt_n':>5} {'llm_n':>6} {'jac':>6} "
        f"{'count':>6} {'time_s':>7}  flags"
    )
    for r in results:
        flags = "; ".join(r["misalignment_flags"]) if r["misalignment_flags"] else "OK"
        print(
            f"{r['image'].split('.')[0]:>5} "
            f"{r['gt_n_holes']:>5} "
            f"{r['llm_n_holes']:>6} "
            f"{r['score_jaccard']:>6.2f} "
            f"{'Y' if r['exact_count_match'] else 'N':>6} "
            f"{r['call_meta']['elapsed_s']:>7.1f}  {flags}"
        )
    if results:
        mean_j = statistics.mean(r["score_jaccard"] for r in results)
        print(f"\nmean score-Jaccard: {mean_j:.3f}  "
              f"(classical baseline ~0.255; PRD bar 0.90)")


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("ids", nargs="*", default=_DEFAULT_IDS)
    p.add_argument("--fused-dir", default=str(_DEFAULT_FUSED),
                   help="dir with the existing <id>_04_llm_input.png + result.json")
    p.add_argument("--out", default=str(_DEFAULT_OUT))
    p.add_argument("--model", default="gemma-4-31b-it",
                   help="Google AI Studio model id (e.g. gemini-3.1-flash-lite)")
    args = p.parse_args()

    fused_dir = Path(args.fused_dir)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    client = VLMSpikeClient(model=args.model)
    meta = load_metadata()
    print(f"model={client.model}  temp={client.temperature}  fused_dir={fused_dir}")

    results: list[dict] = []
    for stem in args.ids:
        try:
            r = run_one(client, stem, fused_dir, meta, out_dir)
            results.append(r)
            (out_dir / f"{stem}_llm_result.json").write_text(json.dumps(r, indent=2))
            print(f"{stem:>5} OK  jac={r['score_jaccard']:.2f}  "
                  f"n_llm={r['llm_n_holes']} n_gt={r['gt_n_holes']}  "
                  f"{r['call_meta']['elapsed_s']:.1f}s")
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"{stem:>5} ERR {type(e).__name__}: {str(e)[:160]}")
            results.append({"image": f"{stem}.jpg", "error": str(e)})

    _print_table([r for r in results if "error" not in r])
    (out_dir / "_summary.json").write_text(json.dumps(results, indent=2))
    print(f"\nwrote: {out_dir}/_summary.json  and per-image *_llm_result.json")


if __name__ == "__main__":
    main()
