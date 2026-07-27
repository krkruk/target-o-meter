"""System test: Django loads ``.env`` on boot (Phase 5.B).

The motivating bug (first ``DEBUG=false make dev`` smoke): ``oauth.auth0.
authorize_redirect`` crashed on ``Invalid URL 'https:///.well-known/...'``.
Root cause: ``python-dotenv`` was a declared dependency but **nothing on the
Django bootstrap path called ``load_dotenv()``** — only the standalone vision
CLI did. So ``settings.AUTH0_DOMAIN`` read the empty-string default and that
empty host flowed into the OAuth client registration, producing the malformed
discovery-doc URL.

Contract pinned here: a marker env var written into a throwaway ``.env`` in the
subprocess CWD reaches ``os.environ`` inside the Django process. Before the fix
(``load_dotenv()`` at the top of ``settings.py``), the marker is absent and the
test fails; after the fix, ``load_dotenv()`` picks up the file and the marker
is visible.

Also pins a drift guard: every ``KEY=`` declared in ``.env.example`` is read
somewhere under ``src/`` (greppable). This catches the class of bug where
``.env`` ships a var the settings module never reads (``AUTH0_SECRET`` and
``APP_BASE_URL`` were the original offenders).
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest


pytestmark = [pytest.mark.django_db, pytest.mark.dev]


_REPO_ROOT = Path(__file__).resolve().parents[2]
_MANAGE_PY = _REPO_ROOT / "src" / "manage.py"
_ENV_EXAMPLE = _REPO_ROOT / ".env.example"


def test_load_dotenv_runs_on_django_boot(tmp_path: Path) -> None:
    """A marker var in ``.env`` reaches ``os.environ`` inside the Django process.

    Writes a throwaway ``.env`` (carrying a unique marker) and points Django at
    it via ``TOM_ENV_FILE``. ``load_dotenv()`` (called at the top of
    ``settings.py``) loads it; the shell command prints the marker back; we
    assert it matches.

    Before the Phase 5.B fix, ``settings.py`` never called ``load_dotenv()`` —
    the marker stayed absent and this test failed red.
    """
    marker = f"phase5-marker-{uuid.uuid4().hex[:8]}"
    env_file = tmp_path / ".env"
    env_file.write_text(f"TOM_ENV_LOAD_MARKER={marker}\n")

    proc = subprocess.run(
        [
            sys.executable, str(_MANAGE_PY), "shell", "-c",
            "import os; print('MARKER=' + os.environ.get('TOM_ENV_LOAD_MARKER', 'MISSING'))",
        ],
        cwd=str(_REPO_ROOT),
        env={**os.environ, "TOM_ENV_FILE": str(env_file)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, (
        f"manage.py shell failed (rc={proc.returncode}):\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert f"MARKER={marker}" in proc.stdout, (
        f"marker {marker!r} not found in manage.py shell output — ``.env`` was "
        f"not loaded on Django boot.\nstdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )


def test_auth0_domain_from_env_reaches_oauth_client(tmp_path: Path) -> None:
    """The ``AUTH0_DOMAIN`` from ``.env`` flows into the OAuth client's discovery
    URL — the specific crash from the Phase 5.B bug report.

    The original symptom: ``oauth.auth0.authorize_redirect`` crashed on
    ``Invalid URL 'https:///.well-known/openid-configuration': No host
    supplied`` because ``settings.AUTH0_DOMAIN`` was empty (``.env`` not
    loaded). Here we set a bespoke domain in a throwaway ``.env``, then assert
    the registered OAuth client's ``server_metadata_url`` carries that host
    (not the empty-host form).

    The subprocess env deliberately strips any inherited ``AUTH0_DOMAIN`` so the
    only source is the throwaway ``.env`` — isolating the test from the parent
    pytest process's own (loaded) env.
    """
    domain = f"phase5-{uuid.uuid4().hex[:8]}.example.com"
    env_file = tmp_path / ".env"
    env_file.write_text(f"AUTH0_DOMAIN={domain}\n")

    clean_env = {k: v for k, v in os.environ.items() if k != "AUTH0_DOMAIN"}
    clean_env["TOM_ENV_FILE"] = str(env_file)

    proc = subprocess.run(
        [
            sys.executable, str(_MANAGE_PY), "shell", "-c",
            "from src.bff.oauth import oauth; print(oauth.auth0._server_metadata_url)",
        ],
        cwd=str(_REPO_ROOT),
        env=clean_env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, (
        f"manage.py shell failed (rc={proc.returncode}):\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    # The discovery URL must carry the bespoke domain — not the hostless form
    # ``https:///.well-known/...`` from the bug report.
    expected = f"https://{domain}/.well-known/openid-configuration"
    assert expected in proc.stdout, (
        f"expected {expected!r} in output — the AUTH0_DOMAIN from .env did not "
        f"reach the OAuth client registration.\nstdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )


def test_env_example_keys_are_read_somewhere() -> None:
    """Every ``KEY=`` in ``.env.example`` is read somewhere under ``src/``.

    Drift guard: catches the class of bug where ``.env`` ships a var the code
    never reads (``AUTH0_SECRET`` and ``APP_BASE_URL`` were the original
    offenders). The grep is deliberately loose (``KEY`` substring) to allow
    ``os.environ.get("KEY", ...)`` / ``os.environ["KEY"]`` / ``getenv("KEY")``
    alike.
    """
    if not _ENV_EXAMPLE.exists():
        pytest.skip(".env.example absent")

    # Parse KEY= lines (skip blanks + comments). Strip optional inline default.
    declared_keys: set[str] = set()
    for raw in _ENV_EXAMPLE.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if key and key.isidentifier():
            declared_keys.add(key)

    # Read all Python under src/ once.
    src_blob = "\n".join(
        p.read_text(errors="replace")
        for p in (_REPO_ROOT / "src").rglob("*.py")
        if "__pycache__" not in p.parts
    )

    unread = sorted(k for k in declared_keys if k not in src_blob)
    assert not unread, (
        f"keys declared in .env.example but never read under src/: {unread}. "
        f"Either wire them into settings.py or drop them from .env.example."
    )


def test_django_vite_dev_mode_overrides_under_debug_true() -> None:
    """``DJANGO_VITE_DEV_MODE=False`` makes ``settings.DJANGO_VITE['default']
    ['dev_mode']`` False even when ``DEBUG=True``.

    Pins the settings-level wiring for the dev-container posture (S-02
    impl-review F11): ``dev_mode`` was bound directly to ``DEBUG``, so the dev
    container emitted ``http://localhost:5173/...`` with no Vite answering and
    served a blank page. The fix decouples them via this env var. The HTML-level
    regression lives in ``test_spa_pipeline.py::test_dev_container_*``; this is
    the unit-level resolution guard (a subprocess with both envs set, asserting
    the resolved ``dev_mode``).

    Default (unset) behavior is covered by the native-dev tests in
    ``test_spa_pipeline.py`` (``test_index_serves_spa_shell_dev_mode``).
    """
    proc = subprocess.run(
        [
            sys.executable, str(_MANAGE_PY), "shell", "-c",
            "from django.conf import settings; "
            "print('DEVMODE=' + str(settings.DJANGO_VITE['default']['dev_mode'])); "
            "print('DEBUG=' + str(settings.DEBUG))",
        ],
        cwd=str(_REPO_ROOT),
        # DEBUG=True + the override False = the dev-container posture.
        env={**os.environ, "DEBUG": "True", "DJANGO_VITE_DEV_MODE": "False"},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, (
        f"manage.py shell failed (rc={proc.returncode}):\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "DEVMODE=False" in proc.stdout, (
        f"DJANGO_VITE_DEV_MODE=False did not flip dev_mode to False under "
        f"DEBUG=True (the dev-container bug).\nstdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )
    assert "DEBUG=True" in proc.stdout, (
        f"sanity check failed: DEBUG was not True.\nstdout:\n{proc.stdout}"
    )
