"""Authlib OAuth registry — registers the Auth0 OIDC client with PKCE.

HTTP-adjacent → lives in the BFF per AGENTS.md §5 ("ONLY ``src/bff/`` is
permitted to import django-ninja or handle HTTP"). The identity domain stays
pure Python.

Scope is ``openid profile`` with NO ``email`` — defense-in-depth enforcement
of Zero Email Storage (AGENTS.md §2). PKCE (``code_challenge_method="S256"``)
is added beyond the tutorial quickstart.
"""
from __future__ import annotations

from authlib.integrations.django_client import OAuth
from django.conf import settings

oauth = OAuth()

oauth.register(
    "auth0",
    client_id=settings.AUTH0_CLIENT_ID,
    client_secret=settings.AUTH0_CLIENT_SECRET,
    client_kwargs={
        # NO email scope (Zero Email Storage — research §3).
        "scope": "openid profile",
        # PKCE — defense-in-depth even on a confidential client.
        "code_challenge_method": "S256",
    },
    server_metadata_url=(
        f"https://{settings.AUTH0_DOMAIN}/.well-known/openid-configuration"
    ),
)
