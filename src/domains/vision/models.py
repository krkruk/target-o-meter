"""Vision domain ORM models.

Persist pipeline-job metadata only (AGENTS.md §1: DB stores metadata; binaries
on storage). The q2 task reads its input path and writes its result/paths back
here.
"""
from __future__ import annotations

import uuid
from django.db import models


class ScoringJob(models.Model):
    """Persistent state for one image-scoring job.

    Per AGENTS.md §5 (No Foreign Keys Across Domains), ``user_uuid`` is a plain
    UUIDField — owner identity comes from the Identity domain via the BFF; the
    vision domain only stores the UUID for owner-only access checks.

    The q2 task body reads ``input_path``, runs the pipeline, then writes the
    3 deliverable paths + result JSON back here.
    """

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_uuid = models.UUIDField(db_index=True)

    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.QUEUED,
    )

    # Inputs (set at enqueue time by schedule_image_processing).
    input_path = models.CharField(max_length=512)
    target_type = models.CharField(max_length=32, default="air_pistol")
    caliber_hint = models.CharField(max_length=64, null=True, blank=True)
    # S-03 FR-009 confirmation params. Collected in the wizard pre-upload,
    # persisted on the pipeline request, and snapshotted onto AcceptedResult
    # at accept. Both nullable: pre-existing rows + the mock path may omit
    # them. ``distance`` is meters (the wizard's DISTANCES_M span 7–500);
    # ``weapon_type`` is free-text (ISSF-appropriate values live client-side).
    distance = models.PositiveIntegerField(null=True, blank=True)
    weapon_type = models.CharField(max_length=32, null=True, blank=True)

    # Outputs (set by process_image on success).
    result = models.JSONField(null=True, blank=True)
    llm_input_path = models.CharField(max_length=512, null=True, blank=True)
    marked_image_path = models.CharField(max_length=512, null=True, blank=True)
    result_json_path = models.CharField(max_length=512, null=True, blank=True)

    # Error trace (set by process_image on failure).
    error = models.TextField(null=True, blank=True)

    # Lifecycle timestamps.
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # Set when process_image flips status to RUNNING. Used by reap_stuck_jobs
    # to detect rows orphaned by a SIGKILLed worker (no chance to write a
    # terminal state). Null until the task is first picked up.
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = "vision"
        db_table = "vision_scoringjob"

    def __str__(self) -> str:  # pragma: no cover — cosmetic
        return f"ScoringJob(id={self.id}, status={self.status})"


class AcceptedResult(models.Model):
    """A user-confirmed, persisted score — sibling to ``ScoringJob`` (not a
    flag on it) so the CV-pipeline lifecycle and the user-accept lifecycle stay
    clean. This is what the dashboard's aggregation queries read from.

    S-03 (FR-010/011/012). One row per accepted detection result:

      - ``source_job`` is the ``ScoringJob.id`` the result was accepted from —
        a plain UUID (no FK per AGENTS.md §5), so the CV row can be reclaimed
        independently if ever needed.
      - ``holes`` is the corrected-hole snapshot (detector output + any pre-
        accept user corrections), stored as JSON.
      - ``score_average`` is the mean of the holes' scores — the "result" for
        hero-stats + chart aggregation.
      - ``unique_together = ("source_job", "user_uuid")`` DB-enforces
        idempotency for the accept path: a second concurrent accept for the
        same job raises ``IntegrityError``, caught in ``accept_job`` and
        converted to the 200 re-fetch path (the canonical insert-or-return-
        existing idiom — safer than a check-then-create sequence, which under
        SQLite's default isolation lets both transactions pass the check).

    Immutable after create (PRD FR-010 Socrates: "editing saved results is v2");
    corrections happen pre-accept on the ``/results/:jobId`` screen and are
    snapshotted here at accept.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_uuid = models.UUIDField(db_index=True)
    source_job = models.UUIDField()

    target_type = models.CharField(max_length=32)
    caliber_hint = models.CharField(max_length=64, null=True, blank=True)
    distance = models.PositiveIntegerField(null=True, blank=True)
    weapon_type = models.CharField(max_length=32, null=True, blank=True)

    holes = models.JSONField()
    score_average = models.FloatField()

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "vision"
        db_table = "vision_acceptedresult"
        unique_together = ("source_job", "user_uuid")

    def __str__(self) -> str:  # pragma: no cover — cosmetic
        return f"AcceptedResult(id={self.id}, source_job={self.source_job})"
