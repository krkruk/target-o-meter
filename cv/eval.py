"""CV spike eval harness.

Reads resources/paper_targets/metadata.yml (ground truth) and runs the
detection pipeline on every image. Reports per-image:

  - hit count error:  |n_pred - n_true|
  - score Jaccard:    multiset(pred) ∩ multiset(true) / union (the PRD ≥90% metric proxy)
  - failure stage:    None or one of homography/rings/morph/watershed/scoring

Run from the repo root with:

    uv run python -m cv.eval
"""

from __future__ import annotations

import math
import sys
from collections import Counter
from pathlib import Path

import yaml

from cv.detect import detect

REPO_ROOT = Path(__file__).resolve().parent.parent
META_PATH = REPO_ROOT / "resources" / "paper_targets" / "metadata.yml"
IMAGE_DIR = REPO_ROOT / "resources" / "paper_targets"


def multiset_jaccard(a: list[int], b: list[int]) -> float:
    """Multiset Jaccard: |∩| / |∪| treating inputs as multisets."""
    ca, cb = Counter(a), Counter(b)
    inter = sum((ca & cb).values())
    union = sum((ca | cb).values())
    if union == 0:
        return 1.0 if inter == 0 else 0.0
    return inter / union


def run() -> int:
    if not META_PATH.exists():
        print(f"ERROR: metadata file not found at {META_PATH}", file=sys.stderr)
        return 2

    with open(META_PATH) as fh:
        meta = yaml.safe_load(fh)

    rows: list[dict] = []
    for img_name, info in sorted(meta.items(), key=lambda kv: int(kv[0].split(".")[0])):
        true_hits = list(info.get("hits") or [])
        caliber = info.get("caliber")
        img_path = IMAGE_DIR / img_name
        if not img_path.exists():
            rows.append({
                "id": img_name, "n_true": len(true_hits), "n_pred": 0,
                "count_err": len(true_hits), "jaccard": 0.0,
                "caliber": str(caliber), "failure_stage": "missing_image",
            })
            continue
        try:
            res = detect(img_path, caliber)
        except Exception as exc:  # noqa: BLE001 — eval must keep going
            rows.append({
                "id": img_name, "n_true": len(true_hits), "n_pred": 0,
                "count_err": len(true_hits), "jaccard": 0.0,
                "caliber": str(caliber), "failure_stage": f"exception:{type(exc).__name__}",
            })
            continue

        pred_scores = res["scores"]
        rows.append({
            "id": img_name,
            "n_true": len(true_hits),
            "n_pred": len(pred_scores),
            "count_err": abs(len(pred_scores) - len(true_hits)),
            "jaccard": multiset_jaccard(pred_scores, true_hits),
            "caliber": str(caliber),
            "failure_stage": res["failure_stage"] or "",
        })

    # --- print table ---------------------------------------------------------
    hdr = f"| {'id':<8} | {'n_true':>6} | {'n_pred':>6} | {'count_err':>9} | {'jaccard':>8} | {'caliber':<10} | {'failure_stage':<14} |"
    sep = "|" + "|".join(["-" * (len(col) + 2) for col in [
        " " * 8, " " * 6, " " * 6, " " * 9, " " * 8, " " * 10, " " * 14
    ]]) + "|"
    print(hdr)
    print(sep)
    for r in rows:
        print(
            f"| {r['id']:<8} | {r['n_true']:>6} | {r['n_pred']:>6} | "
            f"{r['count_err']:>9} | {r['jaccard']:>8.2f} | {r['caliber']:<10} | "
            f"{r['failure_stage']:<14} |"
        )

    # --- aggregates ----------------------------------------------------------
    n = len(rows)
    mean_jac = sum(r["jaccard"] for r in rows) / n
    pct_count0 = 100 * sum(1 for r in rows if r["count_err"] == 0) / n
    pct_jac_ge_90 = 100 * sum(1 for r in rows if r["jaccard"] >= 0.9) / n
    rmse_count = math.sqrt(sum(r["count_err"] ** 2 for r in rows) / n)

    fail_stages = Counter(r["failure_stage"] for r in rows if r["failure_stage"])

    print()
    print("Aggregate metrics")
    print("-----------------")
    print(f"  N images                     : {n}")
    print(f"  Mean score Jaccard           : {mean_jac:.3f}")
    print(f"  % images count_err == 0      : {pct_count0:.1f}%")
    print(f"  % images Jaccard >= 0.90     : {pct_jac_ge_90:.1f}%")
    print(f"  Hit-count RMSE               : {rmse_count:.2f}")
    print(f"  Failure stages               : {dict(fail_stages)}")

    # Per-caliber breakdown
    print()
    print("Per-caliber breakdown")
    print("---------------------")
    by_cal: dict[str, list[dict]] = {}
    for r in rows:
        by_cal.setdefault(r["caliber"], []).append(r)
    for cal, rs in sorted(by_cal.items()):
        m = len(rs)
        mj = sum(x["jaccard"] for x in rs) / m
        pc = 100 * sum(1 for x in rs if x["count_err"] == 0) / m
        print(f"  {cal:<10} N={m:>3}  mean_jac={mj:.3f}  pct_count0={pc:.1f}%")

    return 0


if __name__ == "__main__":
    sys.exit(run())
