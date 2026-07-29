"""The ban history model (S-04).

One row per ban event; "active" is a *query* (``banned_until > now() AND
lifted_at IS NULL``), not a column or a uniqueness constraint — multiple
historical bans per user are allowed, and the UI shows only the active +
last-expired state. The ``Ban`` cascade-deletes with its ``User`` (intra-domain
FK; AGENTS.md §5 forbids only *cross-domain* FKs).

One class per file per ``context/foundation/lessons.md`` (the ``One class per
file`` rule). Module-level ``_DURATION_DELTAS`` is a supporting constant for
*this* class only — the explicit exception the lesson allows.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from django.db import models


class Duration(models.TextChoices):
    """The four owner-selectable ban durations.

    Stored as short codes so the column stays narrow (``max_length=8``); the
    UI maps them to human labels ("1 day" etc.). The ``_DURATION_DELTAS`` map
    below is the single place a duration code maps to a ``timedelta``.
    """

    ONE_HOUR = "1h", "1 hour"
    ONE_DAY = "1d", "1 day"
    SEVEN_DAYS = "7d", "7 days"
    THIRTY_DAYS = "30d", "30 days"


_DURATION_DELTAS: dict[str, timedelta] = {
    Duration.ONE_HOUR: timedelta(hours=1),
    Duration.ONE_DAY: timedelta(days=1),
    Duration.SEVEN_DAYS: timedelta(days=7),
    Duration.THIRTY_DAYS: timedelta(days=30),
}


class Ban(models.Model):
    """A single ban event on a ``User``.

    - ``reason`` is the owner's required free-text justification (min 5 chars
      enforced by the DTO/service, not the column).
    - ``duration_kind`` is one of the four ``Duration`` codes.
    - ``banned_at`` is set automatically on creation; ``banned_until`` is a
      concrete UTC datetime computed at creation (``banned_at + duration``),
      so expiry is automatic (a single comparison, no cron).
    - ``lifted_at`` is null while the ban is active (or expired-by-time); the
      owner sets it on early unban.

    "Active ban" = ``banned_until > now() AND lifted_at IS NULL``.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        "identity.User",
        on_delete=models.CASCADE,
        related_name="bans",
    )
    reason = models.TextField()
    duration_kind = models.CharField(max_length=8, choices=Duration.choices)
    banned_at = models.DateTimeField(auto_now_add=True)
    # Concrete UTC datetime computed at creation (``banned_at + duration``).
    # ``USE_TZ=True`` (settings.py) so this is timezone-aware.
    banned_until = models.DateTimeField()
    # Null while active/expired-by-time; set when the owner unbans early.
    lifted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = "identity"
        db_table = "identity_ban"
        ordering = ["-banned_at"]  # most recent first

    def __str__(self) -> str:  # pragma: no cover — cosmetic
        return f"Ban(user={self.user_id}, until={self.banned_until}, lifted={self.lifted_at})"
