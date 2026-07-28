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


# ---------------------------------------------------------------------------
# S-03 Phase 4: byte-oriented surface (read_upload_bytes / write_deliverable_
# bytes) that works under BOTH FS and S3. process_image switches to this surface
# (tempfile dance for cv2.imread) so the S3 path no longer raises
# NotImplementedError.
# ---------------------------------------------------------------------------


def test_read_write_bytes_round_trip_under_fs(tmp_path) -> None:
    """Under USE_S3=False (FS), the byte-oriented surface round-trips:
    ``write_deliverable_bytes`` stores bytes and returns the stored key; reading
    that key back via the underlying storage yields the same bytes."""
    from src.domains.vision.pipeline.storage import ScoringStorage

    storage = ScoringStorage(location=tmp_path / "bucket")
    job_id = "11111111-1111-1111-1111-111111111111"
    key = storage.write_deliverable_bytes(job_id, "12_marked.png", b"PNG-BYTES")
    assert key == f"jobs/{job_id}/12_marked.png"
    assert storage.read_upload_bytes(key) == b"PNG-BYTES"
    # And an uploaded file reads back byte-identical via read_upload_bytes.
    rel = storage.save_upload(b"upload-bytes", "u.jpg")
    assert storage.read_upload_bytes(rel) == b"upload-bytes"


class _FakeS3Storage:
    """In-memory stand-in for django-storages' S3 backend.

    Records the (name, data) writes and serves them back from ``.open(name).read()``
    — enough to prove the S3 branch of the byte-oriented surface uses the backend's
    streaming API instead of the filesystem path ops (which raise under S3).
    """

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def save(self, name: str, content) -> str:
        self.files[name] = content.read()
        return name

    def open(self, name: str, mode: str = "rb"):
        import io
        return io.BytesIO(self.files[name])

    def url(self, name: str) -> str:
        return f"https://fake-s3.example/{name}"


def test_read_write_bytes_under_s3_uses_backend_not_path_ops(monkeypatch) -> None:
    """Under USE_S3=True, ``write_deliverable_bytes`` stores via
    ``self._storage.save(...)`` and ``read_upload_bytes`` reads via
    ``self._storage.open(...).read()`` — NOT the path-shaped methods, which
    raise ``NotImplementedError`` under S3. The old FS-only path must stay
    unreachable on the S3 branch."""
    from src.domains.vision.pipeline.storage import ScoringStorage

    storage = ScoringStorage.__new__(ScoringStorage)  # bypass __init__ env read
    storage._storage = _FakeS3Storage()
    storage._is_s3 = True
    storage._root = None

    job_id = "22222222-2222-2222-2222-222222222222"
    key = storage.write_deliverable_bytes(job_id, "x_marked.png", b"S3-MARKED")
    assert key == f"jobs/{job_id}/x_marked.png"
    # The bytes landed in the fake backend under the constructed key.
    assert storage._storage.files[key] == b"S3-MARKED"
    # Reading the upload back goes through .open(...).read(), not _safe_join.
    storage._storage.files["uploads/abc.jpg"] = b"UPLOAD"
    assert storage.read_upload_bytes("uploads/abc.jpg") == b"UPLOAD"

    # The FS path-shaped methods still raise under S3 (CLI-only surface).
    with pytest.raises(NotImplementedError):
        storage.absolute_path("uploads/abc.jpg")
    with pytest.raises(NotImplementedError):
        storage.read_upload("uploads/abc.jpg")


def test_read_deliverable_bytes_round_trip_under_fs(tmp_path) -> None:
    """``read_deliverable_bytes`` (the deliverable sibling of
    ``read_upload_bytes``) returns the bytes ``write_deliverable_bytes`` stored,
    under FS. The BFF's marked-image proxy route reads a job's marked-image
    artifact through this method, so it must round-trip under both backends."""
    from src.domains.vision.pipeline.storage import ScoringStorage

    storage = ScoringStorage(location=tmp_path / "bucket")
    job_id = "44444444-4444-4444-4444-444444444444"
    key = storage.write_deliverable_bytes(job_id, "44_marked.png", b"MARKED-PNG")
    assert storage.read_deliverable_bytes(key) == b"MARKED-PNG"


def test_read_deliverable_bytes_under_s3_uses_backend_not_path_ops(monkeypatch) -> None:
    """Under USE_S3=True, ``read_deliverable_bytes`` reads via
    ``self._storage.open(...).read()`` (NOT the path-shaped ``_safe_join``,
    which raises ``NotImplementedError`` under S3). Mirrors the upload-bytes
    S3 test — proves the deliverable read is backend-agnostic too."""
    from src.domains.vision.pipeline.storage import ScoringStorage

    storage = ScoringStorage.__new__(ScoringStorage)  # bypass __init__ env read
    storage._storage = _FakeS3Storage()
    storage._is_s3 = True
    storage._root = None

    key = "jobs/55555555-5555-5555-5555-555555555555/55_marked.png"
    storage._storage.files[key] = b"S3-DELIVERABLE"
    assert storage.read_deliverable_bytes(key) == b"S3-DELIVERABLE"

    # The FS path-shaped method still raises under S3 (the read must NOT
    # route through it).
    with pytest.raises(NotImplementedError):
        storage.read_upload(key)


def test_old_path_methods_still_work_under_fs(tmp_path) -> None:
    """The FS-only path methods (absolute_path / read_upload / write_deliverable
    / deliverable_dir) are kept for the CLI + existing FS tests — they still
    work under USE_S3=False (only raise under S3)."""
    from src.domains.vision.pipeline.storage import ScoringStorage

    storage = ScoringStorage(location=tmp_path / "bucket")
    job_id = "33333333-3333-3333-3333-333333333333"
    rel = storage.write_deliverable(job_id, "y_result.json", b"{}")
    assert storage.read_upload(rel) == b"{}"
    assert storage.absolute_path(rel).exists()
