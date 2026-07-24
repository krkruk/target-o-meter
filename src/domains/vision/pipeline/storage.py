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
        # Default to MEDIA_ROOT/scoring (configured in settings); fall back to
        # BASE_DIR/scoring_storage when MEDIA_ROOT isn't set.
        if location is None:
            from django.conf import settings
            media_root = getattr(settings, "MEDIA_ROOT", None)
            location = Path(media_root) / "scoring" if media_root else Path(settings.BASE_DIR) / "scoring_storage"
        self._storage = FileSystemStorage(location=str(location), base_url=base_url)
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
        """
        resolved = (self._root / stored_path).resolve()
        try:
            resolved.relative_to(self._root)
        except ValueError as exc:
            raise ValueError(
                f"stored_path {stored_path!r} escapes the storage root"
            ) from exc
        return resolved

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
        """Read an upload back as bytes (the q2 task body uses this)."""
        return self._safe_join(stored_path).read_bytes()

    def absolute_path(self, stored_path: str) -> Path:
        """Resolve a stored path relative to the storage root."""
        return self._safe_join(stored_path)
