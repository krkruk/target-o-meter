"""Storage adapter wrapping Django's ``FileSystemStorage``.

Per AGENTS.md §1: hashed-path bucketing for OpenCV binaries; DB stores
metadata only. The pipeline reads inputs and writes the 3 deliverables through
this adapter in production; the CLI path bypasses it (writes to a local
``--out`` dir directly via ``PipelineRunner``).
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import UUID

from django.core.files.storage import FileSystemStorage


class ScoringStorage:
    """Production-side storage for uploaded images + the 3 deliverables.

    Wraps Django's ``FileSystemStorage`` so the q2 task body can read inputs
    and write outputs through a path-like interface. The CLI bypasses this and
    passes an ``out_dir`` Path to ``PipelineRunner`` directly.
    """

    def __init__(self, location: str | Path | None = None, base_url: str | None = None) -> None:
        # Three construction modes (S-02 makes the default branch env-driven):
        #   - explicit ``location``: tests / CLI always want a concrete on-disk
        #     bucket → FileSystemStorage(location=...) directly.
        #   - no location + USE_S3: settings already wired STORAGES['default'] to
        #     the S3 backend → reuse Django's configured ``default_storage``
        #     (don't hand-build an S3Storage; settings owns the creds).
        #   - no location + USE_S3 off: host-dev default → FileSystemStorage
        #     under MEDIA_ROOT/scoring (or BASE_DIR/scoring_storage).
        from django.conf import settings

        if location is not None:
            self._storage = FileSystemStorage(location=str(location), base_url=base_url)
            self._is_s3 = False
        elif getattr(settings, "USE_S3", False):
            from django.core.files.storage import default_storage
            self._storage = default_storage
            self._is_s3 = True
        else:
            media_root = getattr(settings, "MEDIA_ROOT", None)
            loc = (
                str(Path(media_root) / "scoring") if media_root
                else str(Path(settings.BASE_DIR) / "scoring_storage")
            )
            self._storage = FileSystemStorage(location=loc, base_url=base_url)
            self._is_s3 = False

        if self._is_s3:
            # The path-shaped methods (absolute_path / write_deliverable /
            # read_upload / _safe_join) assume a local filesystem root — they
            # break under S3. S-02's MockDetector short-circuits before any of
            # them run, so guard them loud rather than silently wrong. The
            # OpenCV-needs-local-bytes refactor lands in S-03 alongside the real
            # detector.
            self._root = None
        else:
            # Cache the resolved root once so containment checks see a stable
            # canonical path even if a caller passes a stored_path containing
            # ``..`` segments or symlinks pointing outside the bucket.
            self._root = Path(self._storage.location).resolve()

    def _safe_join(self, stored_path: str) -> Path:
        """Join ``stored_path`` onto the storage root, refusing to escape it.

        Defense-in-depth against path traversal: today every ``stored_path``
        originates from ``save_upload`` (hex-controlled digest + final
        extension), so traversal is unreachable. The moment a future caller
        (e.g. the BFF) passes anything user-controlled through this surface,
        ``../../etc/passwd`` would be in scope without this check.

        S3 has no filesystem root — the path-shaped methods are deferred to
        S-03 (see ``__init__``). Calling one under ``USE_S3`` raises now so a
        premature call is loud, not silently wrong.
        """
        if self._is_s3:
            raise NotImplementedError("S3 path ops land in S-03")
        resolved = (self._root / stored_path).resolve()
        try:
            resolved.relative_to(self._root)
        except ValueError as exc:
            raise ValueError(
                f"stored_path {stored_path!r} escapes the storage root"
            ) from exc
        return resolved

    def _safe_key(self, stored_path: str) -> str:
        """S3-side counterpart to ``_safe_join``: reject keys whose shape could
        escape the storage namespace.

        S3 has no filesystem root, so there is no ``resolve()``-equivalent —
        but the same traversal vectors exist: an absolute key (leading ``/``),
        a ``..`` segment, or a prefix outside the buckets this adapter owns
        (``uploads/`` for saved uploads, ``jobs/`` for deliverables). Today
        every ``stored_path`` is server-composed (``save_upload``'s hex digest,
        ``write_deliverable_bytes``'s ``jobs/{job_id}/{name}``), so this never
        fires — it is the same defense-in-depth posture as ``_safe_join``: the
        moment a future caller passes anything user-controlled through this
        surface, the guard makes the violation loud instead of silent.

        Returns the validated key unchanged (the caller hands it to
        ``self._storage.open/save``).
        """
        if stored_path.startswith("/"):
            raise ValueError(
                f"stored_path {stored_path!r} is absolute — S3 keys are relative"
            )
        parts = stored_path.split("/")
        if any(part == ".." for part in parts):
            raise ValueError(
                f"stored_path {stored_path!r} contains a '..' segment"
            )
        if not (stored_path.startswith("uploads/") or stored_path.startswith("jobs/")):
            raise ValueError(
                f"stored_path {stored_path!r} is outside the uploads/|jobs/ namespaces"
            )
        return stored_path

    def save_upload(self, upload_bytes: bytes, original_name: str) -> str:
        """Save an uploaded image's raw bytes; return the stored path.

        Uses an SHA-1 of the bytes + the original extension to dedupe and
        bucket. Returns the path relative to the storage root (what
        ``input_path`` on ScoringJob stores).
        """
        digest = hashlib.sha1(upload_bytes).hexdigest()[:16]
        ext = Path(original_name).suffix or ".jpg"
        name = f"uploads/{digest}{ext}"
        from django.core.files.base import ContentFile
        self._storage.save(name, ContentFile(upload_bytes))
        return name

    def deliverable_dir(self, job_id: UUID) -> Path:
        """The directory deliverables for this job live in."""
        return self._safe_join(f"jobs/{job_id}")

    def write_deliverable(self, job_id: UUID, name: str, data: bytes) -> str:
        """Write ``name`` (e.g. ``12_llm_input.png``) into the job's bucket;
        return the relative path stored on ``ScoringJob``."""
        out_dir = self.deliverable_dir(job_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        full = out_dir / name
        full.write_bytes(data)
        return str(full.relative_to(self._root))

    def read_upload(self, stored_path: str) -> bytes:
        """Read an upload back as bytes (the q2 task body uses this).

        FS-only — raises ``NotImplementedError`` under S3. The byte-oriented
        ``read_upload_bytes`` is the S3-safe replacement; this stays for the
        CLI / FS-test path.
        """
        return self._safe_join(stored_path).read_bytes()

    def absolute_path(self, stored_path: str) -> Path:
        """Resolve a stored path relative to the storage root.

        FS-only — raises ``NotImplementedError`` under S3. ``process_image``
        no longer calls this (S-03 Phase 4 switched it to the byte-oriented
        surface + a tempfile dance for ``cv2.imread``); it stays for the CLI.
        """
        return self._safe_join(stored_path)

    # ------------------------------------------------------------------
    # S-03 Phase 4: byte-oriented surface that works under BOTH FS and S3.
    # ``process_image`` switched to these so the S3 path no longer raises
    # (cv2.imread cannot read an S3 key; the upload bytes are downloaded to a
    # tempfile before the pipeline runs, and deliverables are uploaded back).
    # ------------------------------------------------------------------

    def read_upload_bytes(self, stored_path: str) -> bytes:
        """Read an upload back as bytes, under either backend.

        Under FS this is the existing ``read_upload`` body (``_safe_join`` +
        ``read_bytes``); under S3 it streams via ``self._storage.open(...).read()``
        — the only S3-safe way to get the upload's bytes for the tempfile dance.
        """
        if self._is_s3:
            return self._storage.open(self._safe_key(stored_path), "rb").read()
        return self._safe_join(stored_path).read_bytes()

    def read_deliverable_bytes(self, stored_path: str) -> bytes:
        """Read a deliverable (a job's ``marked_image_path`` etc.) back as
        bytes, under either backend.

        Symmetric to ``read_upload_bytes`` but for the deliverable keys written
        by ``write_deliverable_bytes``. The BFF's marked-image proxy route
        reads a job's marked-image artifact through this so the bytes never
        reach the browser via a presigned S3 URL (which would expose
        ``AWSAccessKeyId`` + signature and bake in an internal endpoint host).
        """
        if self._is_s3:
            return self._storage.open(self._safe_key(stored_path), "rb").read()
        return self._safe_join(stored_path).read_bytes()

    def write_deliverable_bytes(self, job_id: UUID, name: str, data: bytes) -> str:
        """Write a deliverable (``{stem}_marked.png`` etc.) and return its stored
        key, under either backend.

        Under FS this is the existing ``write_deliverable`` body (mkdir + write +
        relative path); under S3 it stores via ``self._storage.save(name, ...)``
        under ``jobs/{job_id}/{name}`` and returns that key. The key is what
        ``ScoringJob.llm_input_path`` / ``marked_image_path`` / ``result_json_path``
        store, and what ``_job_to_dto``'s ``storage._storage.url(...)`` resolves.
        """
        from django.core.files.base import ContentFile
        key = f"jobs/{job_id}/{name}"
        if self._is_s3:
            self._storage.save(key, ContentFile(data))
            return key
        out_dir = self._safe_join(f"jobs/{job_id}")
        out_dir.mkdir(parents=True, exist_ok=True)
        full = out_dir / name
        full.write_bytes(data)
        return str(full.relative_to(self._root))
