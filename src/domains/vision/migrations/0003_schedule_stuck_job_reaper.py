"""Register a django-q2 Schedule row that runs ``reap_stuck_jobs`` every 60s.

S-02 impl-review F3: the BFF's GET ``/v1/scoring/jobs/{id}`` previously called
``reap_stuck_jobs()`` on every poll, taking the SQLite write lock on every
read. Reaping was always documented as "intended to be called by a scheduled
q2 task or the BFF-on-GET" (services.py docstring); this migration moves it
to the scheduled-task path so reads stay pure reads.

The 1200s ``STUCK_RUNNING_TIMEOUT_SECONDS`` makes a 60s cadence plenty — a
reaped row appears within ≤60s of staleness, far under the 20-minute reap
window. ``Schedule.MINUTES`` with ``minutes=1`` + ``repeats=-1`` (forever) is
the django-q2 idiom for "every minute, indefinitely".

Idempotent: re-running this migration (or running it on a DB that already has
the row) is a no-op. The lookup key is the ``name`` field.
"""

from django.db import migrations
from django.utils import timezone

from src.domains.vision.migrations._reaper_constants import (
    REAPER_FUNC,
    REAPER_SCHEDULE_NAME,
)


def create_reaper_schedule(apps, schema_editor):
    """Idempotently create the reaper Schedule row."""
    Schedule = apps.get_model("django_q", "Schedule")
    # Use the literal 'I' (Minutes) rather than Schedule.MINUTES — the
    # historical model returned by apps.get_model doesn't carry the class
    # constants the live model exposes.
    Schedule.objects.get_or_create(
        name=REAPER_SCHEDULE_NAME,
        defaults={
            "func": REAPER_FUNC,
            "schedule_type": "I",  # Schedule.MINUTES
            "minutes": 1,
            "repeats": -1,  # forever
            "next_run": timezone.now(),
        },
    )


def remove_reaper_schedule(apps, schema_editor):
    """Remove the reaper Schedule row on reverse."""
    Schedule = apps.get_model("django_q", "Schedule")
    Schedule.objects.filter(name=REAPER_SCHEDULE_NAME).delete()


class Migration(migrations.Migration):

    dependencies = [
        # The reaper targets RUNNING ScoringJob rows whose started_at exceeds
        # the timeout — depends on the started_at column landing first.
        ("vision", "0002_scoringjob_started_at"),
        # Schedule rows live in django_q's table.
        ("django_q", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_reaper_schedule, remove_reaper_schedule),
    ]
