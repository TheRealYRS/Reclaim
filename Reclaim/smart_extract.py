import shutil
import zipfile
from pathlib import Path

from hole_punch import get_allocated_size
from safe_extract import (
    get_safe_output_path,
    safe_extract,
)
from state_manager import (
    completed_file_is_valid,
    get_completed_record,
    get_completed_compressed_bytes,
    load_state,
    mark_completed,
    save_state,
)
from zip_analyzer import validate_archive_members


# ==============================================================
# COLLISION POLICIES
# ==============================================================

COLLISION_SKIP = "skip"
COLLISION_RENAME = "rename"
COLLISION_REPLACE = "replace"
COLLISION_CANCEL = "cancel"

VALID_COLLISION_POLICIES = {
    COLLISION_SKIP,
    COLLISION_RENAME,
    COLLISION_REPLACE,
    COLLISION_CANCEL,
}


class ExtractionCancelled(Exception):
    """Raised when extraction is cancelled by the caller."""

    pass


# ==============================================================
# PROGRESS
# ==============================================================

def emit_progress(
    callback,
    event,
    **data
):
    if callback is not None:
        callback(event, data)


# ==============================================================
# CANCELLATION
# ==============================================================

def check_cancel(
    cancel_event
):
    if (
        cancel_event is not None
        and cancel_event.is_set()
    ):
        raise ExtractionCancelled(
            "Extraction cancelled by user."
        )


# ==============================================================
# PATH HELPERS
# ==============================================================

def _unique_path(
    target
):
    """
    Return a non-existing path:

        file.txt
        file (1).txt
        file (2).txt
    """

    target = Path(target)

    if not target.exists():
        return target

    parent = target.parent
    stem = target.stem
    suffix = target.suffix

    counter = 1

    while True:

        candidate = (
            parent
            / f"{stem} ({counter}){suffix}"
        )

        if not candidate.exists():
            return candidate

        counter += 1


def _copy_verified_output(
    source,
    target,
    output_dir
):
    """
    Copy an already-verified output file to a new target.

    This is used when the source ZIP member has already been
    reclaimed. In that situation the original compressed bytes
    cannot safely be decompressed again, so a user-requested
    "Rename" is fulfilled by duplicating the verified output
    already on disk.
    """

    source = Path(source)
    target = Path(target)
    output_dir = Path(output_dir).resolve()

    source = source.resolve()
    target = target.resolve()

    try:
        target.relative_to(output_dir)
    except ValueError as error:
        raise ValueError(
            f"Duplicate output path escapes destination: {target}"
        ) from error

    if source == target:
        return

    if target.exists():
        raise FileExistsError(
            f"Duplicate destination already exists: {target}"
        )

    if source.is_symlink():
        raise ValueError(
            f"Refusing to copy from symbolic link: {source}"
        )

    if not source.is_file():
        raise FileNotFoundError(
            f"Verified source output does not exist: {source}"
        )

    target.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    temp_target = target.with_name(
        f".reclaim-copy-{target.name}.tmp"
    )

    try:

        if temp_target.exists():
            temp_target.unlink()

        shutil.copy2(
            source,
            temp_target
        )

        if target.exists():
            raise FileExistsError(
                f"Duplicate destination appeared during copy: "
                f"{target}"
            )

        temp_target.replace(
            target
        )

    finally:

        try:
            temp_target.unlink(
                missing_ok=True
            )
        except OSError:
            pass


# ==============================================================
# ARCHIVE DISCOVERY
# ==============================================================

def build_member_map(
    files
):
    return {
        info.filename: info
        for info in files
    }


