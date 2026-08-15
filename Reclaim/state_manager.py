import hashlib
import json
import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path


# ==============================================================
# RECLAIM STATE MANAGER
# ==============================================================

STATE_FILENAME = ".reclaim_state.json"

# Version 3 changes the archive identity model.
#
# IMPORTANT:
# The old v2 fingerprint hashed bytes from the beginning/end of
# the ZIP file. That is not suitable for Reclaim because Reclaim
# intentionally changes the ZIP's physical allocation.
#
# V3 instead identifies the archive from immutable ZIP metadata:
# filenames, CRCs, sizes, compression metadata, offsets, flags,
# and archive-level metadata.
#
STATE_VERSION = 3

# Supported legacy format.
LEGACY_STATE_VERSION = 2


# ==============================================================
# STATE PATH
# ==============================================================

def get_state_path(output_dir):
    """
    Return the path to Reclaim's persistent state file.
    """

    return Path(output_dir) / STATE_FILENAME


# ==============================================================
# IMMUTABLE ARCHIVE IDENTITY
# ==============================================================

def _get_archive_metadata(zip_path):
    """
    Read the ZIP metadata used to create a stable archive identity.

    The metadata comes from the ZIP central directory and related
    archive structure. Reclaim's hole punching changes the physical
    allocation of compressed data, but does not change these values.

    Returns:
        A JSON-serializable dictionary.
    """

    zip_path = Path(zip_path)

    if not zip_path.is_file():
        raise FileNotFoundError(
            f"ZIP archive does not exist: {zip_path}"
        )

    logical_size = zip_path.stat().st_size

    with zipfile.ZipFile(
        zip_path,
        "r"
    ) as archive:

        members = []

        for info in archive.infolist():

            members.append(
                {
                    "filename": info.filename,
                    "file_size": int(info.file_size),
                    "compress_size": int(info.compress_size),
                    "crc": int(info.CRC),
                    "compress_type": int(info.compress_type),
                    "flag_bits": int(info.flag_bits),
                    "header_offset": int(info.header_offset),
                    "create_system": int(info.create_system),
                    "create_version": int(info.create_version),
                    "extract_version": int(info.extract_version),
                    "external_attr": int(info.external_attr),
                    "internal_attr": int(info.internal_attr),
                    "date_time": list(info.date_time),
                }
            )

        metadata = {
            "logical_size": int(logical_size),
            "comment": archive.comment.decode(
                "utf-8",
                errors="surrogateescape"
            ),
            "members": members,
        }

    return metadata


