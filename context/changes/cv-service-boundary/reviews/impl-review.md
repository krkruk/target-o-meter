<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Vision domain: AI hole-detection pipeline

- **Plan**: context/changes/cv-service-boundary/plan.md
- **Scope**: All 7 phases (full plan)
- **Date**: 2026-07-23
- **Verdict**: NEEDS ATTENTION
- **Findings**: [2 critical] [7 warnings] [1 observation]

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | WARNING |
| Scope Discipline | WARNING |
| Safety & Quality | FAIL |
| Architecture | PASS |
| Pattern Consistency | WARNING |
| Success Criteria | FAIL |

## Findings

### F1 — process_image has no idempotency guard; q2 retry double-bills

- **Severity**: CRITICAL
- **Impact**: MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: src/domains/vision/services.py:99-110
- **Detail**: process_image does ScoringJob.objects.get() then unconditionally writes status=RUNNING. settings.py:103 sets q2 retry=1200, and process_image re-raises after setting FAILED — q2 will re-enqueue. On retry the worker re-enters, overwrites FAILED→RUNNING→SUCCEEDED on the same job, calls the paid Google API a second time, and overwrites the previous deliverables (no cleanup). No select_for_update, no `if job.status in (SUCCEEDED, FAILED): return` early-exit.
- **Fix A (Recommended)**: Wrap the task body in `with transaction.atomic(): job = ScoringJob.objects.select_for_update().get(id=job_id)` and add a terminal-state guard `if job.status in (SUCCEEDED, FAILED): return`.
  - Strength: Standard Django task-idempotency pattern; closes both the double-bill and (partially) the stuck-row hole.
  - Tradeoff: One call-site refactor + a new test faking a retry.
  - Confidence: HIGH.
  - Blind spot: Haven't verified q2's lock semantics on SQLite WAL.
- **Fix B**: Make FAILED terminal by not re-raising after the FAILED save.
  - Strength: Smaller diff; stops q2's retry loop.
  - Tradeoff: Loses legitimate retry on transient errors; doesn't fix F2.
  - Confidence: MED.
  - Blind spot: Doesn't address F2.
- **Decision**: FIXED via Fix A — services.py:99-129 wraps claim in `transaction.atomic()` + `select_for_update()` + terminal-state guard. Existing tests pass.

### F2 — ScoringJob can be stranded in RUNNING forever (no reaper)

- **Severity**: CRITICAL
- **Impact**: MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: src/domains/vision/services.py:108-110, 166-169
- **Detail**: Status is set RUNNING and saved outside the success/failure atomic blocks. If the worker is SIGKILL'd (OOM, deploy, host reboot) between line 110 and the terminal save, the row stays RUNNING forever — no leased_until/heartbeat, no sweeper. Compounds with F1.
- **Fix**: Add a started_at timestamp on ScoringJob + a sweeper (scheduled q2 task or BFF-on-GET) that flips rows whose started_at < now - TIMEOUT back to QUEUED or FAILED.
  - Strength: Closes the SIGKILL window that F1's idempotency guard can't reach.
  - Tradeoff: New migration (started_at column) + a recurring task; needs a chosen TIMEOUT (~2× q_cluster.timeout=600).
  - Confidence: HIGH.
  - Blind spot: Whether q2 has a built-in stuck-task reaper to enable instead.
- **Decision**: FIXED — added `started_at` field (migration 0002), set in process_image claim block; added `reap_stuck_jobs()` in services.py with `STUCK_RUNNING_TIMEOUT_SECONDS=1200`. Existing tests pass.

### F3 — Phase 2 "load-bearing" regression gate silently skips in CI

- **Severity**: WARNING
- **Impact**: HIGH — architectural stakes; think carefully before deciding
- **Dimension**: Success Criteria
- **Location**: src/domains/vision/tests/test_geometry_regression.py:50; src/domains/vision/tests/conftest.py
- **Detail**: Plan §60/§298 calls the geometry regression gate "non-negotiable." Locally the gate runs and passes 10/10 (verified: 29.87s). But `resources/train/` is gitignored, so on a fresh clone or in CI `has_local_train_set()` returns False and all 10 tests silently skip.
- **Fix A (Recommended)**: Commit the 10 train images + metadata.yml (or git-lfs / fixture-release archive the conftest downloads and caches).
  - Strength: CI actually enforces the gate.
  - Tradeoff: Repo size grows; needs git-lfs decision.
  - Confidence: HIGH.
  - Blind spot: Licensing/PII constraints on the train images.
