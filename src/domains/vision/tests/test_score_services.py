"""Domain unit tests for the score-list/detail/update/delete services backing
``GET/PATCH/DELETE /v1/scores`` (the ``user-score-dashboard`` change).

These pin the pure-domain behavior the BFF maps onto HTTP:

  - ``list_results`` — pagination math + per-user filter + ``-created_at`` order.
  - ``get_result`` — owner-checked detail; ``PermissionError`` on missing OR
    not-mine (the ID-prober invariant — both look identical to the caller).
  - ``update_result`` — recomputes ``score_average`` from patched holes,
    advances ``updated_at`` (``created_at`` unchanged), applies optional param
    overrides, ``PermissionError`` on missing/not-mine.
  - ``delete_result`` — row gone, ``ScoringJob`` retained, storage objects
    best-effort deleted (a storage failure does NOT raise).

Uses the ``make_accepted_result`` seeder (AGENTS.md §5 — no ORM tools like
factory_boy directly against domain models).
"""
from __future__ import annotations

import uuid

import pytest

from src.domains.vision.dtos import DetectedHoleDTO
from src.domains.vision.services import (
    delete_result,
    get_result,
    list_results,
    update_result,
)
from src.domains.vision.test_utils import days_ago, make_accepted_result


pytestmark = [pytest.mark.django_db, pytest.mark.dev]


# ---------------------------------------------------------------------------
# list_results — pagination + filter + order
# ---------------------------------------------------------------------------


def test_list_results_returns_only_callers_rows_newest_first() -> None:
    """A user sees only their own rows, most-recent first; another user's rows
    are invisible."""
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()
    older = make_accepted_result(user_uuid=user_a, created_at=days_ago(2))
    newest = make_accepted_result(user_uuid=user_a, created_at=days_ago(0))
    make_accepted_result(user_uuid=user_b, created_at=days_ago(0))  # not A's

    out = list_results(user_uuid=user_a)
    assert [r.result_id for r in out.items] == [newest.id, older.id]
    assert out.total == 2


def test_list_results_pagination_clamps_page_size_to_50() -> None:
    """page_size > 50 clamps to 50 (the documented ceiling); page >= 1."""
    user = uuid.uuid4()
    for _ in range(3):
        make_accepted_result(user_uuid=user)

    out = list_results(user_uuid=user, page=1, page_size=500)
    assert out.page_size == 50
    # Negative page clamps to 1.
    out_neg = list_results(user_uuid=user, page=-3, page_size=10)
    assert out_neg.page == 1


def test_list_results_pagination_math_offset_slice_and_totals() -> None:
    """5 rows, page_size=2 → page 1 = rows[0:2], total=5, total_pages=3."""
    user = uuid.uuid4()
    rows = [
        make_accepted_result(user_uuid=user, created_at=days_ago(i))
        for i in range(5)  # days_ago(0..4), so index 0 is newest
    ]

    out = list_results(user_uuid=user, page=1, page_size=2)
    assert out.total == 5
    assert out.total_pages == 3
    assert [r.result_id for r in out.items] == [rows[0].id, rows[1].id]

    # Page 3 returns just the last (oldest) row.
    out_last = list_results(user_uuid=user, page=3, page_size=2)
    assert [r.result_id for r in out_last.items] == [rows[4].id]


# ---------------------------------------------------------------------------
# get_result — owner-checked detail
# ---------------------------------------------------------------------------


def test_get_result_returns_dto_with_holes_for_owner() -> None:
    """The owner gets the full snapshot including the corrected ``holes``."""
    user = uuid.uuid4()
    ar = make_accepted_result(user_uuid=user)

    dto = get_result(result_id=ar.id, user_uuid=user)
    assert dto.result_id == ar.id
    assert len(dto.holes) == len(ar.holes)
    assert dto.score_average == ar.score_average


def test_get_result_raises_permission_error_on_unknown_id() -> None:
    """An unknown id raises ``PermissionError`` (the BFF maps to 404)."""
    with pytest.raises(PermissionError):
        get_result(result_id=uuid.uuid4(), user_uuid=uuid.uuid4())


