"""S-04 owner routes — RBAC + pagination + search + ban/unban/delete.

Drives the BFF (django-ninja) + identity domain (services) through the Django
test client. Auth is established via ``client.force_login()``; role is derived
from ``OWNER_SUB_ID`` (``make_owner`` / ``make_user``). CSRF is exercised on
the mutating routes (POST ban, POST unban, DELETE) — the invariant the SPA's
``X-CSRFToken`` header relies on.

Mirrors ``test_auth_flow.py``'s ``pytestmark``, ``_login_as``, and the CSRF
helpers (``client.handler.enforce_csrf_checks = True``).
"""

from __future__ import annotations

import pytest

from src.domains.identity.test_utils import make_ban, make_owner, make_user


pytestmark = [pytest.mark.django_db, pytest.mark.dev]


def _login_as(client, user) -> None:
    """Authenticate the test client as ``user`` (populates ``request.user``)."""
    client.force_login(user, backend="django.contrib.auth.backends.ModelBackend")


def _csrf(client) -> str:
    """Seed the ``csrftoken`` cookie (via ``/``) and return it."""
    client.get("/")
    return client.cookies["csrftoken"].value


# ---------------------------------------------------------------------------
# GET /v1/users — RBAC + paginated shape
# ---------------------------------------------------------------------------


def test_get_users_401_anonymous(client) -> None:
    response = client.get("/v1/users")
    assert response.status_code == 401


def test_get_users_403_for_non_owner(client, owner_sub, user_sub) -> None:
    user = make_user(sub=user_sub, nick="bob")
    _login_as(client, user)
    assert client.get("/v1/users").status_code == 403


def test_get_users_200_owner_returns_paginated_shape(client, owner_sub, user_sub) -> None:
    """Owner → 200 with the paginated ``AdminUserListOut`` shape."""
    make_user(sub=user_sub, nick="alice")
    owner = make_owner(owner_sub)
    _login_as(client, owner)

    response = client.get("/v1/users")
    assert response.status_code == 200, response.content
    body = response.json()
    assert set(body.keys()) == {"items", "page", "page_size", "total", "total_pages"}
    assert body["page"] == 1
    assert body["page_size"] == 20
    assert body["total"] == 2  # owner + alice
    assert body["total_pages"] == 1


def test_get_users_pagination(client, owner_sub) -> None:
    """``?page=2&page_size=20`` returns the overflow page with correct totals."""
    owner = make_owner(owner_sub)
    for i in range(25):
        make_user(sub=f"auth0|u{i}", nick=f"seed-{i:02d}")
    _login_as(client, owner)

    response = client.get("/v1/users?q=seed-&page=2&page_size=20")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 25
    assert body["total_pages"] == 2
    # Page 2 has the remaining 5 (after page 1's 20), all nick starts "seed-".
    assert len(body["items"]) == 5
    assert all(item["nick"].startswith("seed-") for item in body["items"])


def test_get_users_search_by_nick_and_sub(client, owner_sub) -> None:
    """``?q=`` filters by nick (CI) and sub."""
    owner = make_owner(owner_sub)
    make_user(sub="auth0|visible-token", nick="alice")
    make_user(sub="auth0|other", nick="bob")
    _login_as(client, owner)

    by_nick = client.get("/v1/users?q=ALI").json()
    assert {i["nick"] for i in by_nick["items"]} == {"alice"}

    by_sub = client.get("/v1/users?q=visible-token").json()
    assert {i["nick"] for i in by_sub["items"]} == {"alice"}


def test_get_users_carries_sub_for_owner_audience(client, owner_sub, user_sub) -> None:
    """The owner list DOES carry ``sub`` (unlike ``/v1/me``'s ``UserOut``)."""
    make_user(sub=user_sub, nick="alice")
    _login_as(client, make_owner(owner_sub))
    body = client.get("/v1/users").json()
    alice = next(i for i in body["items"] if i["nick"] == "alice")
    assert alice["sub"] == user_sub
    assert "ban" in alice
    assert alice["is_owner"] is False


