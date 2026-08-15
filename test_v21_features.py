
import hashlib
import tempfile
import zipfile
from pathlib import Path

from smart_extract import (
    COLLISION_CANCEL,
    COLLISION_RENAME,
    COLLISION_REPLACE,
    COLLISION_SKIP,
    analyze_archive,
    find_collisions,
    smart_extract,
)
from zip_analyzer import get_archive_summary


def create_zip(path):
    files = {
        "hello.txt": b"Hello from Reclaim!\n",
        "folder/data.txt": (b"Reclaim test data.\n" * 100),
    }

    with zipfile.ZipFile(
        path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        for name, data in files.items():
            archive.writestr(name, data)

    return files


def sha256(path):
    digest = hashlib.sha256()

    with open(path, "rb") as file:
        while True:
            chunk = file.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)

    return digest.hexdigest()


def test_analysis(root):
    print("\n[1/6] Testing archive analysis...")

    zip_path = root / "analysis.zip"
    expected = create_zip(zip_path)

    summary = get_archive_summary(zip_path)
    detailed = analyze_archive(zip_path)

    assert summary["file_count"] == len(expected)
    assert summary["compressed_bytes"] > 0
    assert summary["uncompressed_bytes"] >= summary["compressed_bytes"]
    assert summary["compression_ratio"] >= 1.0
    assert summary["estimated_reclaimable_bytes"] == (
        summary["compressed_bytes"]
    )

    assert len(detailed["files"]) == len(expected)

    names = {
        item["filename"]
        for item in detailed["files"]
    }

    assert names == set(expected)

    print("PASS")


def test_collision_detection(root):
    print("\n[2/6] Testing collision detection...")

    zip_path = root / "collision_detect.zip"
    output = root / "collision_detect_output"
    output.mkdir()

    create_zip(zip_path)

    existing = output / "hello.txt"
    existing.write_text(
        "EXISTING FILE",
        encoding="utf-8",
    )

    collisions = find_collisions(
        zip_path,
        output,
    )

    assert len(collisions) == 1
    assert collisions[0]["filename"] == "hello.txt"

    print("PASS")


def test_skip_collision(root):
    print("\n[3/6] Testing SKIP collision policy...")

    zip_path = root / "skip.zip"
    output = root / "skip_output"
    output.mkdir()

    create_zip(zip_path)

    existing = output / "hello.txt"
    existing.write_text(
        "DO NOT REPLACE",
        encoding="utf-8",
    )

    result = smart_extract(
        zip_path,
        output,
        collision_policy=COLLISION_SKIP,
    )

    assert result["failed"] == 0
    assert result["skipped"] == 1
    assert existing.read_text(
        encoding="utf-8"
    ) == "DO NOT REPLACE"

    assert (
        output / "folder" / "data.txt"
    ).is_file()

    print("PASS")


def test_rename_collision(root):
    print("\n[4/6] Testing RENAME collision policy...")

    zip_path = root / "rename.zip"
    output = root / "rename_output"
    output.mkdir()

    create_zip(zip_path)

    existing = output / "hello.txt"
    existing.write_text(
        "ORIGINAL",
        encoding="utf-8",
    )

    result = smart_extract(
        zip_path,
        output,
        collision_policy=COLLISION_RENAME,
    )

    renamed = output / "hello (1).txt"

    assert result["failed"] == 0
    assert renamed.is_file()
    assert existing.read_text(
        encoding="utf-8"
    ) == "ORIGINAL"
    assert renamed.read_text(
        encoding="utf-8"
    ) == "Hello from Reclaim!\n"

    print("PASS")


def test_replace_collision(root):
    print("\n[5/6] Testing REPLACE collision policy...")

    zip_path = root / "replace.zip"
    output = root / "replace_output"
    output.mkdir()

    expected = create_zip(zip_path)

    existing = output / "hello.txt"
    existing.write_text(
        "OLD CONTENT",
        encoding="utf-8",
    )

    result = smart_extract(
        zip_path,
        output,
        collision_policy=COLLISION_REPLACE,
    )

    assert result["failed"] == 0
    assert existing.read_bytes() == expected["hello.txt"]

    print("PASS")


def test_cancel_collision(root):
    print("\n[6/6] Testing CANCEL collision policy...")

    zip_path = root / "cancel.zip"
    output = root / "cancel_output"
    output.mkdir()

    create_zip(zip_path)

    existing = output / "hello.txt"
    existing.write_text(
        "KEEP THIS",
        encoding="utf-8",
    )

    result = smart_extract(
        zip_path,
        output,
        collision_policy=COLLISION_CANCEL,
    )

    assert result["cancelled"] is True
    assert result["successful"] == 0
    assert existing.read_text(
        encoding="utf-8"
    ) == "KEEP THIS"

    assert not (
        output / "folder" / "data.txt"
    ).exists()

    print("PASS")


def main():
    print("=" * 60)
    print("Reclaim v2.1 feature test suite")
    print("=" * 60)

    with tempfile.TemporaryDirectory(
        prefix="reclaim-v21-test-"
    ) as temp:
        root = Path(temp)

        test_analysis(root)
        test_collision_detection(root)
        test_skip_collision(root)
        test_rename_collision(root)
        test_replace_collision(root)
        test_cancel_collision(root)

    print("\n" + "=" * 60)
    print("ALL RECLAIM V2.1 FEATURE TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()