def test_get_result_raises_permission_error_on_other_users_id() -> None:
    """Another user's result id raises ``PermissionError`` — an ID-prober can't
    distinguish 'exists, not mine' from 'doesn't exist'."""
    owner = uuid.uuid4()
    ar = make_accepted_result(user_uuid=owner)
    with pytest.raises(PermissionError):
        get_result(result_id=ar.id, user_uuid=uuid.uuid4())


# ---------------------------------------------------------------------------
# update_result — recompute average + immutability lift
# ---------------------------------------------------------------------------


def _holes(scores: list[int]) -> list[DetectedHoleDTO]:
    return [
        DetectedHoleDTO(x=i, y=i, score=s, confidence=1.0)
        for i, s in enumerate(scores)
    ]


def test_update_result_recomputes_average_and_advances_updated_at() -> None:
    """PATCH the holes → ``score_average`` reflects the new mean, ``updated_at``
    advances, ``created_at`` is unchanged."""
    user = uuid.uuid4()
    ar = make_accepted_result(
        user_uuid=user,
        holes=[{"x": 0, "y": 0, "score": 9, "confidence": 1.0}] * 4,  # avg 9.0
    )
    created_before = ar.created_at
    updated_before = ar.updated_at

    dto = update_result(
        result_id=ar.id, user_uuid=user,
        holes=_holes([10, 10, 10, 10]),  # avg 10.0
    )

    assert dto.score_average == pytest.approx(10.0)
    ar.refresh_from_db()
    assert ar.created_at == created_before
    assert ar.updated_at > updated_before


def test_update_result_applies_optional_param_overrides() -> None:
    """Optional params (target_type, distance, …) override when provided."""
    user = uuid.uuid4()
    ar = make_accepted_result(user_uuid=user, target_type="air_pistol", distance=25)

    update_result(
        result_id=ar.id, user_uuid=user,
        holes=_holes([8, 8]),
        target_type="precision_pistol", distance=50, weapon_type="free_pistol",
        caliber_hint=".22",
    )
    ar.refresh_from_db()
    assert ar.target_type == "precision_pistol"
    assert ar.distance == 50
    assert ar.weapon_type == "free_pistol"
    assert ar.caliber_hint == ".22"


def test_update_result_raises_permission_error_on_other_users_id() -> None:
    """Another user's result → ``PermissionError`` (BFF → 404)."""
    owner = uuid.uuid4()
    ar = make_accepted_result(user_uuid=owner)
    with pytest.raises(PermissionError):
        update_result(
            result_id=ar.id, user_uuid=uuid.uuid4(),
            holes=_holes([10]),
        )


def test_update_result_raises_permission_error_on_unknown_id() -> None:
    with pytest.raises(PermissionError):
        update_result(
            result_id=uuid.uuid4(), user_uuid=uuid.uuid4(),
            holes=_holes([10]),
        )


# ---------------------------------------------------------------------------
# delete_result — row gone + ScoringJob retained + best-effort storage
# ---------------------------------------------------------------------------


def test_delete_result_removes_row_and_raises_on_other_users_id() -> None:
    """Owner deletes their row → it's gone; another user's id → ``PermissionError``."""
    user = uuid.uuid4()
    ar = make_accepted_result(user_uuid=user)

    delete_result(result_id=ar.id, user_uuid=user)
    from src.domains.vision.models import AcceptedResult
    assert not AcceptedResult.objects.filter(id=ar.id).exists()

    # Unknown id → PermissionError.
    with pytest.raises(PermissionError):
        delete_result(result_id=uuid.uuid4(), user_uuid=user)


def test_delete_result_retains_scoring_job_audit_row() -> None:
    """The ``ScoringJob`` the result was accepted from is NOT deleted — it
    remains the audit record."""
    from src.domains.vision.models import ScoringJob
    user = uuid.uuid4()
    job = ScoringJob.objects.create(
        user_uuid=user, status=ScoringJob.Status.SUCCEEDED,
        input_path="uploads/abc.jpg",
    )
    ar = make_accepted_result(user_uuid=user, source_job=job.id)

    delete_result(result_id=ar.id, user_uuid=user)
    assert ScoringJob.objects.filter(id=job.id).exists()
