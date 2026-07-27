"""S-02 impl-review F3 regression (vision-side): the django-q2 Schedule row
that runs ``reap_stuck_jobs`` every 60s is created by migration 0003 and is
idempotent.

The BFF-side companion (``get_scoring_job`` no longer calls ``reap_stuck_jobs``)
lives in ``tests/system/test_reaper_schedule.py`` because asserting on
``bff.routers.scoring_routes`` from inside the vision domain would violate
the .importlinter "BFF Above Domains" contract.
"""
from __future__ import annotations

import importlib

import pytest
from django_q.models import Schedule

from src.domains.vision.migrations._reaper_constants import (
    REAPER_FUNC,
    REAPER_SCHEDULE_NAME,
)


pytestmark = pytest.mark.django_db


def test_reaper_schedule_row_exists_after_migrate() -> None:
    """The migration's ``RunPython`` created exactly one reaper Schedule row."""
    rows = Schedule.objects.filter(name=REAPER_SCHEDULE_NAME)
    assert rows.count() == 1
    row = rows.get()
    assert row.func == REAPER_FUNC
    assert row.schedule_type == "I"  # Schedule.MINUTES
    assert row.minutes == 1
    assert row.repeats == -1  # forever


def test_reaper_schedule_creation_is_idempotent() -> None:
    """Re-running the migration's create logic does NOT duplicate the row."""
    migration = importlib.import_module(
        "src.domains.vision.migrations.0003_schedule_stuck_job_reaper"
    )
    migration.create_reaper_schedule(Schedule._meta.apps, None)
    migration.create_reaper_schedule(Schedule._meta.apps, None)
    assert Schedule.objects.filter(name=REAPER_SCHEDULE_NAME).count() == 1