def test_get_users_attaches_ban_status(client, owner_sub, user_sub) -> None:
    """Active + prior ban states attach to the right rows."""
    from datetime import timedelta
    from django.utils import timezone

    active = make_user(sub=user_sub, nick="alice")
    make_ban(user=active, banned_until=timezone.now() + timedelta(days=7), reason="active")
    prior = make_user(sub="auth0|prior", nick="bob")
    make_ban(user=prior, banned_until=timezone.now() - timedelta(days=1))
    _login_as(client, make_owner(owner_sub))

    body = client.get("/v1/users").json()
    by_nick = {i["nick"]: i for i in body["items"]}
    assert by_nick["alice"]["ban"]["is_banned"] is True
    assert by_nick["alice"]["ban"]["reason"] == "active"
    assert by_nick["bob"]["ban"]["is_banned"] is False
    assert by_nick["bob"]["ban"]["has_prior_ban"] is True


# ---------------------------------------------------------------------------
# POST /v1/users/{sub}/ban
# ---------------------------------------------------------------------------


def _ban(client, sub: str, *, duration="1d", reason="banned for spamming the range", csrf=None) -> object:
    return client.post(
        f"/v1/users/{sub}/ban",
        data={"duration": duration, "reason": reason},
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )


def test_ban_200_happy_path(client, owner_sub, user_sub) -> None:
    owner = make_owner(owner_sub)
    target = make_user(sub=user_sub, nick="alice")
    _login_as(client, owner)
    csrf = _csrf(client)

    response = _ban(client, target.sub, csrf=csrf)
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["is_banned"] is True
    assert body["reason"] == "banned for spamming the range"


def test_ban_404_unknown_sub(client, owner_sub) -> None:
    _login_as(client, make_owner(owner_sub))
    csrf = _csrf(client)
    assert _ban(client, "auth0|never", csrf=csrf).status_code == 404


def test_ban_409_on_owner(client, owner_sub) -> None:
    owner = make_owner(owner_sub)
    _login_as(client, owner)
    csrf = _csrf(client)
    assert _ban(client, owner.sub, csrf=csrf).status_code == 409


def test_ban_422_short_reason(client, owner_sub, user_sub) -> None:
    target = make_user(sub=user_sub, nick="alice")
    _login_as(client, make_owner(owner_sub))
    csrf = _csrf(client)
    assert _ban(client, target.sub, reason="no", csrf=csrf).status_code == 422


def test_ban_422_invalid_duration(client, owner_sub, user_sub) -> None:
    target = make_user(sub=user_sub, nick="alice")
    _login_as(client, make_owner(owner_sub))
    csrf = _csrf(client)
    # Invalid duration bypasses the Literal (raw data) → 422.
    assert _ban(client, target.sub, duration="2h", csrf=csrf).status_code == 422


def test_ban_403_without_csrf(client, owner_sub, user_sub) -> None:
    """POST ban without ``X-CSRFTOKEN`` → 403 (auto-CSRF on SessionAuth)."""
    target = make_user(sub=user_sub, nick="alice")
    _login_as(client, make_owner(owner_sub))
    client.handler.enforce_csrf_checks = True
    response = client.post(
        f"/v1/users/{target.sub}/ban",
        data={"duration": "1d", "reason": "no csrf header here"},
        content_type="application/json",
    )
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# POST /v1/users/{sub}/unban
# ---------------------------------------------------------------------------


def _unban(client, sub: str, *, csrf=None) -> object:
    return client.post(
        f"/v1/users/{sub}/unban",
        content_type="application/json",
        HTTP_X_CSRFTOKEN=csrf,
    )


