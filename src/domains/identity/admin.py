"""Django admin registration for the identity ``User`` (F-01 Phase 4.2).

A read-mostly admin over ``identity_user`` so the seeded dev admin can inspect
and seed rows in a GUI. ``is_staff`` is settable (so another local admin can
be promoted); ``role`` / ``is_owner`` are read-only (derived from
``OWNER_SUB_ID`` — never editable, by design).

No password-change flow is exposed: OAuth users have unusable passwords, and
the dev admin's password is set via the Docker dev-seed path (Phase 7, deferred)
or ``manage.py shell`` — not via the admin.
"""
from __future__ import annotations

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from src.domains.identity.models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    """Minimal read-mostly admin for ``identity_user``.

    Extends Django's ``UserAdmin`` so ``has_view_permission`` /
    ``has_module_permission`` (which call ``has_perm``/``has_module_perms``
    from ``PermissionsMixin``) resolve correctly — plan-review F1.
    """

    # The identity User has no username/password fields, so strip Django's
    # default UserAdmin fieldsets (which assume them).
    list_display = ("sub", "nick", "is_staff", "last_login")
    search_fields = ("sub", "nick")
    list_filter = ("is_staff",)
    readonly_fields = ("role", "is_owner", "sub", "last_login")

    # No password management in admin — OAuth users have unusable passwords;
    # the dev admin password is set out-of-band (seed/shell).
    fieldsets = (
        (None, {"fields": ("sub", "nick")}),
        ("Role (derived from OWNER_SUB_ID)", {
            "fields": ("role", "is_owner"),
            "description": "Role is derived from the OWNER_SUB_ID env var and "
                           "cannot be edited here.",
        }),
        ("Permissions", {"fields": ("is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Important dates", {"fields": ("last_login",)}),
    )
    add_fieldsets = (
        (None, {"fields": ("sub", "nick")}),
    )

    ordering = ("sub",)
