"""Data seeders and helpers for the vision domain.

Per AGENTS.md §5 (Test Encapsulation), system tests MUST NOT use ORM tools
directly against domain models; they go through test_utils.py or the REST API.
S-03 adds the ``make_accepted_result`` seeder for the aggregation route's
system tests.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Optional

from src.domains.vision.models import AcceptedResult


def make_accepted_result(
    *,
    user_uuid: uuid.UUID,
    source_job: Optional[uuid.UUID] = None,
    target_type: str = "air_pistol",
    caliber_hint: Optional[str] = "9x19mm",
    distance: Optional[int] = 25,
    weapon_type: Optional[str] = "sport_pistol",
    holes: Optional[list[dict]] = None,
    score_average: Optional[float] = None,
    created_at: Optional[datetime] = None,
) -> AcceptedResult:
    """Create an ``AcceptedResult`` row directly (the aggregation queries read
    this table). Pure row factory — no env mutation.

    ``holes`` defaults to a 10-hole pattern each scoring 9 (score_average then
    defaults to 9.0). ``created_at`` lets the caller spread rows across
    calendar days to exercise the derived-session (calendar-day) aggregation
    logic; when omitted the row uses ``auto_now_add`` (now).
    """
    if holes is None:
        holes = [{"x": i * 10, "y": i * 10, "score": 9, "confidence": 0.9}
                 for i in range(10)]
    if score_average is None:
        score_average = sum(h["score"] for h in holes) / len(holes)
    ar = AcceptedResult(
        user_uuid=user_uuid,
        source_job=source_job or uuid.uuid4(),
        target_type=target_type,
        caliber_hint=caliber_hint,
        distance=distance,
        weapon_type=weapon_type,
        holes=holes,
        score_average=score_average,
    )
    if created_at is not None:
        # Bypass auto_now_add by saving then updating the timestamp.
        ar.save()
        AcceptedResult.objects.filter(id=ar.id).update(created_at=created_at)
        ar.refresh_from_db()
        return ar
    ar.save()
    return ar


def days_ago(n: int) -> datetime:
    """A timezone-aware datetime ``n`` days before now (midnight-local), for
    spreading ``AcceptedResult.created_at`` across calendar days in tests."""
    from django.utils import timezone
    return timezone.now() - timedelta(days=n)
