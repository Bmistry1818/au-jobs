from __future__ import annotations

from pathlib import Path

from aujobs import manifest

# SHA-256 of the bytes b"abc", a published test vector.
ABC_SHA256 = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_record_then_load_round_trip(tmp_path: Path) -> None:
    workbook = tmp_path / "profiles.xlsx"
    workbook.write_bytes(b"abc")
    manifest_path = tmp_path / "manifest.json"

    recorded = manifest.record(
        "occupation_profiles",
        workbook,
        "https://example.test/profiles.xlsx",
        manifest_path=manifest_path,
        retrieved_at="2026-10-01T00:00:00+00:00",
    )

    assert recorded.sha256 == ABC_SHA256
    assert recorded.bytes == 3
    assert manifest.load(manifest_path) == {"occupation_profiles": recorded}


def test_record_replaces_an_earlier_entry_with_the_same_name(tmp_path: Path) -> None:
    workbook = tmp_path / "profiles.xlsx"
    manifest_path = tmp_path / "manifest.json"
    workbook.write_bytes(b"first")
    manifest.record("occupation_profiles", workbook, "u1", manifest_path=manifest_path)
    workbook.write_bytes(b"second")
    manifest.record("occupation_profiles", workbook, "u2", manifest_path=manifest_path)

    loaded = manifest.load(manifest_path)
    assert list(loaded) == ["occupation_profiles"]
    assert loaded["occupation_profiles"].source_url == "u2"


def test_verify_flags_changed_and_missing_files(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    workbook = raw_dir / "profiles.xlsx"
    workbook.write_bytes(b"abc")
    manifest_path = raw_dir / "manifest.json"
    manifest.record("occupation_profiles", workbook, "u", manifest_path=manifest_path)

    assert manifest.verify(manifest_path, raw_dir) == []

    workbook.write_bytes(b"tampered")
    assert "changed after it was recorded" in manifest.verify(manifest_path, raw_dir)[0]

    workbook.unlink()
    assert "missing" in manifest.verify(manifest_path, raw_dir)[0]


def test_load_without_a_manifest_is_empty(tmp_path: Path) -> None:
    assert manifest.load(tmp_path / "none.json") == {}
