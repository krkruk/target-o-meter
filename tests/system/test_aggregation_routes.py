"""System test: ``GET /v1/scores/aggregations`` (S-03 Phase 5).

Pins the dashboard's single aggregation endpoint — resource-named per the
API-design lesson (plural noun ``/scores/aggregations``, NOT ``/scoring/
aggregate``). Reads ``AcceptedResult`` rows (seeded via ``make_accepted_result``)
and computes hero stats + a recent list + a daily-average chart.

Derived session = calendar day (the user's decision). Last session = the max
``created_at__date`` with >=1 accepted result for the user.

Driven through the Django test client (``client.force_login``), mirroring the
existing scoring-routes system tests.
"""
from __future__ import annotations


import pytest

from src.domains.identity.test_utils import make_user
from src.domains.vision.test_utils import days_ago, make_accepted_result


pytestmark = [pytest.mark.django_db, pytest.mark.dev]


def _login_as(client, user) -> None:
    client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")


def test_get_aggregations_returns_401_anonymous(client) -> None:
    """No session → session_auth falsy → 401 before the body runs."""
    response = client.get("/v1/scores/aggregations")
    assert response.status_code == 401


def test_get_aggregations_empty_user(client, user_sub) -> None:
    """A user with no accepted results → 200, hero stats all None/0, empty lists."""
    user = make_user(sub=user_sub, nick="alba")
    _login_as(client, user)

    response = client.get("/v1/scores/aggregations")
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["hero"]["total_shots"] == 0
    assert body["hero"]["last_session_average"] is None
    assert body["hero"]["best_result"] is None
    assert body["recent"] == []
    assert body["daily_averages"] == []


def test_get_aggregations_multi_day(client, user_sub) -> None:
    """A user with 3 accepted results on 3 different days → 200, hero stats
    computed: total_shots = sum of hole counts, best_result = max score_average,
    last_session_average = the most-recent day's mean. recent has 3 entries,
    daily_averages has 3 entries."""
    user = make_user(sub=user_sub, nick="bran")
    _login_as(client, user)

    make_accepted_result(
        user_uuid=user.id, created_at=days_ago(2),
        holes=[{"x": 1, "y": 1, "score": 8, "confidence": 0.9}] * 5,
    )  # avg 8.0, 5 holes, day -2
    make_accepted_result(
        user_uuid=user.id, created_at=days_ago(1),
        holes=[{"x": 1, "y": 1, "score": 10, "confidence": 0.9}] * 10,
    )  # avg 10.0, 10 holes, day -1 (the most recent day)
    make_accepted_result(
        user_uuid=user.id, created_at=days_ago(0),
        holes=[{"x": 1, "y": 1, "score": 6, "confidence": 0.9}] * 4,
    )  # avg 6.0, 4 holes, day 0 (today)

    response = client.get("/v1/scores/aggregations")
    assert response.status_code == 200, response.content
    body = response.json()
    hero = body["hero"]
    # total_shots = sum of hole counts = 5 + 10 + 4 = 19.
    assert hero["total_shots"] == 19
    # best_result = max score_average across the user's results = 10.0.
    assert hero["best_result"] == pytest.approx(10.0)
    # last_session_average = the most-recent calendar day's mean. Today has one
    # result averaging 6.0 → 6.0.
    assert hero["last_session_average"] == pytest.approx(6.0)
    # recent holds the 3 results.
    assert len(body["recent"]) == 3
    # daily_averages holds the 3 days (zero days omitted).
    assert len(body["daily_averages"]) == 3
    # Each recent entry carries the per-hole count + score_average + target_type.
    for r in body["recent"]:
        assert {"result_id", "source_job", "created_at", "score_average", "hole_count", "target_type"} <= set(r)


def test_get_aggregations_same_day_derived_session(client, user_sub) -> None:
    """Two accepted results on the SAME day → last_session_average = the mean
    across both (derived session = calendar day)."""
    user = make_user(sub=user_sub, nick="cary")
    _login_as(client, user)
    make_accepted_result(
        user_uuid=user.id, created_at=days_ago(0),
        holes=[{"x": 1, "y": 1, "score": 8, "confidence": 0.9}] * 5,
        score_average=8.0,
    )
    make_accepted_result(
        user_uuid=user.id, created_at=days_ago(0),
        holes=[{"x": 1, "y": 1, "score": 10, "confidence": 0.9}] * 5,
        score_average=10.0,
    )

    response = client.get("/v1/scores/aggregations")
    assert response.status_code == 200
    # mean(8.0, 10.0) = 9.0 — the two same-day results form one session.
    assert response.json()["hero"]["last_session_average"] == pytest.approx(9.0)


def test_get_aggregations_isolates_users(client, user_sub) -> None:
    """User A's results do not appear in user B's aggregation (cross-user
    isolation by user_uuid)."""
    user_a = make_user(sub=user_sub, nick="dane")
    user_b = make_user(sub="auth0|other-aggregation-user", nick="emma")
    _login_as(client, user_a)
    make_accepted_result(
        user_uuid=user_a.id, created_at=days_ago(0),
        holes=[{"x": 1, "y": 1, "score": 9, "confidence": 0.9}] * 10,
    )

    # Switch to user B — sees nothing of A's data.
    client.logout()
    _login_as(client, user_b)
    response = client.get("/v1/scores/aggregations")
    assert response.status_code == 200
    body = response.json()
    assert body["hero"]["total_shots"] == 0
    assert body["hero"]["best_result"] is None
    assert body["recent"] == []
