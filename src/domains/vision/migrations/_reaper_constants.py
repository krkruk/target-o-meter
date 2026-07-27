"""Shared constants for the stuck-job reaper Schedule row.

Lives outside ``0003_schedule_stuck_job_reaper.py`` so tests and future
migrations can import it without Django complaining about importing
migration code at runtime.
"""

# The django-q2 Schedule ``name`` field — the idempotency key for the row.
REAPER_SCHEDULE_NAME = "reap-stuck-scoring-jobs"

# The dotted path the Schedule's ``func`` field points at. The vision domain
# owns the reaper, so the path is anchored on ``src.domains.vision.services``.
REAPER_FUNC = "src.domains.vision.services.reap_stuck_jobs"
