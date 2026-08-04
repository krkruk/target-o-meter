"""Django admin registration for the vision domain (S-03 impl-review F1).

Read-mostly admins over ``ScoringJob`` + ``AcceptedResult`` so the seeded dev
admin can inspect pipeline state + accepted results in a GUI. Mirrors the
identity ``UserAdmin`` posture: a read-mostly admin must actually be read-
mostly.

Why read-mostly:
  - ``ScoringJob`` is the CV-pipeline record, driven by the q2 task body. The
    only legitimate writes are ``schedule_image_processing`` (insert) and
    ``process_image`` (status + result updates). Editing it in the GUI would
    desync ``status`` from the actual pipeline state (e.g. flipping a ``queued``
    row to ``succeeded`` without running detection leaves dangling paths). So
    everything is read-only except in ``manage.py shell``.
  - ``AcceptedResult`` is mutable after create via ``PATCH /v1/scores/{id}``
    (PRD FR-010 Socrates amendment for the ``user-score-dashboard`` change).
    The whole row stays read-only in the GUI — the only legitimate writes are
    ``accept_job`` (insert) and ``update_result`` / ``delete_result`` (the
    dashboard PATCH/DELETE path); editing it by hand would bypass the average
    recompute. ``updated_at`` is the audit trail for the PATCH path.

Both models are still registered (not just left unregistered): the dashboard
aggregates from ``AcceptedResult``, so being able to eyeball rows in the admin
is the manual-verification gate the plan's Progress 2.7 calls out.
"""
from __future__ import annotations

from django.contrib import admin

from src.domains.vision.models import AcceptedResult, ScoringJob


@admin.register(ScoringJob)
class ScoringJobAdmin(admin.ModelAdmin):
    """Read-mostly admin over ``vision_scoringjob``.

    Lists the columns a debugging operator scans for (status, detector inputs,
    timing) without dumping the large ``result`` JSON into ``list_display``
    (that bloats the changelist; it stays in the detail view). The path fields
    are storage keys — read-only so a fat-finger edit can't point a row at an
    object the pipeline never wrote.
    """

    list_display = (
        "id", "status", "target_type", "caliber_hint", "distance",
        "weapon_type", "created_at", "updated_at", "started_at", "completed_at",
    )
    list_filter = ("status", "target_type")
    search_fields = ("id", "caliber_hint", "weapon_type")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)

    # Everything is read-only — edits would desync status from the pipeline.
    readonly_fields = (
        "id", "user_uuid", "status", "input_path", "target_type",
        "caliber_hint", "distance", "weapon_type", "result",
        "llm_input_path", "marked_image_path", "result_json_path", "error",
        "created_at", "updated_at", "started_at", "completed_at",
    )


@admin.register(AcceptedResult)
class AcceptedResultAdmin(admin.ModelAdmin):
    """Read-mostly admin over ``vision_acceptedresult``.

    Mutable after create via ``PATCH /v1/scores/{id}`` (PRD FR-010 Socrates
    amendment for the ``user-score-dashboard`` change), but still read-only in
    the GUI: the only legitimate writes are ``accept_job`` (insert) and
    ``update_result`` / ``delete_result`` (the dashboard PATCH/DELETE path), and
    hand-editing would bypass the average recompute. The ``holes`` JSONField is
    the corrected snapshot; the ``recent`` list + ``total_shots`` on the
    dashboard aggregate from this row, so being able to eyeball it is the
    manual-verification gate (Progress 2.7). ``updated_at`` is exposed so the
    audit trail for the PATCH path is visible alongside ``created_at``.
    """

    list_display = (
        "id", "source_job", "user_uuid", "target_type", "score_average",
        "distance", "weapon_type", "created_at", "updated_at",
    )
    list_filter = ("target_type",)
    search_fields = ("id", "source_job", "user_uuid", "caliber_hint", "weapon_type")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)

    # No field is editable in the GUI (writes go through accept_job /
    # update_result / delete_result, never a hand-edit — a hand-edit would
    # bypass the average recompute + storage bookkeeping).
    readonly_fields = (
        "id", "user_uuid", "source_job", "target_type", "caliber_hint",
        "distance", "weapon_type", "holes", "score_average",
        "created_at", "updated_at",
    )
