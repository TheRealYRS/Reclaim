
import os
import stat
import tempfile
import zipfile
import zlib
from pathlib import Path, PurePosixPath

from zip_analyzer import get_data_range, validate_member
from hole_punch import make_sparse, punch_hole, get_allocated_size


CHUNK_SIZE = 1024 * 1024


def check_cancel(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        from smart_extract import ExtractionCancelled
        raise ExtractionCancelled("Extraction cancelled by user.")


def emit_progress(callback, event, **data):
    if callback is not None:
        callback(event, data)


def get_safe_output_path(output_dir, filename):
    output_dir = Path(output_dir).resolve()
    normalized = filename.replace("\\", "/")
    path = PurePosixPath(normalized)

    if path.is_absolute() or normalized.startswith("//"):
        raise ValueError(f"Unsafe absolute/UNC ZIP path: {filename!r}")
    if len(normalized) >= 2 and normalized[1] == ":":
        raise ValueError(f"Unsafe drive-qualified ZIP path: {filename!r}")
    if ".." in path.parts:
        raise ValueError(f"Path traversal detected: {filename!r}")

    target = output_dir / Path(*path.parts)
    try:
        target.parent.resolve().relative_to(output_dir)
    except ValueError as error:
        raise ValueError(f"Unsafe extraction path: {filename!r}") from error
    return target


def ensure_safe_parent(output_dir, target):
    output_dir = Path(output_dir).resolve()
    target = Path(target)

    try:
        target.parent.resolve().relative_to(output_dir)
    except ValueError as error:
        raise ValueError(f"Output path escapes destination: {target}") from error

    current = output_dir
    for part in target.parent.relative_to(output_dir).parts:
        current = current / part
        if current.exists():
            if current.is_symlink():
                raise ValueError(
                    f"Refusing to extract through symbolic-link directory: {current}"
                )
            if not current.is_dir():
                raise ValueError(f"Expected directory but found file: {current}")
        else:
            current.mkdir()

    if target.parent.is_symlink():
        raise ValueError(
            f"Refusing to extract through symbolic-link directory: {target.parent}"
        )


def set_safe_permissions(path):
    try:
        if path.is_dir():
            path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        else:
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def extract_verified(
    archive,
    info,
    target,
    output_root,
    progress_callback=None,
    cancel_event=None,
    overwrite=False,
):
    target = Path(target)
    output_root = Path(output_root).resolve()

    ensure_safe_parent(output_root, target)

    if target.is_symlink():
        raise ValueError(f"Refusing to overwrite symbolic link: {target}")

    if target.exists() and not overwrite:
        raise FileExistsError(f"Destination already exists: {target}")

    temp_fd = None
    temp_path = None
    total_written = 0
    crc = 0

    try:
        temp_fd, temp_name = tempfile.mkstemp(
            prefix=".reclaim-",
            suffix=".tmp",
            dir=str(target.parent),
        )
        temp_path = Path(temp_name)

        with os.fdopen(temp_fd, "wb") as destination:
            temp_fd = None

            with archive.open(info, "r") as source:
                while True:
                    check_cancel(cancel_event)
                    chunk = source.read(CHUNK_SIZE)
                    if not chunk:
                        break

                    destination.write(chunk)
                    total_written += len(chunk)
                    crc = zlib.crc32(chunk, crc)

                    emit_progress(
                        progress_callback,
                        "bytes_progress",
                        filename=info.filename,
                        bytes_processed=total_written,
                        file_size=info.file_size,
                        compressed_size=info.compress_size,
                    )

            destination.flush()
            os.fsync(destination.fileno())

        if total_written != info.file_size:
            raise ValueError(
                f"Size verification failed for {info.filename!r}: "
                f"expected {info.file_size:,}, got {total_written:,}"
            )

        crc &= 0xFFFFFFFF
        if crc != info.CRC:
            raise ValueError(
                f"CRC verification failed for {info.filename!r}: "
                f"expected {info.CRC:08x}, got {crc:08x}"
            )

        check_cancel(cancel_event)
        ensure_safe_parent(output_root, target)

        if target.is_symlink():
            raise ValueError(f"Destination became a symbolic link: {target}")
        if target.exists() and not overwrite:
            raise FileExistsError(
                f"Destination appeared during extraction: {target}"
            )

        os.replace(temp_path, target)
        temp_path = None
        set_safe_permissions(target)
        return total_written

    finally:
        if temp_fd is not None:
            try:
                os.close(temp_fd)
            except OSError:
                pass
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def safe_extract(
    zip_path,
    info,
    output_dir,
    progress_callback=None,
    cancel_event=None,
    target_override=None,
    overwrite=False,
):
    zip_path = Path(zip_path)
    output_dir = Path(output_dir).resolve()

    print(f"Processing: {info.filename}")
    check_cancel(cancel_event)

    print("  Validating entry...")
    validate_member(info)

    with zipfile.ZipFile(zip_path, "r") as archive:
        data_start, data_end = get_data_range(archive, info)

    file_size = zip_path.stat().st_size
    if data_start < 0 or data_end < data_start or data_end > file_size:
        raise ValueError("Invalid compressed data range.")

    if data_end - data_start != info.compress_size:
        raise ValueError(f"Compressed range size mismatch for {info.filename!r}")

    print("  ✓ Entry validation successful")
    print(f"  Compressed size: {info.compress_size:,} bytes")
    print(f"  Data range: {data_start:,} → {data_end:,}")

    target = (
        Path(target_override)
        if target_override is not None
        else get_safe_output_path(output_dir, info.filename)
    )
    target = target.resolve()

    try:
        target.relative_to(output_dir)
    except ValueError as error:
        raise ValueError(f"Output path escapes destination: {target}") from error

    ensure_safe_parent(output_dir, target)
    print("  Output path validated")
    print("  Extracting and verifying...")

    try:
        with zipfile.ZipFile(zip_path, "r") as archive:
            extract_verified(
                archive,
                info,
                target,
                output_dir,
                progress_callback=progress_callback,
                cancel_event=cancel_event,
                overwrite=overwrite,
            )
    except Exception as error:
        from smart_extract import ExtractionCancelled
        if isinstance(error, ExtractionCancelled):
            raise
        print("  ✗ Extraction/verification failed")
        print(f"    Error: {error}")
        print("  ⚠ Compressed data was NOT reclaimed")
        raise

    print("  ✓ Extraction completed")
    print("  ✓ CRC verification successful")

    if data_start == data_end:
        print("  ✓ No compressed data to reclaim")
        return 0

    check_cancel(cancel_event)

    print("  Making ZIP sparse...")
    make_sparse(zip_path)
    print("  ✓ ZIP is sparse")

    allocated_before = get_allocated_size(zip_path)

    check_cancel(cancel_event)
    print("  Reclaiming compressed data...")
    punch_hole(zip_path, data_start, data_end)

    allocated_after = get_allocated_size(zip_path)
    reclaimed = max(0, allocated_before - allocated_after)

    if not target.is_file():
        raise RuntimeError(
            f"Extracted output disappeared after reclamation: {target}"
        )

    print("  ✓ Reclamation successful")
    print(f"  ✓ Space reclaimed: {reclaimed:,} bytes")
    return reclaimed
