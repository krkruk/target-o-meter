"""S-02 impl-review F3 regression (BFF-side): ``get_scoring_job`` no longer
calls ``reap_stuck_jobs()``. Reaping moved to a django-q2 Schedule (every
60s, registered by vision migration 0003) so the GET read path stays off the
SQLite write lock that the 1500ms client poll would otherwise serialize on.

The vision-side companion (the Schedule row exists + is idempotent) lives in
``src/domains/vision/tests/test_reaper_schedule.py``; the BFF behavior is
asserted here because importing ``bff.routers.scoring_routes`` from inside
the vision domain would violate the .importlinter "BFF Above Domains"
contract.
"""
from __future__ import annotations

import inspect

import pytest

from src.bff.routers import scoring_routes


pytestmark = [pytest.mark.django_db, pytest.mark.dev]


def test_get_scoring_job_does_not_call_reap_stuck_jobs() -> None:
    """The read path is off the write lock: ``reap_stuck_jobs`` is neither
    imported into ``scoring_routes`` nor referenced in ``get_scoring_job``'s
    source. Reaping runs on the Schedule row instead.
    """
    # 1. The BFF module no longer imports reap_stuck_jobs.
    assert not hasattr(scoring_routes, "reap_stuck_jobs"), (
        "scoring_routes re-imported reap_stuck_jobs — the read path should "
        "stay off the SQLite write lock (see S-02 impl-review F3)."
    )
    # 2. The route handler's source makes no reaping call.
    src = inspect.getsource(scoring_routes.get_scoring_job)
    assert "reap_stuck_jobs()" not in src
