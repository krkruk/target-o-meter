"""Storage swap unit tests (Phase 2): env-driven ``STORAGES`` + ``ScoringStorage``.

Pins the ``USE_S3`` swap so a future change can't silently regress it:

  - ``USE_S3=False`` (default) → ``STORAGES['default']`` is FileSystemStorage and
    ``ScoringStorage()`` round-trips bytes to disk.
  - ``USE_S3=True`` + MinIO vars → ``STORAGES['default']`` is django-storages'
    ``S3Storage`` (no real S3 call is made — we only assert the backend string).
  - ``USE_S3=True`` without ``AWS_ACCESS_KEY_ID`` → settings import raises a
    loud ``KeyError`` (``os.environ[...]`` indexing), never a silent misconfig.

``importlib.reload(settings_module)`` exercises both boot branches against a
fresh module read; an autouse fixture reloads back to the ``USE_S3=False``
default in teardown so the rest of the session is not polluted.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest


pytestmark = pytest.mark.django_db


from src.target_o_meter import settings as settings_module  # noqa: E402


_AWS_KEYS = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_STORAGE_BUCKET_NAME",
    "AWS_S3_ENDPOINT_URL",
    "AWS_S3_ADDRESSING_STYLE",
)


def _reload_settings() -> None:
    importlib.reload(settings_module)


@pytest.fixture(autouse=True)
def _restore_default_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reload settings back to the ``USE_S3=False`` default after each test so a
    ``USE_S3=True`` reload can't leak into the rest of the session."""
    yield
    monkeypatch.delenv("USE_S3", raising=False)
    for key in _AWS_KEYS:
        monkeypatch.delenv(key, raising=False)
    _reload_settings()


def test_default_is_filesystem(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """USE_S3=False (default): default backend is FileSystemStorage and a
    location-scoped ScoringStorage round-trips bytes to disk."""
    monkeypatch.delenv("USE_S3", raising=False)
    _reload_settings()
    assert settings_module.STORAGES["default"]["BACKEND"].endswith("FileSystemStorage")

    from src.domains.vision.pipeline.storage import ScoringStorage

    storage = ScoringStorage(location=tmp_path / "bucket")
    rel = storage.save_upload(b"swap-bytes", "photo.jpg")
    assert (tmp_path / "bucket" / rel).read_bytes() == b"swap-bytes"


def test_default_no_location_writes_under_media_root_scoring(tmp_path) -> None:
    """USE_S3=False with no explicit location: ScoringStorage() falls back to
    MEDIA_ROOT/scoring and round-trips there (manual task 2.5 — the host-dev
    path writes to a 'scoring' subdir, not the repo root). Covers the default
    branch the explicit-location tests above don't reach."""
    from django.test import override_settings

    from src.domains.vision.pipeline.storage import ScoringStorage

    with override_settings(MEDIA_ROOT=str(tmp_path)):
        storage = ScoringStorage()  # no location → the env-driven default branch
        assert storage._is_s3 is False
        assert Path(storage._storage.location).name == "scoring"
        rel = storage.save_upload(b"no-location-bytes", "n.jpg")
        assert (tmp_path / "scoring" / rel).read_bytes() == b"no-location-bytes"


def test_use_s3_selects_s3_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """USE_S3=True + MinIO vars: the default backend is django-storages S3 and
    the AWS_* settings are read from the environment."""
    monkeypatch.setenv("USE_S3", "True")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "minioadmin")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "minioadmin")
    monkeypatch.setenv("AWS_STORAGE_BUCKET_NAME", "target-o-meter-local")
    monkeypatch.setenv("AWS_S3_ENDPOINT_URL", "http://localhost:9000")
    _reload_settings()

    assert settings_module.USE_S3 is True
    assert (
        settings_module.STORAGES["default"]["BACKEND"]
        == "storages.backends.s3.S3Storage"
    )
    assert settings_module.AWS_STORAGE_BUCKET_NAME == "target-o-meter-local"
    assert settings_module.AWS_S3_ENDPOINT_URL == "http://localhost:9000"


def test_use_s3_missing_var_raises_keyerror(monkeypatch: pytest.MonkeyPatch) -> None:
    """USE_S3=True without AWS_ACCESS_KEY_ID: settings import raises KeyError
    (loud at boot), not a silent fallback to empty creds."""
    monkeypatch.setenv("USE_S3", "True")
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    with pytest.raises(KeyError, match="AWS_ACCESS_KEY_ID"):
        _reload_settings()