def get_archive_identity(zip_path):
    """
    Return a stable identity for a ZIP archive.

    Unlike the old byte-fingerprint approach, this identity is based
    on ZIP metadata that remains unchanged when Reclaim punches holes
    in compressed data.

    The identity is therefore suitable for resume after reclamation.
    """

    metadata = _get_archive_metadata(
        zip_path
    )

    payload = json.dumps(
        metadata,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode(
        "utf-8",
        errors="surrogateescape"
    )

    return hashlib.sha256(
        payload
    ).hexdigest()


# ==============================================================
# LEGACY IDENTITY
# ==============================================================

def get_legacy_archive_fingerprint(zip_path):
    """
    Reproduce the old Reclaim v2 fingerprint.

    This is retained only to recognize legacy state files during
    migration. It must NOT be used as the normal archive identity.
    """

    zip_path = Path(zip_path)

    if not zip_path.is_file():
        raise FileNotFoundError(
            f"ZIP archive does not exist: {zip_path}"
        )

    file_size = zip_path.stat().st_size

    hasher = hashlib.sha256()

    hasher.update(
        str(file_size).encode("utf-8")
    )

    chunk_size = 1024 * 1024

    with open(zip_path, "rb") as file:

        first_chunk = file.read(
            chunk_size
        )

        hasher.update(
            first_chunk
        )

        if file_size > chunk_size:

            file.seek(
                max(
                    0,
                    file_size - chunk_size
                )
            )

            last_chunk = file.read(
                chunk_size
            )

            hasher.update(
                last_chunk
            )

    return hasher.hexdigest()


# ==============================================================
# STATE CREATION
# ==============================================================

def create_state(zip_path):
    """
    Create a new V3 state structure for an archive.
    """

    zip_path = Path(zip_path)

    now = datetime.now(
        timezone.utc
    ).isoformat()

    return {
        "version": STATE_VERSION,

        "archive": {
            "name": zip_path.name,

            # Logical ZIP size is stable across hole punching.
            "logical_size": zip_path.stat().st_size,

            "archive_id": get_archive_identity(
                zip_path
            ),

            "identity_method": (
                "zip-central-directory-metadata-v1"
            ),
        },

        "completed": {},

        "created_at": now,
        "updated_at": now,
    }


# ==============================================================
# STATE VALIDATION
# ==============================================================

def _is_valid_state_structure(state):
    """
    Validate a V3 state structure.
    """

    if not isinstance(
        state,
        dict
    ):
        return False

    if state.get("version") != STATE_VERSION:
        return False

    archive = state.get(
        "archive"
    )

    if not isinstance(
        archive,
        dict
    ):
        return False

    if not archive.get(
        "archive_id"
    ):
        return False

    completed = state.get(
        "completed"
    )

    if not isinstance(
        completed,
        dict
    ):
        return False

    return True


# ==============================================================
# CURRENT ARCHIVE MEMBERS
# ==============================================================

def _get_member_map(zip_path):
    """
    Return current ZIP members keyed by filename.
    """

    with zipfile.ZipFile(
        zip_path,
        "r"
    ) as archive:

        return {
            info.filename: info
            for info in archive.infolist()
            if not info.is_dir()
        }


# ==============================================================
# LEGACY MIGRATION
# ==============================================================

def _migrate_legacy_state(
    zip_path,
    legacy_state
):
    """
    Convert a Reclaim v2 state file to V3.

    Legacy states used a byte fingerprint that can become invalid
    after hole punching. We therefore do NOT trust that fingerprint
    as the new identity.

    Instead, completed records are retained only when their stored
    file metadata still matches the current ZIP entry.

    This allows an already-partially-reclaimed archive to continue
    using its existing progress state.
    """

    current_members = _get_member_map(
        zip_path
    )

    legacy_completed = legacy_state.get(
        "completed",
        {}
    )

    migrated_completed = {}

    if isinstance(
        legacy_completed,
        dict
    ):

        for filename, record in legacy_completed.items():

            if not isinstance(
                filename,
                str
            ):
                continue

            if not isinstance(
                record,
                dict
            ):
                continue

            info = current_members.get(
                filename
            )

            if info is None:
                continue

            saved_size = record.get(
                "file_size"
            )

            saved_crc = record.get(
                "crc"
            )

            if saved_size != info.file_size:
                continue

            if saved_crc != info.CRC:
                continue

            migrated_completed[filename] = {
                "file_size": int(
                    saved_size
                ),

                "crc": int(
                    saved_crc
                ),

                "output_path": str(
                    record.get(
                        "output_path",
                        ""
                    )
                ),

                "reclaimed": int(
                    record.get(
                        "reclaimed",
                        0
                    )
                ),

                "completed_at": record.get(
                    "completed_at",
                    datetime.now(
                        timezone.utc
                    ).isoformat()
                ),

                "migrated_from_version": (
                    LEGACY_STATE_VERSION
                ),
            }

    state = create_state(
        zip_path
    )

    state["completed"] = (
        migrated_completed
    )

    state["migration"] = {
        "from_version": LEGACY_STATE_VERSION,
        "migrated_at": datetime.now(
            timezone.utc
        ).isoformat(),
    }

    return state


# ==============================================================
# LOAD STATE
# ==============================================================

def load_state(
    zip_path,
    output_dir
):
    """
    Load extraction state for a ZIP archive.

    Behavior:

        - missing state -> create V3 state
        - corrupt state -> create V3 state
        - V3 state with matching archive_id -> use it
        - V3 state for another archive -> create fresh state
        - V2 state -> migrate safely using current ZIP metadata
    """

    zip_path = Path(
        zip_path
    )

    output_dir = Path(
        output_dir
    )

    state_path = get_state_path(
        output_dir
    )

    if not state_path.exists():

        return create_state(
            zip_path
        )

    try:

        with open(
            state_path,
            "r",
            encoding="utf-8"
        ) as file:

            state = json.load(
                file
            )

    except (
        json.JSONDecodeError,
        OSError,
        UnicodeDecodeError
    ):

        print(
            "⚠ State file could not be read."
        )

        print(
            "  Starting a new extraction state."
        )

        return create_state(
            zip_path
        )

    # ----------------------------------------------------------
    # Native V3 state
    # ----------------------------------------------------------

    if _is_valid_state_structure(
        state
    ):

        current_identity = (
            get_archive_identity(
                zip_path
            )
        )

        saved_identity = (
            state["archive"].get(
                "archive_id"
            )
        )

        if saved_identity != current_identity:

            print(
                "⚠ Existing state belongs to "
                "a different archive."
            )

            print(
                "  Starting a new extraction state."
            )

            return create_state(
                zip_path
            )

        return state

    # ----------------------------------------------------------
    # Legacy V2 state
    # ----------------------------------------------------------

    if (
        isinstance(state, dict)
        and state.get("version")
        == LEGACY_STATE_VERSION
    ):

        print(
            "↻ Migrating Reclaim v2 state "
            "to the v3 resume format..."
        )

        migrated = _migrate_legacy_state(
            zip_path,
            state
        )

        migrated_count = len(
            migrated.get(
                "completed",
                {}
            )
        )

        print(
            f"✓ Migrated {migrated_count} "
            f"completed file(s)"
        )

        return migrated

    # ----------------------------------------------------------
    # Unknown state format
    # ----------------------------------------------------------

    print(
        "⚠ State file has an incompatible format."
    )

    print(
        "  Starting a new extraction state."
    )

    return create_state(
        zip_path
    )


# ==============================================================
# SAVE STATE
# ==============================================================

def save_state(
    zip_path,
    output_dir,
    state
):
    """
    Save V3 state atomically and durably.
    """

    zip_path = Path(
        zip_path
    )

    output_dir = Path(
        output_dir
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    state_path = get_state_path(
        output_dir
    )

    temp_path = state_path.with_name(
        state_path.name + ".tmp"
    )

    # Ensure archive metadata is present and corresponds to
    # the current archive identity.
    current_identity = (
        get_archive_identity(
            zip_path
        )
    )

    state["version"] = STATE_VERSION

    state.setdefault(
        "archive",
        {}
    )

    state["archive"]["name"] = (
        zip_path.name
    )

    state["archive"]["logical_size"] = (
        zip_path.stat().st_size
    )

    state["archive"]["archive_id"] = (
        current_identity
    )

    state["archive"]["identity_method"] = (
        "zip-central-directory-metadata-v1"
    )

    state.setdefault(
        "completed",
        {}
    )

    state["updated_at"] = (
        datetime.now(
            timezone.utc
        ).isoformat()
    )

    # ----------------------------------------------------------
    # Write temporary state
    # ----------------------------------------------------------

    with open(
        temp_path,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            state,
            file,
            indent=4,
            ensure_ascii=False
        )

        file.flush()

        os.fsync(
            file.fileno()
        )

    # ----------------------------------------------------------
    # Atomic replacement
    # ----------------------------------------------------------

    temp_path.replace(
        state_path
    )


# ==============================================================
# COMPLETION RECORD
# ==============================================================

def mark_completed(
    state,
    filename,
    file_size=None,
    crc=None,
    output_path=None,
    reclaimed=0
):
    """
    Record a successfully extracted and reclaimed file.

    The signature remains backward compatible with the previous
    Reclaim implementation.

    The record intentionally stores ZIP-entry metadata and output
    metadata rather than relying solely on a filename.
    """

    if not isinstance(
        state,
        dict
    ):
        raise TypeError(
            "State must be a dictionary."
        )

    completed = state.setdefault(
        "completed",
        {}
    )

    completed[filename] = {
        "file_size": int(
            file_size
            if file_size is not None
            else 0
        ),

        "crc": int(
            crc
            if crc is not None
            else 0
        ),

        "output_path": str(
            output_path
            if output_path is not None
            else ""
        ),

        "reclaimed": int(
            reclaimed
        ),

        "completed_at": (
            datetime.now(
                timezone.utc
            ).isoformat()
        ),
    }


# ==============================================================
# GET COMPLETION RECORD
# ==============================================================

def get_completed_record(
    state,
    filename
):
    """
    Return a completion record or None.
    """

    completed = state.get(
        "completed",
        {}
    )

    if not isinstance(
        completed,
        dict
    ):
        return None

    record = completed.get(
        filename
    )

    if not isinstance(
        record,
        dict
    ):
        return None

    return record


# ==============================================================
# BASIC COMPLETION CHECK
# ==============================================================

def is_completed(
    state,
    filename
):
    """
    Return True when a completion record exists.

    This remains for backward compatibility.

    New resume logic should prefer completed_file_is_valid().
    """

    return (
        get_completed_record(
            state,
            filename
        )
        is not None
    )


# ==============================================================
# CRC HELPER
# ==============================================================

def _calculate_crc32(
    path,
    chunk_size=1024 * 1024
):
    """
    Calculate CRC32 for an output file.

    Used only when strong resume verification is requested.
    """

    crc = 0

    with open(
        path,
        "rb"
    ) as file:

        while True:

            chunk = file.read(
                chunk_size
            )

            if not chunk:
                break

            crc = __import__(
                "zlib"
            ).crc32(
                chunk,
                crc
            )

    return crc & 0xFFFFFFFF


# ==============================================================
# RESUME VALIDATION
# ==============================================================

def completed_file_is_valid(
    state,
    filename,
    output_path,
    expected_size,
    expected_crc,
    verify_crc=True
):
    """
    Determine whether a previously completed output file can be
    safely skipped during resume.

    Validation includes:

        1. Completion record exists
        2. Output path exists and is a regular file
        3. Saved output path matches
        4. Current output size matches ZIP entry size
        5. Saved ZIP size matches current ZIP metadata
        6. Saved ZIP CRC matches current ZIP metadata
        7. Optional actual CRC verification of output data

    Returns:
        True only when all enabled checks pass.
    """

    record = get_completed_record(
        state,
        filename
    )

    if record is None:
        return False

    output_path = Path(
        output_path
    )

    # ----------------------------------------------------------
    # Output must be a regular file.
    # ----------------------------------------------------------

    try:

        if not output_path.is_file():
            return False

        if output_path.is_symlink():
            return False

    except OSError:

        return False

    # ----------------------------------------------------------
    # Output path must match the saved path.
    # ----------------------------------------------------------

    saved_output_path = record.get(
        "output_path"
    )

    if saved_output_path:

        try:

            if (
                Path(saved_output_path).resolve()
                != output_path.resolve()
            ):
                return False

        except OSError:

            return False

    # ----------------------------------------------------------
    # Actual output size.
    # ----------------------------------------------------------

    try:

        actual_size = (
            output_path.stat().st_size
        )

    except OSError:

        return False

    if actual_size != expected_size:
        return False

    # ----------------------------------------------------------
    # State metadata must match current ZIP metadata.
    # ----------------------------------------------------------

    saved_size = record.get(
        "file_size"
    )

    saved_crc = record.get(
        "crc"
    )

    if saved_size != expected_size:
        return False

    if saved_crc != expected_crc:
        return False

    # ----------------------------------------------------------
    # Optional full output CRC verification.
    # ----------------------------------------------------------

    if verify_crc:

        try:

            actual_crc = _calculate_crc32(
                output_path
            )

        except OSError:

            return False

        if actual_crc != expected_crc:
            return False

    return True


# ==============================================================
# COMPLETED COUNT
# ==============================================================

def get_completed_count(
    state
):
    """
    Return the number of completion records.
    """

    completed = state.get(
        "completed",
        {}
    )

    if not isinstance(
        completed,
        dict
    ):
        return 0

    return len(
        completed
    )


# ==============================================================
# COMPLETED COMPRESSED BYTES
# ==============================================================

def get_completed_compressed_bytes(
    state,
    member_map
):
    """
    Calculate compressed bytes represented by valid completion
    records.

    member_map should be:
        {filename: ZipInfo}

    The helper only counts records that still correspond to a
    current archive member with matching CRC and uncompressed size.
    """

    total = 0

    completed = state.get(
        "completed",
        {}
    )

    if not isinstance(
        completed,
        dict
    ):
        return 0

    for filename, record in completed.items():

        if not isinstance(
            record,
            dict
        ):
            continue

        info = member_map.get(
            filename
        )

        if info is None:
            continue

        if record.get(
            "file_size"
        ) != info.file_size:
            continue

        if record.get(
            "crc"
        ) != info.CRC:
            continue

        total += info.compress_size

    return total


# ==============================================================
# STATE CLEANUP
# ==============================================================

def remove_state(
    output_dir
):
    """
    Remove Reclaim's state file.

    This is intentionally explicit and is not called automatically.
    """

    state_path = get_state_path(
        output_dir
    )

    try:

        state_path.unlink(
            missing_ok=True
        )

    except OSError:

        pass