- **Fix B**: Add a CI-specific gate test that FAILS (not skips) when resources/train/ is absent; document the local-only run as deliberate scope reduction.
  - Strength: No repo bloat; makes the silent skip visible.
  - Tradeoff: Numerical invariant still unenforced in CI.
  - Confidence: MED.
  - Blind spot: CI env-var detection.
- **Decision**: FIXED via hybrid — added `regression_image_set()` to conftest that always yields the 4 byte-identical versioned fixtures (12, 46, 29, 21) and appends the remaining 6 ids when `resources/train/` is present locally. Removed `skipif` from `test_geometry_regression.py`. CI now enforces the gate on 4/10 ids; developer machines run all 10. Verified: 10 passed in 28.21s locally.

### F4 — Phase 6 CLI success criterion fails (CLI takes paths, not ids)

- **Severity**: WARNING
- **Impact**: HIGH — architectural stakes; think carefully before deciding
- **Dimension**: Plan Adherence / Success Criteria
- **Location**: src/domains/vision/__main__.py:63-66, 122
- **Detail**: Plan §57/§544/§552 specify CLI `python -m src.domains.vision [ids...]` with `ids=[12,46,29,21]` default; Phase-6 success criterion literally requires `python -m src.domains.vision 12 --detector mock --out /tmp/...` to write 3 files + _summary.json. Verified failure: that exact command exits 0 but emits "12: MISSING (skipping)" and writes only `_summary.json`. CLI takes `nargs="+"` of `Path`, not integer ids, and has no default. test_cli.py sidesteps this by passing a full path. The misleading exit-0-on-skip is itself a bug.
- **Fix A (Recommended)**: Restore the id-based CLI surface — accept ints, default to [12,46,29,21], resolve to resources/train/<id>.jpg (or tests/fixtures/<id>.jpg as fallback). Update test_cli.py to invoke with an id.
  - Strength: Every example in the plan works as written; success criterion passes verbatim.
  - Tradeoff: Removes path-based flexibility added in commit a17b250; intent may have shifted.
  - Confidence: MED.
  - Blind spot: a17b250's rationale suggests the change was intentional.
- **Fix B**: Update the plan's Phase 6 contract + success criterion to match the path-based CLI; have __main__ exit non-zero when every image skipped.
  - Strength: Honors the implementation as the new source of truth; smallest code change.
  - Tradeoff: Drift from §57 "Desired End State"; other docs (README, research) may reference id-based invocation.
  - Confidence: HIGH.
  - Blind spot: Other documentation may need updates.
- **Decision**: FIXED via Fix B — `__main__.py` now tracks `succeeded` count and returns 1 when 0 of N requested images produced deliverables. Plan §57 Desired End State + Phase 6 §544/§552 contracts updated to match the path-based CLI (path-based access is more scalable and will be reused for system testing). CLI test fixture passes; all-skipped exits 1.

### F5 — "One class per file" rule (lessons.md) violated in 7 files

- **Severity**: WARNING
- **Impact**: MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Pattern Consistency
- **Location**: src/domains/vision/geometry/classical_stages.py:101,109,170 (3 classes); src/domains/vision/detectors/google_ai_studio_detector.py:24,53; src/domains/vision/detectors/ollama_detector.py:34,61; src/domains/vision/geometry/{ring_detector,homography_refiner,geometry_pipeline,circular_points_rectifier}.py
- **Detail**: `context/foundation/lessons.md` is explicit: "One class per file … no second class." classical_stages.py's own module docstring acknowledges the rule while breaking it with 3 classes. The *Result dataclass + Worker pairs aren't "private helpers" — RefinementResult is imported cross-module (geometry_pipeline.py:37), which the lesson's "serves only that class" carve-out explicitly disqualifies.
- **Fix A (Recommended)**: Split classical_stages.py into image_grayscaler.py / black_disc_calibrator.py / issf_scorer.py; pull GoogleStudioVLMClient into google_studio_vlm_client.py and OllamaVLMClient into ollama_vlm_client.py; pull *Result dataclasses into geometry/results.py as a contract collection.
  - Strength: Brings the domain into compliance with its own lesson.
  - Tradeoff: ~10 new files, import-statement churn; re-run regression test.
  - Confidence: HIGH.
  - Blind spot: Low risk of touching cv/-port fidelity during the move.
