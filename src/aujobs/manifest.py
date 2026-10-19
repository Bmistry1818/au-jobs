"""Snapshot manifest: a record of every raw source file the pipeline has used.

Each downloaded workbook is hashed and logged with its source URL and retrieval
time. Every published figure can then be traced to an exact file, and a file
that changes after it was recorded shows up as a checksum mismatch.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from aujobs.config import MANIFEST_JSON, read_json, write_json_atomic

_CHUNK_BYTES = 65_536


@dataclass(frozen=True, slots=True)
class Snapshot:
    name: str
    file: str
    source_url: str
    sha256: str
    bytes: int
    retrieved_at: str


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load(manifest_path: Path = MANIFEST_JSON) -> dict[str, Snapshot]:
    """All recorded snapshots, keyed by name. Empty if there is no manifest yet."""
    if not manifest_path.exists():
        return {}
    payload = read_json(manifest_path)
    return {entry["name"]: Snapshot(**entry) for entry in payload.get("snapshots", [])}


def record(
    name: str,
    path: Path,
    source_url: str,
    *,
    manifest_path: Path = MANIFEST_JSON,
    retrieved_at: str | None = None,
) -> Snapshot:
    """Hash ``path`` and store it in the manifest under ``name``, replacing any earlier entry."""
    snapshot = Snapshot(
        name=name,
        file=path.name,
        source_url=source_url,
        sha256=sha256_of(path),
        bytes=path.stat().st_size,
        retrieved_at=retrieved_at or utc_now(),
    )
    snapshots = load(manifest_path)
    snapshots[name] = snapshot
    write_json_atomic(
        manifest_path,
        {"snapshots": [asdict(snapshots[key]) for key in sorted(snapshots)]},
    )
    return snapshot


def verify(manifest_path: Path, raw_dir: Path) -> list[str]:
    """Describe every recorded file that is missing or no longer matches its checksum."""
    problems: list[str] = []
    for snapshot in load(manifest_path).values():
        path = raw_dir / snapshot.file
        if not path.exists():
            problems.append(f"{snapshot.name}: {snapshot.file} is missing from {raw_dir}")
        elif sha256_of(path) != snapshot.sha256:
            problems.append(f"{snapshot.name}: {snapshot.file} changed after it was recorded")
    return problems


def newest_retrieval(snapshots: Iterable[Snapshot]) -> str | None:
    return max((snapshot.retrieved_at for snapshot in snapshots), default=None)
