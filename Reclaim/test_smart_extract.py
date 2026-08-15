import tempfile
import zipfile
import hashlib
from pathlib import Path

from smart_extract import smart_extract
from state_manager import load_state


def create_test_zip(zip_path):
    files = {
        "hello.txt": b"Hello SmartExtract!\n",
        "folder/test.txt": b"This is a nested file.\n" * 100,
        "large.bin": bytes(range(256)) * 10000,
    }

    with zipfile.ZipFile(
        zip_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as z:
        for name, data in files.items():
            z.writestr(name, data)

    return files


def sha256(path):
    h = hashlib.sha256()

    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)

    return h.hexdigest()


def verify_files(output_dir, expected):
    for name, data in expected.items():

        path = output_dir / name

        assert path.exists(), f"Missing file: {name}"
        assert path.is_file(), f"Not a file: {name}"

        actual = path.read_bytes()

        assert actual == data, (
            f"Content mismatch: {name}"
        )

        assert len(actual) == len(data), (
            f"Size mismatch: {name}"
        )


def main():

    with tempfile.TemporaryDirectory(
        prefix="smartextract-test-"
    ) as temp:

        root = Path(temp)

        zip_path = root / "test.zip"
        output_dir = root / "output"

        print("=" * 60)
        print("SmartExtract automated test suite")
        print("=" * 60)

        # --------------------------------------------------
        # TEST 1 — Create archive
        # --------------------------------------------------

        print("\n[1/7] Creating test ZIP...")

        expected = create_test_zip(zip_path)

        assert zip_path.exists()

        print("PASS")

        # --------------------------------------------------
        # TEST 2 — Full extraction
        # --------------------------------------------------

        print("\n[2/7] Testing full extraction...")

        result = smart_extract(
            zip_path,
            output_dir,
        )

        print("Result:", result)

        assert result["failed"] == 0
        assert result["successful"] == 3

        verify_files(
            output_dir,
            expected
        )

        print("PASS")

        # --------------------------------------------------
        # TEST 3 — State file
        # --------------------------------------------------

        print("\n[3/7] Testing resume state...")

        state = load_state(
            zip_path,
            output_dir
        )

        completed = state.get(
            "completed",
            {}
        )

        assert len(completed) == 3

        for name in expected:
            assert name in completed

        print("PASS")

        # --------------------------------------------------
        # TEST 4 — Resume extraction
        # --------------------------------------------------

        print("\n[4/7] Testing resume...")

        result = smart_extract(
            zip_path,
            output_dir,
        )

        print("Result:", result)

        assert result["failed"] == 0
        assert result["skipped"] == 3

        verify_files(
            output_dir,
            expected
        )

        print("PASS")

        # --------------------------------------------------
        # TEST 5 — Test interruption / stop_after
        # --------------------------------------------------

        print("\n[5/7] Testing interruption...")

        interrupt_zip = root / "interrupt.zip"
        interrupt_output = root / "interrupt_output"

        interrupt_expected = create_test_zip(
            interrupt_zip
        )

        result = smart_extract(
            interrupt_zip,
            interrupt_output,
            stop_after=1,
        )

        print("Result:", result)

        assert result["interrupted"] is True
        assert result["successful"] == 1

        state = load_state(
            interrupt_zip,
            interrupt_output
        )

        assert len(
            state["completed"]
        ) == 1

        print("PASS")

        # --------------------------------------------------
        # TEST 6 — Resume after interruption
        # --------------------------------------------------

        print("\n[6/7] Testing resume after interruption...")

        result = smart_extract(
            interrupt_zip,
            interrupt_output,
        )

        print("Result:", result)

        assert result["failed"] == 0

        verify_files(
            interrupt_output,
            interrupt_expected
        )

        state = load_state(
            interrupt_zip,
            interrupt_output
        )

        assert len(
            state["completed"]
        ) == 3

        print("PASS")

        # --------------------------------------------------
        # TEST 7 — Output integrity
        # --------------------------------------------------

        print("\n[7/7] Testing output integrity...")

        for name, data in expected.items():

            path = output_dir / name

            assert sha256(path) == hashlib.sha256(
                data
            ).hexdigest()

        print("PASS")

        # --------------------------------------------------
        # Final
        # --------------------------------------------------

        print("\n" + "=" * 60)
        print("ALL SMARTEXTRACT TESTS PASSED")
        print("=" * 60)


if __name__ == "__main__":
    main()