def find_collisions(
    zip_path,
    output_dir
):
    """
    Find archive members whose normal extraction destination
    already exists.

    This is metadata-only and does not modify the archive.
    """

    zip_path = Path(zip_path)
    output_dir = Path(output_dir).resolve()

    output_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    with zipfile.ZipFile(
        zip_path,
        "r"
    ) as archive:

        files = [
            info
            for info in archive.infolist()
            if not info.is_dir()
        ]

        validate_archive_members(
            archive,
            files
        )

    collisions = []

    for info in files:

        target = get_safe_output_path(
            output_dir,
            info.filename
        )

        if target.exists():

            collisions.append(
                {
                    "filename": info.filename,
                    "output_path": target,
                    "file_size": info.file_size,
                    "crc": info.CRC,
                }
            )

    return collisions


# ==============================================================
# ARCHIVE ANALYSIS
# ==============================================================

def analyze_archive(
    zip_path
):
    """
    Return analysis information for the GUI.
    """

    from zip_analyzer import get_archive_summary

    summary = get_archive_summary(
        zip_path
    )

    with zipfile.ZipFile(
        zip_path,
        "r"
    ) as archive:

        files = [
            info
            for info in archive.infolist()
            if not info.is_dir()
        ]

    summary["files"] = [
        {
            "filename": info.filename,
            "compressed_size": info.compress_size,
            "uncompressed_size": info.file_size,
            "crc": info.CRC,
        }
        for info in files
    ]

    return summary


# ==============================================================
# RESULT
# ==============================================================

def build_result(
    *,
    cancelled,
    interrupted,
    total_files,
    successful,
    skipped,
    failed,
    total_reclaimed,
    logical_size,
    allocated_before,
    allocated_after,
    total_compressed_bytes,
    completed_compressed_bytes,
):
    return {
        "cancelled": bool(cancelled),
        "interrupted": bool(interrupted),
        "total_files": int(total_files),
        "successful": int(successful),
        "skipped": int(skipped),
        "failed": int(failed),
        "total_reclaimed": int(total_reclaimed),
        "logical_size": int(logical_size),
        "allocated_before": int(allocated_before),
        "allocated_after": int(allocated_after),
        "total_compressed_bytes": int(
            total_compressed_bytes
        ),
        "completed_compressed_bytes": int(
            completed_compressed_bytes
        ),
    }


# ==============================================================
# MAIN EXTRACTION
# ==============================================================

