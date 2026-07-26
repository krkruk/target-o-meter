"""Identity domain ORM models.

The swappable ``User`` is the zero-email anchor of the auth vertical: it stores
only Auth0's canonical ``sub`` and a display ``nick``. Role is *derived* from
``OWNER_SUB_ID`` (research §7), never persisted — the env var is the single
source of truth, so hand-editing a row cannot split Owner state from config.

Per AGENTS.md §5 this domain defines pure models; no django-ninja, no HTTP.
``user_uuid`` references into other domains are plain ``UUIDField`` (not FK).
"""

from __future__ import annotations

import os
import uuid

from django.contrib.auth.models import (
    AbstractBaseUser,
    BaseUserManager,
    PermissionsMixin,
)
from django.db import models


def _generated_nick() -> str:
    """F-01 fallback nick before S-01's nick-on-first-login UX lands.

    S-01 will prompt the user; until then, a non-human-readable default keeps
    the CI-uniqueness constraint satisfiable for OAuth-created rows.
    """
    return f"shooter-{uuid.uuid4().hex[:8]}"


class Role(models.TextChoices):
    """Two-tier RBAC (AGENTS.md §2). Owner is *derived* from ``OWNER_SUB_ID``,
    never persisted on the row — so this enum is for DTO/representation only."""

    OWNER = "owner", "Owner"
    USER = "user", "User"


class UserManager(BaseUserManager):
    """Manager Django requires for a custom user.

    ``create_user`` is the OAuth path (Auth0 already proved identity, so the
    password is unusable). ``create_superuser`` is the dev-admin seed path only
    — it sets a *usable* password (diverging from research §7, which left it
    unusable) because the seeded dev admin must log into Django admin, which
    requires a real password.

    Role is NEVER conferred here: Owner is derived from ``OWNER_SUB_ID``, the
    single source of truth.
    """

    use_in_migrations = True

    def create_user(self, sub: str, nick: str = "", **extra) -> User:
        if not sub:
            raise ValueError("Users must have a non-empty sub")
        if not nick:
            nick = _generated_nick()
        user = self.model(sub=sub, nick=nick, **extra)
        user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(
        self, sub: str, nick: str = "", password: str = "", **extra
    ) -> User:
        # ``password`` is a named param (not ``extra.pop``) so a caller that
        # forgets it gets a clear ``TypeError`` naming the missing arg instead
        # of an opaque ``KeyError`` (impl-review F6 — match Django's positional
        # contract for ``BaseUserManager.create_superuser``).
        if not password:
            raise ValueError("Superusers must have a usable password.")
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        if extra.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        user = self.create_user(sub=sub, nick=nick, **extra)
        # ``create_user`` set an unusable password; the dev admin needs a real
        # one to log into Django admin (overrides research §7 sketch).
        user.set_password(password)
        user.save(using=self._db)
        return user


class User(AbstractBaseUser, PermissionsMixin):
    """Swappable identity user keyed by Auth0's canonical ``sub``.

    - ``sub`` is the OIDC subject (opaque, provider-scoped). Unique.
    - ``nick`` is the only user-visible identifier; case-insensitive unique.
    - ``is_staff`` gates Django admin access (dev admin only; OAuth users
      always False). ``is_superuser`` is inherited from ``PermissionsMixin``
      (kept per Q3 — admin ``has_perm``/``has_module_perms`` need it).
    - ``role`` / ``is_owner`` are *derived* from ``OWNER_SUB_ID`` — never
      persisted, so editing a row cannot desync Owner state from config.

    ``PermissionsMixin`` (not bare ``AbstractBaseUser``) is required so the
    registered ``identity_user`` ModelAdmin's ``has_view_permission`` /
    ``has_module_permission`` resolve without ``AttributeError``
    (plan-review F1).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sub = models.CharField(max_length=255, unique=True)
    nick = models.CharField(max_length=64)
    # S-01: explicit "has the user chosen a nick" flag. ``UserManager.create_user``
    # always assigns a ``shooter-<uuid8>`` fallback, so emptiness/string-matching
    # can't detect first login. New OAuth rows are ``False`` → the SPA prompts;
    # ``set_nick`` flips it to ``True``. Schema-only migration (no backfill — see
    # plan §1.1).
    has_set_nick = models.BooleanField(default=False)
    is_staff = models.BooleanField(default=False)
    # ``last_login`` + ``is_superuser`` inherited from the base classes.

    USERNAME_FIELD = "sub"
    REQUIRED_FIELDS: list[str] = []

    objects = UserManager()

    class Meta:
        app_label = "identity"
        db_table = "identity_user"
        constraints = [
            models.UniqueConstraint(
                models.functions.Lower("nick"),
                name="identity_user_nick_ci_unique",
                violation_error_message=(
                    "A user with that nick (case-insensitive) already exists."
                ),
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover — cosmetic
        return f"User(sub={self.sub!r}, nick={self.nick!r})"

    @property
    def role(self) -> str:
        """Derived role: ``OWNER`` iff ``self.sub == OWNER_SUB_ID`` env.

        Fails closed: empty/missing env → never Owner. This is the load-bearing
        decision from research §7 — never persist role, always re-derive, so
        the env var is the single source of truth.
        """
        owner_sub = os.environ.get("OWNER_SUB_ID", "")
        if owner_sub and self.sub == owner_sub:
            return Role.OWNER
        return Role.USER

    @property
    def is_owner(self) -> bool:
        """Thin read of ``self.role == OWNER`` for the BFF's ``require_owner``."""
        return self.role == Role.OWNER