- **Fix B**: Update the lesson to permit "Result dataclass + its worker" as a second carve-out; split only the clear violations (classical_stages.py, the VLM client subclasses).
  - Strength: Smaller diff.
  - Tradeoff: Weakens the lesson; precedent breeds more multi-class files.
  - Confidence: MED.
  - Blind spot: Where to draw the line becomes subjective.
- **Decision**: FIXED via Fix A — split `classical_stages.py` into `image_grayscaler.py` + `black_disc_calibrator.py` + `issf_scorer.py`; pulled `GoogleStudioVLMClient` into `google_studio_vlm_client.py` and `OllamaVLMClient` into `ollama_vlm_client.py`; pulled `RingDetection`/`RectificationResult`/`RefinementResult`/`GeometryResult` into `geometry/results.py` (the sanctioned ports.py/dtos.py-style contract collection). ruff + lint-imports clean; 33 tests pass (regression gate numerics preserved).

### F6 — Bare `except Exception:` silently swallows failures

- **Severity**: WARNING
- **Impact**: MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: src/domains/vision/geometry/geometry_pipeline.py:203; src/domains/vision/pipeline/pipeline_runner.py:104; src/domains/vision/geometry/ring_detector.py:156
- **Detail**: Three bare `except Exception:` blocks with no variable, no log. pipeline_runner.py:104 is the most consequential: when classical scoring fails it silently substitutes LLM scores for classical scores, so scores_classical becomes identical to scores_llm and any later diagnostic comparing the two is meaningless.
- **Fix**: Replace with `except Exception as exc: logger.warning(..., exc_info=True)` and emit an explicit metrics flag (e.g. metrics["classical_score_fallback"]=True).
  - Strength: Makes the fallback observable without changing the happy path.
  - Tradeoff: Three call sites; need a module logger.
  - Confidence: HIGH.
  - Blind spot: None significant.
- **Decision**: FIXED — added module loggers; replaced bare excepts with logged warnings + `exc_info=True`; added `classical_score_fallback` flag to `result_dict` so the substitution is observable downstream. 33 tests pass.

### F7 — storage.read_upload / absolute_path lack path-traversal containment

- **Severity**: WARNING
- **Impact**: LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: src/domains/vision/pipeline/storage.py:61-67
- **Detail**: (Path(self._storage.location) / stored_path).read_bytes() does no containment check. Safe today — stored_path always originates from save_upload with a hex-controlled digest. But the moment the BFF passes anything user-controlled, /etc/passwd and ../../.. are in scope. Defense in depth before the BFF lands.
- **Fix**: Add `_safe_join(stored_path)` doing `resolved.relative_to(Path(self._storage.location).resolve())` and raising ValueError on miss. Apply in read_upload, absolute_path, deliverable_dir.
  - Strength: Standard Django-storage containment; closes the hole before BFF wiring exposes it.
  - Tradeoff: ~6 LOC + one test with ../etc/passwd.
  - Confidence: HIGH.
  - Blind spot: None significant.
- **Decision**: FIXED — added `_safe_join(stored_path)` + cached `_root` to `ScoringStorage`; applied in `read_upload`, `absolute_path`, `deliverable_dir`. Raises `ValueError` on escape. 33 tests pass.

### F8 — Grep-gate success criterion fails as literally specified

- **Severity**: WARNING
- **Impact**: LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Success Criteria
- **Location**: context/changes/cv-service-boundary/plan.md (Phases 1.5, 2.3, 4.3, 6.3, 7.5)
- **Detail**: The plan's literal command `rg -n "^import cv|^from cv\b" src/domains/vision` returns 12 matches — all `import cv2` (the legitimate OpenCV library), because the regex `^import cv` matches the prefix `cv2`. The success criterion says "returns no matches / empty." Intent is intact (test_no_cv_imports.py passes and explicitly allows cv2), but the success criterion as written fails on every machine.
- **Fix**: Tighten the regex in the plan to `^import cv\.|^from cv\.` (or pin the success criterion on test_no_cv_imports.py passing).
  - Strength: Success criterion becomes true; the real guardrail (the test) keeps doing the work.
  - Tradeoff: Plan edit only.
  - Confidence: HIGH.
  - Blind spot: None significant.