def test_unban_200_after_ban(client, owner_sub, user_sub) -> None:
    from datetime import timedelta
    from django.utils import timezone

    owner = make_owner(owner_sub)
    target = make_user(sub=user_sub, nick="alice")
    make_ban(user=target, banned_until=timezone.now() + timedelta(days=7), reason="active")
    _login_as(client, owner)
    csrf = _csrf(client)

    response = _unban(client, target.sub, csrf=csrf)
    assert response.status_code == 200, response.content
    body = response.json()
    assert body["is_banned"] is False
    assert body["has_prior_ban"] is True
    assert body["lifted_at"] is not None


def test_unban_409_no_active_ban(client, owner_sub, user_sub) -> None:
    target = make_user(sub=user_sub, nick="alice")
    _login_as(client, make_owner(owner_sub))
    csrf = _csrf(client)
    assert _unban(client, target.sub, csrf=csrf).status_code == 409


def test_unban_404_unknown(client, owner_sub) -> None:
    _login_as(client, make_owner(owner_sub))
    csrf = _csrf(client)
    assert _unban(client, "auth0|never", csrf=csrf).status_code == 404


# ---------------------------------------------------------------------------
# DELETE /v1/users/{sub}
# ---------------------------------------------------------------------------


def _delete(client, sub: str, *, csrf=None) -> object:
    return client.delete(f"/v1/users/{sub}", HTTP_X_CSRFTOKEN=csrf)


def test_delete_204_happy_path(client, owner_sub, user_sub) -> None:
    from django.contrib.auth import get_user_model

    owner = make_owner(owner_sub)
    target = make_user(sub=user_sub, nick="alice")
    _login_as(client, owner)
    csrf = _csrf(client)

    response = _delete(client, target.sub, csrf=csrf)
    assert response.status_code == 204, response.content
    assert get_user_model().objects.filter(sub=user_sub).count() == 0


def test_delete_cascades_bans(client, owner_sub, user_sub) -> None:
    from datetime import timedelta
    from django.utils import timezone

    from src.domains.identity.ban import Ban

    owner = make_owner(owner_sub)
    target = make_user(sub=user_sub, nick="alice")
    make_ban(user=target, banned_until=timezone.now() + timedelta(days=7))
    ban_id = Ban.objects.filter(user=target).first().id
    _login_as(client, owner)
    csrf = _csrf(client)

    _delete(client, target.sub, csrf=csrf)
    assert not Ban.objects.filter(id=ban_id).exists()


def test_delete_404_unknown(client, owner_sub) -> None:
    _login_as(client, make_owner(owner_sub))
    csrf = _csrf(client)
    assert _delete(client, "auth0|never", csrf=csrf).status_code == 404


def test_delete_409_on_owner(client, owner_sub) -> None:
    owner = make_owner(owner_sub)
    _login_as(client, owner)
    csrf = _csrf(client)
    assert _delete(client, owner.sub, csrf=csrf).status_code == 409


def test_delete_403_without_csrf(client, owner_sub, user_sub) -> None:
    target = make_user(sub=user_sub, nick="alice")
    _login_as(client, make_owner(owner_sub))
    client.handler.enforce_csrf_checks = True
    assert client.delete(f"/v1/users/{target.sub}").status_code == 403


# ---------------------------------------------------------------------------
# Non-owner cannot mutate (403)
# ---------------------------------------------------------------------------


def test_ban_403_for_non_owner(client, owner_sub, user_sub) -> None:
    user = make_user(sub=user_sub, nick="alice")
    target = make_user(sub="auth0|target", nick="bob")
    _login_as(client, user)
    csrf = _csrf(client)
    assert _ban(client, target.sub, csrf=csrf).status_code == 403


def test_delete_403_for_non_owner(client, owner_sub, user_sub) -> None:
    user = make_user(sub=user_sub, nick="alice")
    target = make_user(sub="auth0|target", nick="bob")
    _login_as(client, user)
    csrf = _csrf(client)
    assert _delete(client, target.sub, csrf=csrf).status_code == 403
