"""Config-contract system test — automates plan §Phase 1 manual task 1.6.

The plan's manual verification 1.6 is: ```.env.example covers every env var
referenced in code (```grep -rn "os.environ" src/ | sort -u``` matches). This
encodes that grep as an automated, fail-loud regression guard: every literal
env-var key the running app reads from ``os.environ`` / ``os.getenv`` must
appear in ``.env.example`` (as a ``KEY=`` line or a documentation comment), so a
new contributor copying ``.env.example`` to ``.env`` is never missing a var the
app silently requires.

This is a static, blackbox-shaped check: it reads source + the template as
files and never imports app internals. Platform-injected vars (set by Railway
or docker-compose, never by a developer editing ``.env``) live in an explicit
allowlist below so the contract stays honest about what is and isn't a
developer-set var.

Not TDD'able as production code (it has none) — it stands in for the human
"read the docs and confirm coverage" step.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_DIR = _REPO_ROOT / "src"
_ENV_EXAMPLE = _REPO_ROOT / ".env.example"

pytestmark = pytest.mark.dev


# Matches the literal KEY in every common os.environ access shape:
#   os.environ["KEY"], os.environ.get("KEY", ...), os.environ.pop("KEY"),
#   os.getenv("KEY"). Only literal-string keys are captured; dynamic keys
#   (os.environ[some_var]) are intentionally ignored — they can't be checked
#   against a static template.
_ENV_KEY = re.compile(
    r"os\.environ\[\s*['\"]([A-Z_][A-Z0-9_]*)['\"]"
    r"|os\.environ\.get\(\s*['\"]([A-Z_][A-Z0-9_]*)['\"]"
    r"|os\.environ\.pop\(\s*['\"]([A-Z_][A-Z0-9_]*)['\"]"
    r"|os\.getenv\(\s*['\"]([A-Z_][A-Z0-9_]*)['\"]"
)

# Env vars the platform (Railway) or docker-compose injects, NOT the developer.
# They are deliberately absent from .env.example; listing them here keeps the
# coverage assertion honest and forces a conscious decision when one changes.
_PLATFORM_INJECTED = {
    "RAILWAY_VOLUME_MOUNT_PATH",  # Railway Volume mount path; SQLite lives under it.
    "STATIC_ROOT",                # collectstatic output dir; set by compose / CI.
    "TOM_ENV_FILE",               # override pointing settings at a non-default .env file.
}


def _collect_env_keys() -> set[str]:
    """Literal env-var keys read from src/ (excludes tests/fixtures/migrations)."""
    keys: set[str] = set()
    for py in _SRC_DIR.rglob("*.py"):
        text = py.read_text(errors="replace")
        for m in _ENV_KEY.finditer(text):
            for g in m.groups():
                if g:
                    keys.add(g)
    return keys


def test_env_example_covers_every_code_referenced_var() -> None:
    """Every literal env-var key read in src/ appears in .env.example.

    A key is "documented" if its token appears anywhere in .env.example — as a
    ``KEY=value`` line or inside a comment (DEBUG / SECRET_KEY / ALLOWED_HOSTS
    are documented as comments). Platform-injected vars are exempt via
    ``_PLATFORM_INJECTED``.
    """
    env_text = _ENV_EXAMPLE.read_text()
    referenced = _collect_env_keys()

    undocumented = {
        k for k in sorted(referenced)
        if k not in _PLATFORM_INJECTED and k not in env_text
    }
    assert not undocumented, (
        "These env vars are read from os.environ in src/ but missing from "
        f".env.example (add them, or add them to _PLATFORM_INJECTED with a "
        f"reason): {sorted(undocumented)}"
    )


def test_platform_injected_keys_still_exist_in_code() -> None:
    """Guard against a stale allowlist: every _PLATFORM_INJECTED key must still
    be read somewhere in src/, or it should be removed from the allowlist."""
    referenced = _collect_env_keys()
    stale = _PLATFORM_INJECTED - referenced
    assert not stale, (
        f"_PLATFORM_INJECTED lists keys no longer read in src/: {sorted(stale)} "
        "— remove them so the allowlist tracks reality."
    )