def smart_extract(
    zip_path,
    output_dir,
    stop_after=None,
    progress_callback=None,
    cancel_event=None,
    collision_policy=COLLISION_SKIP,
):
    """
    Extract, verify and reclaim a ZIP archive.

    Collision policy:

        skip:
            Existing files are left untouched. Valid Reclaim
            completions are resumed/skipped automatically.

        rename:
            If an existing file is a valid previous Reclaim output,
            create a duplicate from that verified output rather than
            attempting to decompress already-reclaimed ZIP data.

            If an existing file is not a previous valid completion,
            extract the archive member to file (1), file (2), etc.

        replace:
            Replace an existing regular file. A valid previous
            Reclaim completion does not require re-decompression;
            the existing verified output is already the extracted
            file.

        cancel:
            Stop as soon as a collision is encountered.

    IMPORTANT:
    Reclaim may punch the compressed member out of the ZIP after a
    successful extraction. Therefore it is never safe to blindly
    re-extract a previously completed member whose compressed bytes
    may already have been reclaimed.
    """

    zip_path = Path(zip_path)
    output_dir = Path(output_dir).resolve()

    if collision_policy not in VALID_COLLISION_POLICIES:

        raise ValueError(
            "Invalid collision policy: "
            f"{collision_policy!r}"
        )

    if not zip_path.exists():

        raise FileNotFoundError(
            f"ZIP archive does not exist:\n{zip_path}"
        )

    if not zip_path.is_file():

        raise ValueError(
            f"ZIP path is not a file:\n{zip_path}"
        )

    output_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    # ==========================================================
    # SECURITY PREFLIGHT
    # ==========================================================

    emit_progress(
        progress_callback,
        "preflight_started"
    )

    check_cancel(
        cancel_event
    )

    try:

        with zipfile.ZipFile(
            zip_path,
            "r"
        ) as archive:

            files = [
                info
                for info in archive.infolist()
                if not info.is_dir()
            ]

            validate_archive_members(
                archive,
                files
            )

    except zipfile.BadZipFile as error:

        emit_progress(
            progress_callback,
            "preflight_failed",
            error=str(error)
        )

        raise ValueError(
            f"Invalid or malformed ZIP archive:\n{error}"
        ) from error

    except Exception as error:

        emit_progress(
            progress_callback,
            "preflight_failed",
            error=str(error)
        )

        raise

    emit_progress(
        progress_callback,
        "preflight_complete",
        total_files=len(files)
    )

    member_map = build_member_map(
        files
    )

    logical_size = zip_path.stat().st_size

    allocated_before = (
        get_allocated_size(
            zip_path
        )
    )

    total_compressed_bytes = sum(
        info.compress_size
        for info in files
    )

    # ==========================================================
    # RESUME STATE
    # ==========================================================

    state = load_state(
        zip_path,
        output_dir
    )

    prior_completed_bytes = min(
        get_completed_compressed_bytes(
            state,
            member_map
        ),
        total_compressed_bytes
    )

    emit_progress(
        progress_callback,
        "started",
        total_files=len(files),
        total_compressed_bytes=(
            total_compressed_bytes
        ),
        completed_compressed_bytes=(
            prior_completed_bytes
        ),
        logical_size=logical_size,
        allocated_before=allocated_before,
        resumed=prior_completed_bytes > 0,
        collision_policy=collision_policy,
    )

    # ==========================================================
    # STATISTICS
    # ==========================================================

    total_reclaimed = 0
    successful = 0
    skipped = 0
    failed = 0

    cancelled = False
    interrupted = False

    completed_compressed_bytes = 0

    # ==========================================================
    # PROCESS MEMBERS
    # ==========================================================

    for number, info in enumerate(
        files,
        start=1
    ):

        check_cancel(
            cancel_event
        )

        filename = info.filename

        original_target = (
            get_safe_output_path(
                output_dir,
                filename
            )
        )

        record = get_completed_record(
            state,
            filename
        )

        resume_target = original_target

        if record is not None:

            saved_path = record.get(
                "output_path"
            )

            if saved_path:
                resume_target = Path(
                    saved_path
                )

        # ======================================================
        # FIRST: CHECK WHETHER THIS IS A VALID PREVIOUSLY
        # COMPLETED RECLAIM OUTPUT.
        #
        # We do this because a previous completion means the ZIP
        # member may already have been physically reclaimed.
        # ======================================================

        resume_valid = False

        if record is not None:

            try:

                resume_valid = (
                    completed_file_is_valid(
                        state,
                        filename,
                        resume_target,
                        info.file_size,
                        info.CRC,
                        verify_crc=True,
                    )
                )

            except Exception:

                resume_valid = False

        # ======================================================
        # VALID PREVIOUS COMPLETION
        # ======================================================

        if resume_valid:

            # --------------------------------------------------
            # SKIP:
            # Normal resume behavior.
            # --------------------------------------------------

            if collision_policy == COLLISION_SKIP:

                skipped += 1

                completed_compressed_bytes += (
                    info.compress_size
                )

                emit_progress(
                    progress_callback,
                    "file_skipped",
                    filename=filename,
                    index=number,
                    total=len(files),
                    compressed_size=(
                        info.compress_size
                    ),
                    uncompressed_size=(
                        info.file_size
                    ),
                    completed_compressed_bytes=(
                        completed_compressed_bytes
                    ),
                    total_compressed_bytes=(
                        total_compressed_bytes
                    ),
                    resume_verified=True,
                    output_path=str(resume_target),
                )

                continue

            # --------------------------------------------------
            # RENAME:
            #
            # The original compressed bytes may already have been
            # reclaimed. Create the requested duplicate from the
            # verified output file that Reclaim already produced.
            # --------------------------------------------------

            if collision_policy == COLLISION_RENAME:

                duplicate_target = _unique_path(
                    original_target
                )

                try:

                    _copy_verified_output(
                        resume_target,
                        duplicate_target,
                        output_dir
                    )

                except Exception as error:

                    failed += 1

                    emit_progress(
                        progress_callback,
                        "file_failed",
                        filename=filename,
                        index=number,
                        total=len(files),
                        error=(
                            "Could not duplicate the "
                            "previously verified output: "
                            f"{error}"
                        ),
                    )

                    continue

                skipped += 1

                emit_progress(
                    progress_callback,
                    "collision_renamed",
                    filename=filename,
                    index=number,
                    total=len(files),
                    original_path=str(
                        original_target
                    ),
                    output_path=str(
                        duplicate_target
                    ),
                    from_verified_output=True,
                )

                emit_progress(
                    progress_callback,
                    "file_skipped",
                    filename=filename,
                    index=number,
                    total=len(files),
                    compressed_size=(
                        info.compress_size
                    ),
                    uncompressed_size=(
                        info.file_size
                    ),
                    completed_compressed_bytes=(
                        completed_compressed_bytes
                    ),
                    total_compressed_bytes=(
                        total_compressed_bytes
                    ),
                    resume_verified=True,
                    collision=True,
                    duplicated_from_verified=True,
                    output_path=str(
                        duplicate_target
                    ),
                )

                continue

            # --------------------------------------------------
            # REPLACE:
            #
            # The existing file is already verified as the exact
            # requested output. There is nothing useful to
            # re-decompress from the reclaimed ZIP.
            #
            # Treat it as a completed/verified replacement.
            # --------------------------------------------------

            if collision_policy == COLLISION_REPLACE:

                skipped += 1

                completed_compressed_bytes += (
                    info.compress_size
                )

                emit_progress(
                    progress_callback,
                    "file_skipped",
                    filename=filename,
                    index=number,
                    total=len(files),
                    compressed_size=(
                        info.compress_size
                    ),
                    uncompressed_size=(
                        info.file_size
                    ),
                    completed_compressed_bytes=(
                        completed_compressed_bytes
                    ),
                    total_compressed_bytes=(
                        total_compressed_bytes
                    ),
                    resume_verified=True,
                    collision=True,
                    already_verified=True,
                    output_path=str(
                        resume_target
                    ),
                )

                emit_progress(
                    progress_callback,
                    "collision_replaced",
                    filename=filename,
                    index=number,
                    total=len(files),
                    output_path=str(
                        resume_target
                    ),
                    already_verified=True,
                )

                continue

            # --------------------------------------------------
            # CANCEL
            # --------------------------------------------------

            if collision_policy == COLLISION_CANCEL:

                cancelled = True

                emit_progress(
                    progress_callback,
                    "collision_cancelled",
                    filename=filename,
                    index=number,
                    total=len(files),
                    output_path=str(
                        resume_target
                    ),
                    already_completed=True,
                )

                break

        # ======================================================
        # INVALID / NO PREVIOUS COMPLETION
        #
        # If a completion record exists but verification failed,
        # do NOT silently treat the member as a normal fresh
        # extraction. The source may already have been reclaimed.
        #
        # The exception is when the user explicitly requested a
        # separate rename target AND the recorded output can no
        # longer be trusted — we still refuse to guess.
        # ======================================================

        if record is not None and not resume_valid:

            failed += 1

            emit_progress(
                progress_callback,
                "resume_invalid",
                filename=filename,
                index=number,
                total=len(files),
                error=(
                    "A previous completion record exists, "
                    "but its output failed verification. "
                    "Re-extraction was not attempted because "
                    "the ZIP member may already have been "
                    "reclaimed."
                ),
            )

            emit_progress(
                progress_callback,
                "file_failed",
                filename=filename,
                index=number,
                total=len(files),
                error=(
                    "Previously completed output failed "
                    "resume verification."
                ),
                completed_compressed_bytes=(
                    completed_compressed_bytes
                ),
                total_compressed_bytes=(
                    total_compressed_bytes
                ),
            )

            continue

        # ======================================================
        # NO PREVIOUS COMPLETION:
        # HANDLE REAL EXISTING-FILE COLLISION.
        # ======================================================

        target = original_target
        overwrite = False

        if target.exists():

            # --------------------------------------------------
            # SKIP
            # --------------------------------------------------

            if collision_policy == COLLISION_SKIP:

                skipped += 1

                emit_progress(
                    progress_callback,
                    "file_skipped",
                    filename=filename,
                    index=number,
                    total=len(files),
                    compressed_size=(
                        info.compress_size
                    ),
                    uncompressed_size=(
                        info.file_size
                    ),
                    completed_compressed_bytes=(
                        completed_compressed_bytes
                    ),
                    total_compressed_bytes=(
                        total_compressed_bytes
                    ),
                    collision=True,
                    output_path=str(
                        target
                    ),
                )

                continue

            # --------------------------------------------------
            # RENAME
            # --------------------------------------------------

            if collision_policy == COLLISION_RENAME:

                target = _unique_path(
                    original_target
                )

                emit_progress(
                    progress_callback,
                    "collision_renamed",
                    filename=filename,
                    index=number,
                    total=len(files),
                    original_path=str(
                        original_target
                    ),
                    output_path=str(
                        target
                    ),
                    from_verified_output=False,
                )

            # --------------------------------------------------
            # REPLACE
            # --------------------------------------------------

            elif collision_policy == COLLISION_REPLACE:

                if target.is_symlink():

                    failed += 1

                    emit_progress(
                        progress_callback,
                        "file_failed",
                        filename=filename,
                        index=number,
                        total=len(files),
                        error=(
                            "Refusing to replace a "
                            "symbolic-link destination."
                        ),
                    )

                    continue

                if target.is_dir():

                    failed += 1

                    emit_progress(
                        progress_callback,
                        "file_failed",
                        filename=filename,
                        index=number,
                        total=len(files),
                        error=(
                            "Cannot replace a directory "
                            "with an extracted file."
                        ),
                    )

                    continue

                overwrite = True

            # --------------------------------------------------
            # CANCEL
            # --------------------------------------------------

            elif collision_policy == COLLISION_CANCEL:

                cancelled = True

                emit_progress(
                    progress_callback,
                    "collision_cancelled",
                    filename=filename,
                    index=number,
                    total=len(files),
                    output_path=str(
                        target
                    ),
                )

                break

        # ======================================================
        # FILE START
        # ======================================================

        emit_progress(
            progress_callback,
            "file_started",
            filename=filename,
            index=number,
            total=len(files),
            compressed_size=(
                info.compress_size
            ),
            uncompressed_size=(
                info.file_size
            ),
            completed_compressed_bytes=(
                completed_compressed_bytes
            ),
            total_compressed_bytes=(
                total_compressed_bytes
            ),
            output_path=str(
                target
            ),
        )

        # ======================================================
        # EXTRACT + VERIFY + RECLAIM
        # ======================================================

        try:

            reclaimed = safe_extract(
                zip_path,
                info,
                output_dir,
                progress_callback=(
                    progress_callback
                ),
                cancel_event=(
                    cancel_event
                ),
                target_override=target,
                overwrite=overwrite,
            )

        except ExtractionCancelled:

            cancelled = True

            emit_progress(
                progress_callback,
                "cancelled",
                filename=filename,
                index=number,
                total=len(files),
                completed=(
                    successful
                    + skipped
                ),
                successful=successful,
                skipped=skipped,
                failed=failed,
                total_reclaimed=(
                    total_reclaimed
                ),
                completed_compressed_bytes=(
                    completed_compressed_bytes
                ),
                total_compressed_bytes=(
                    total_compressed_bytes
                ),
            )

            break

        except Exception as error:

            failed += 1

            emit_progress(
                progress_callback,
                "file_failed",
                filename=filename,
                index=number,
                total=len(files),
                error=str(error),
                completed_compressed_bytes=(
                    completed_compressed_bytes
                ),
                total_compressed_bytes=(
                    total_compressed_bytes
                ),
            )

            continue

        # ======================================================
        # SUCCESS
        # ======================================================

        successful += 1

        total_reclaimed += (
            reclaimed
        )

        completed_compressed_bytes += (
            info.compress_size
        )

        # ------------------------------------------------------
        # Persist the original archive member's completion record.
        # If a rename target was used, that actual output path is
        # what gets saved.
        # ------------------------------------------------------

        mark_completed(
            state,
            filename,
            file_size=info.file_size,
            crc=info.CRC,
            output_path=target,
            reclaimed=reclaimed,
        )

        try:

            save_state(
                zip_path,
                output_dir,
                state
            )

        except Exception as error:

            failed += 1
            interrupted = True

            emit_progress(
                progress_callback,
                "state_save_failed",
                filename=filename,
                index=number,
                total=len(files),
                error=str(error),
                reclaimed=reclaimed,
            )

            break

        emit_progress(
            progress_callback,
            "file_completed",
            filename=filename,
            index=number,
            total=len(files),
            reclaimed=reclaimed,
            compressed_size=(
                info.compress_size
            ),
            uncompressed_size=(
                info.file_size
            ),
            completed_compressed_bytes=(
                completed_compressed_bytes
            ),
            total_compressed_bytes=(
                total_compressed_bytes
            ),
            output_path=str(
                target
            ),
        )

        # ======================================================
        # TEST INTERRUPTION
        # ======================================================

        if (
            stop_after is not None
            and successful >= stop_after
        ):

            interrupted = True

            allocated_after = (
                get_allocated_size(
                    zip_path
                )
            )

            emit_progress(
                progress_callback,
                "test_interruption",
                successful=successful,
                skipped=skipped,
                failed=failed,
                total_files=len(files),
                total_reclaimed=(
                    total_reclaimed
                ),
                allocated_after=(
                    allocated_after
                ),
                completed_compressed_bytes=(
                    completed_compressed_bytes
                ),
                total_compressed_bytes=(
                    total_compressed_bytes
                ),
            )

            return build_result(
                cancelled=False,
                interrupted=True,
                total_files=len(files),
                successful=successful,
                skipped=skipped,
                failed=failed,
                total_reclaimed=(
                    total_reclaimed
                ),
                logical_size=logical_size,
                allocated_before=(
                    allocated_before
                ),
                allocated_after=(
                    allocated_after
                ),
                total_compressed_bytes=(
                    total_compressed_bytes
                ),
                completed_compressed_bytes=(
                    completed_compressed_bytes
                ),
            )

    # ==========================================================
    # FINAL RESULT
    # ==========================================================

    allocated_after = get_allocated_size(
        zip_path
    )

    result = build_result(
        cancelled=cancelled,
        interrupted=interrupted,
        total_files=len(files),
        successful=successful,
        skipped=skipped,
        failed=failed,
        total_reclaimed=total_reclaimed,
        logical_size=logical_size,
        allocated_before=allocated_before,
        allocated_after=allocated_after,
        total_compressed_bytes=(
            total_compressed_bytes
        ),
        completed_compressed_bytes=(
            completed_compressed_bytes
        ),
    )

    if cancelled:

        emit_progress(
            progress_callback,
            "cancelled_complete",
            **result
        )

    elif interrupted:

        emit_progress(
            progress_callback,
            "interrupted_complete",
            **result
        )

    else:

        emit_progress(
            progress_callback,
            "complete",
            **result
        )

    return result