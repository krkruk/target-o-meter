"""CLI: run the multiring pipeline on one or more train images.

Usage:
    uv run python -m cv.approaches.multiring.run [ids...]

Without args, runs the 4-image test set: 12 46 29 21.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from cv.approaches.multiring.pipeline import run_pipeline


DEFAULT_IDS = [12, 46, 29, 21]
DEFAULT_OUT_DIR = "resources/train/intermediate_multiring"


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    out_dir = DEFAULT_OUT_DIR
    projective_refine = True
    ids: list[int] = []
    for a in argv:
        if a.startswith("--out="):
            out_dir = a.split("=", 1)[1]
        elif a.startswith("--no-projective"):
            projective_refine = False
        elif a.startswith("--projective"):
            projective_refine = True
        else:
            ids.append(int(a))
    if not ids:
        ids = list(DEFAULT_IDS)

    out_path = Path(out_dir)
    rows = []
    for img_id in ids:
        path = Path("resources/train") / f"{img_id}.jpg"
        if not path.exists():
            print(f"  ! {img_id}: missing {path}")
            continue
        try:
            result = run_pipeline(
                image_path=path,
                out_dir=out_path,
                write_intermediates=True,
                projective_refine=projective_refine,
            )
        except Exception as exc:    # noqa: BLE001
            print(f"  ! {img_id}: EXCEPTION {type(exc).__name__}: {exc}")
            continue

        if result.get("ok"):
            n_rings = len(result.get("rings_detected", []))
            aniso_b = result["calibration"].get("anisotropy_before", float("nan"))
            aniso_a = result["calibration"].get("anisotropy_after", float("nan"))
            err = result["self_test"]["bullseye_invert_err_px"]
            print(f"  ✓ {img_id:3d} ok rings={n_rings} aniso[{aniso_b:.3f}→{aniso_a:.3f}] "
                  f"invert_err={err:.2e} total_llm={result['total_llm']}")
            rows.append({
                "id": img_id,
                "ok": True,
                "n_rings": n_rings,
                "anisotropy_before": round(aniso_b, 4),
                "anisotropy_after": round(aniso_a, 4),
                "invert_err_px": err,
                "notes": result.get("notes", ""),
            })
        else:
            print(f"  ✗ {img_id:3d} FAIL stage={result.get('failure_stage')} "
                  f"reason={result.get('reason')}")
            rows.append({
                "id": img_id,
                "ok": False,
                "stage": result.get("failure_stage"),
                "reason": result.get("reason"),
            })

    # Write summary alongside individual results.
    (out_path / "_summary.json").write_text(json.dumps(rows, indent=2))
    print(f"\nWrote {len(rows)} results to {out_path}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