- **Decision**: FIXED — replaced all 9 occurrences of the loose regex `\^import cv|\^from cv\b` in plan.md with the tightened `\^import cv\.|\^from cv\.` (escape the dot — matches only `cv.<thing>` not `cv2`). Verified: tightened regex exits 1 on `src/domains/vision`.

### F9 — Necessary addenda not documented in plan; small drifts bundled

- **Severity**: WARNING
- **Impact**: LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Adherence / Scope Discipline / Pattern Consistency
- **Location**: src/target_o_meter/settings.py:40-43, 93-108; src/domains/vision/__init__.py; src/domains/core/models.py; src/domains/vision/services.py:81-96
- **Detail**: Four small drifts: (a) settings.py adds django_q + Q_CLUSTER — undocumented but REQUIRED for services.py:91 to import; workers=3 honors AGENTS.md §2 cap. (b) vision/__init__.py empty despite plan §80 "public re-exports" contract. (c) core/models.py docstring says "Identity domain ORM models" — copy-paste defect. (d) schedule_image_processing docstring claims atomicity but no test patches async_task to raise and asserts rollback; correct only while orm='default'.
- **Fix**: (a) Add an "Addenda" section to the plan documenting settings.py + fixtures + magenta_gt.py addenda. (b) Drop the empty vision/__init__.py from the contract, or add the promised re-exports. (c) Fix the core/models.py docstring typo. (d) Add a rollback test + an orm-broker guard test.
  - Strength: Plan becomes reliable ground truth; closes two real (small) holes.
  - Tradeoff: Four small edits across plan + code.
  - Confidence: HIGH.
  - Blind spot: None significant.
- **Decision**: FIXED — (a) added "Addenda discovered during implementation" section to plan.md documenting settings.py + fixtures + magenta_gt.py + reap_stuck_jobs + idempotency guard + CLI surface revision; (b) updated package layout to reflect empty `__init__.py` (dropped "public re-exports" contract); (c) fixed core/models.py docstring typo ("Identity" → "Core"); (d) added `test_schedule_image_processing_rolls_back_if_enqueue_fails`, `test_q_cluster_uses_orm_default_broker`, `test_process_image_is_idempotent_on_terminal_state`, `test_reap_stuck_jobs_flips_stale_running_rows`, `test_reap_stuck_jobs_leaves_fresh_running_rows_alone`. 38 tests pass.

### F10 — Minor hygiene nits (bundle)

- **Severity**: OBSERVATION
- **Impact**: LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality / Pattern Consistency
- **Location**: .env.example:1; src/domains/vision/services.py:153, 216-240, 182-193; src/domains/vision/tests/test_no_cv_imports.py:20-30
- **Detail**: Five nits: (1) `.env.example` ships a joke GOOGLE_API_KEY placeholder matching secret-scanner patterns. (2) services.py:153 imports `_json_default` (private) across subpackages; same for `_analysis_to_detection_result` and `_average_shared_metric`. (3) services.py:216-240 _job_to_dto hand-rebuilds the DTO from stored dict with silent 0-defaults on malformed holes. (4) services.py:182-193 get_job raises DoesNotExist vs PermissionError — existence oracle for ID-probers. (5) test_no_cv_imports.py:20-30 AST walker only inspects top-level imports — no hidden cv/ import exists today, but the guard is weaker than it looks.
- **Fix**: Address together or accept; see sub-agent report for per-item recommendations.
  - Strength: Defense-in-depth cleanup.
  - Tradeoff: None — all narrowly scoped.
  - Confidence: HIGH.
  - Blind spot: None significant.
- **Decision**: FIXED (nits 2–5; nit 1 left as user requested — "I like it" — and confirmed no real keys ever ship in .env.example). Renamed cross-module private helpers to public: `json_default`, `analysis_to_detection_result`, `average_shared_metric`. `_job_to_dto` now raises on malformed hole dicts instead of silently substituting 0. `get_job` catches `DoesNotExist` and re-raises as `PermissionError` (closes the existence oracle). `test_no_cv_imports.py` AST walker now uses `ast.walk` for full-tree coverage. 38 tests pass.
