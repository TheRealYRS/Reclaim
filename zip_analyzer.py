import stat
import zipfile

from pathlib import Path, PurePosixPath


# ==============================================================
# RECLAIM ZIP ANALYZER / VALIDATOR
# ==============================================================

LOCAL_FILE_SIGNATURE = b"PK\x03\x04"
CENTRAL_FILE_SIGNATURE = b"PK\x01\x02"

SUPPORTED_COMPRESSION = {
    zipfile.ZIP_STORED,
    zipfile.ZIP_DEFLATED,
    zipfile.ZIP_BZIP2,
    zipfile.ZIP_LZMA,
}

ENCRYPTED_FLAG = 0x0001

# ZIP general-purpose bit used for data descriptors.
DATA_DESCRIPTOR_FLAG = 0x0008

# ZIP UTF-8 filename flag.
UTF8_FLAG = 0x0800

# Windows reserved device names.
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *{
        f"COM{i}"
        for i in range(1, 10)
    },
    *{
        f"LPT{i}"
        for i in range(1, 10)
    },
}


# ==============================================================
# MEMBER NAME VALIDATION
# ==============================================================

def validate_member_name(
    filename
):
    """
    Validate a ZIP member name before using it as a filesystem path.

    Rejects:

        - non-string names
        - empty names
        - null bytes
        - absolute paths
        - UNC paths
        - Windows drive paths
        - path traversal
        - Windows-invalid path components
        - Windows reserved device names
        - alternate data stream syntax
    """

    if not isinstance(
        filename,
        str
    ):

        raise ValueError(
            "ZIP member name is not a string"
        )

    if not filename:

        raise ValueError(
            "ZIP member has an empty filename"
        )

    if "\x00" in filename:

        raise ValueError(
            f"ZIP member contains a null byte: "
            f"{filename!r}"
        )

    normalized = filename.replace(
        "\\",
        "/"
    )

    # ----------------------------------------------------------
    # Absolute / drive / UNC paths
    # ----------------------------------------------------------

    if normalized.startswith("/"):

        raise ValueError(
            f"Unsafe absolute ZIP path: "
            f"{filename!r}"
        )

    if (
        len(normalized) >= 2
        and normalized[1] == ":"
    ):

        raise ValueError(
            f"Unsafe drive-qualified ZIP path: "
            f"{filename!r}"
        )

    if normalized.startswith("//"):

        raise ValueError(
            f"Unsafe UNC ZIP path: "
            f"{filename!r}"
        )

    path = PurePosixPath(
        normalized
    )

    # ----------------------------------------------------------
    # Traversal
    # ----------------------------------------------------------

    if any(
        part == ".."
        for part in path.parts
    ):

        raise ValueError(
            f"Path traversal detected: "
            f"{filename!r}"
        )

    # ----------------------------------------------------------
    # Validate individual Windows path components.
    # ----------------------------------------------------------

    for part in path.parts:

        if part in (
            "",
            ".",
        ):

            continue

        # Alternate data streams can create a file/stream target
        # rather than the intended ordinary file.
        if ":" in part:

            raise ValueError(
                f"Windows alternate-data-stream syntax is "
                f"not allowed: {filename!r}"
            )

        # Windows does not permit trailing spaces or periods in
        # ordinary path components.
        if part.endswith(
            " "
        ) or part.endswith(
            "."
        ):

            raise ValueError(
                f"Windows-invalid path component: "
                f"{filename!r}"
            )

        # Windows filenames cannot contain these characters.
        if any(
            char in part
            for char in '<>"/\\|?*'
        ):

            raise ValueError(
                f"Windows-invalid path component: "
                f"{filename!r}"
            )

        # Windows interprets names such as CON.txt as CON.
        stem = part.split(
            ".",
            1
        )[0].upper()

        if stem in WINDOWS_RESERVED_NAMES:

            raise ValueError(
                f"Windows reserved device name is not allowed: "
                f"{filename!r}"
            )

    return True


# ==============================================================
# SYMLINK DETECTION
# ==============================================================

def is_symlink(
    info
):
    """
    Detect a Unix symbolic-link ZIP entry from external attributes.
    """

    mode = (
        info.external_attr >> 16
    ) & 0xFFFF

    return (
        stat.S_IFMT(mode)
        == stat.S_IFLNK
    )


# ==============================================================
# MEMBER METADATA VALIDATION
# ==============================================================

def validate_member(
    info
):
    """
    Validate one ZIP member's metadata.
    """

    validate_member_name(
        info.filename
    )

    if is_symlink(
        info
    ):

        raise ValueError(
            f"Symbolic-link ZIP entry is not allowed: "
            f"{info.filename!r}"
        )

    if info.flag_bits & ENCRYPTED_FLAG:

        raise ValueError(
            f"Encrypted ZIP entries are not supported: "
            f"{info.filename!r}"
        )

    if (
        info.compress_type
        not in SUPPORTED_COMPRESSION
    ):

        raise ValueError(
            f"Unsupported compression method "
            f"{info.compress_type} for "
            f"{info.filename!r}"
        )

    if info.file_size < 0:

        raise ValueError(
            f"Invalid uncompressed size: "
            f"{info.filename!r}"
        )

    if info.compress_size < 0:

        raise ValueError(
            f"Invalid compressed size: "
            f"{info.filename!r}"
        )

    if info.header_offset < 0:

        raise ValueError(
            f"Invalid local header offset: "
            f"{info.filename!r}"
        )

    return True


# ==============================================================
# SAFE DATA RANGE
# ==============================================================

def get_data_range(
    archive,
    info
):
    """
    Return the exact byte range containing a member's compressed
    data and validate the local file header.

    This intentionally relies on the central-directory metadata
    supplied by Python's ZipFile while independently validating the
    local header at the recorded offset.
    """

    zip_path = Path(
        archive.filename
    )

    file_size = (
        zip_path.stat().st_size
    )

    archive_offset = getattr(
        archive,
        "_offset",
        0
    )

    # ZipFile.header_offset is relative to the archive start when
    # the ZIP contains a prepended stub/self-extractor.
    header_offset = (
        archive_offset
        + info.header_offset
    )

    # The central directory starts at archive.start_dir relative to
    # the beginning of the ZIP file.
    central_directory_start = (
        archive_offset
        + archive.start_dir
    )

    if header_offset < 0:

        raise ValueError(
            f"Negative local-header offset for "
            f"{info.filename!r}"
        )

    if (
        header_offset + 30
        > file_size
    ):

        raise ValueError(
            f"Local header extends beyond ZIP file "
            f"for {info.filename!r}"
        )

    # Local headers must occur before the central directory.
    if (
        header_offset
        >= central_directory_start
    ):

        raise ValueError(
            f"Local header is not before the central directory "
            f"for {info.filename!r}"
        )

    # ----------------------------------------------------------
    # Read local header
    # ----------------------------------------------------------

    archive.fp.seek(
        header_offset
    )

    header = archive.fp.read(
        30
    )

    if len(header) != 30:

        raise ValueError(
            f"Could not read complete local header "
            f"for {info.filename!r}"
        )

    if (
        header[:4]
        != LOCAL_FILE_SIGNATURE
    ):

        raise ValueError(
            f"Invalid local-file header signature "
            f"for {info.filename!r}"
        )

    local_flags = int.from_bytes(
        header[6:8],
        "little"
    )

    local_compression = int.from_bytes(
        header[8:10],
        "little"
    )

    filename_length = int.from_bytes(
        header[26:28],
        "little"
    )

    extra_length = int.from_bytes(
        header[28:30],
        "little"
    )

    header_end = (
        header_offset
        + 30
        + filename_length
        + extra_length
    )

    if (
        header_end
        > file_size
    ):

        raise ValueError(
            f"Local header fields extend beyond "
            f"ZIP file for {info.filename!r}"
        )

    if (
        header_end
        > central_directory_start
    ):

        raise ValueError(
            f"Local header overlaps the central directory "
            f"for {info.filename!r}"
        )

    # ----------------------------------------------------------
    # Local/central flags
    #
    # The values should agree. Bit 3 is valid for data descriptors
    # and is expected to remain set in both records.
    # ----------------------------------------------------------

    if local_flags != info.flag_bits:

        raise ValueError(
            f"Local/central flag mismatch for "
            f"{info.filename!r}"
        )

    if (
        local_compression
        != info.compress_type
    ):

        raise ValueError(
            f"Local/central compression mismatch for "
            f"{info.filename!r}"
        )

    # ----------------------------------------------------------
    # Local filename
    # ----------------------------------------------------------

    archive.fp.seek(
        header_offset + 30
    )

    local_filename_bytes = archive.fp.read(
        filename_length
    )

    if (
        len(local_filename_bytes)
        != filename_length
    ):

        raise ValueError(
            f"Could not read local filename "
            f"for {info.filename!r}"
        )

    try:

        if local_flags & UTF8_FLAG:

            local_filename = (
                local_filename_bytes.decode(
                    "utf-8"
                )
            )

        else:

            local_filename = (
                local_filename_bytes.decode(
                    "cp437"
                )
            )

    except UnicodeDecodeError as error:

        raise ValueError(
            f"Invalid local filename encoding "
            f"for {info.filename!r}"
        ) from error

    if (
        local_filename
        != info.filename
    ):

        raise ValueError(
            f"Local/central filename mismatch: "
            f"{info.filename!r}"
        )

    # ----------------------------------------------------------
    # Compressed data
    # ----------------------------------------------------------

    data_start = header_end

    data_end = (
        data_start
        + info.compress_size
    )

    if (
        data_end
        < data_start
    ):

        raise ValueError(
            f"Compressed data range overflow for "
            f"{info.filename!r}"
        )

    # The actual compressed member data must finish before the
    # central directory. A data descriptor, if present, may follow
    # the compressed data and is intentionally not included here.
    if (
        data_end
        > central_directory_start
    ):

        raise ValueError(
            f"Compressed data overlaps the central directory "
            f"for {info.filename!r}"
        )

    if (
        data_end
        > file_size
    ):

        raise ValueError(
            f"Compressed data extends beyond "
            f"ZIP file for {info.filename!r}"
        )

    return (
        data_start,
        data_end
    )


# ==============================================================
# FILE DISCOVERY
# ==============================================================

def get_files(
    zip_path
):
    """
    Return all non-directory members after validating the complete
    archive structure relevant to destructive extraction.
    """

    zip_path = Path(
        zip_path
    )

    if not zip_path.is_file():

        raise FileNotFoundError(
            f"ZIP file does not exist: {zip_path}"
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

            return files

    except zipfile.BadZipFile:
        raise


# ==============================================================
# ARCHIVE VALIDATION
# ==============================================================

def validate_archive_members(
    archive,
    files=None
):
    """
    Validate all file members before destructive operations.

    Checks:

        - duplicate names
        - Windows path collisions
        - unsafe paths
        - symlinks
        - encrypted entries
        - unsupported compression
        - local headers
        - compressed-data bounds
        - overlapping compressed ranges
        - compressed data before central directory
    """

    if files is None:

        files = [
            info
            for info in archive.infolist()
            if not info.is_dir()
        ]

    names = set()

    # Case-insensitive normalized names are important because the
    # application targets Windows, whose normal filesystem behavior
    # is case-insensitive.
    normalized_names = {}

    ranges = []

    for info in files:

        validate_member(
            info
        )

        filename = info.filename

        # ------------------------------------------------------
        # Exact duplicate
        # ------------------------------------------------------

        if filename in names:

            raise ValueError(
                "Duplicate ZIP filename detected: "
                f"{filename!r}"
            )

        names.add(
            filename
        )

        # ------------------------------------------------------
        # Windows-equivalent duplicate
        #
        # Backslashes are normalized because the extractor treats
        # them as path separators on Windows.
        # ------------------------------------------------------

        normalized = (
            filename
            .replace(
                "\\",
                "/"
            )
            .casefold()
            .rstrip(". ")
        )

        previous = normalized_names.get(
            normalized
        )

        if previous is not None:

            raise ValueError(
                "ZIP contains Windows-equivalent "
                "filename collisions: "
                f"{previous!r} and {filename!r}"
            )

        normalized_names[
            normalized
        ] = filename

        # ------------------------------------------------------
        # Compressed range
        # ------------------------------------------------------

        data_start, data_end = (
            get_data_range(
                archive,
                info
            )
        )

        ranges.append(
            (
                data_start,
                data_end,
                filename
            )
        )

    # ----------------------------------------------------------
    # Check compressed-data range overlap.
    # ----------------------------------------------------------

    non_empty = [
        item
        for item in ranges
        if item[0] < item[1]
    ]

    non_empty.sort(
        key=lambda item: item[0]
    )

    previous_end = None
    previous_name = None

    for (
        start,
        end,
        filename
    ) in non_empty:

        if (
            previous_end is not None
            and start < previous_end
        ):

            raise ValueError(
                "Overlapping compressed data ranges detected "
                f"between {previous_name!r} and "
                f"{filename!r}"
            )

        previous_end = end
        previous_name = filename

    return True


# ==============================================================
# ZIP ANALYSIS
# ==============================================================

def analyze_zip(
    zip_path
):
    """
    Analyze every non-directory member in a ZIP archive.

    The returned metadata is suitable for UI previews, estimates,
    and diagnostics.
    """

    results = []

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

        for info in files:

            data_start, data_end = (
                get_data_range(
                    archive,
                    info
                )
            )

            results.append(
                {
                    "filename": info.filename,

                    "compressed_size": (
                        info.compress_size
                    ),

                    "uncompressed_size": (
                        info.file_size
                    ),

                    "header_offset": (
                        info.header_offset
                    ),

                    "data_start": (
                        data_start
                    ),

                    "data_end": (
                        data_end
                    ),

                    "crc": info.CRC,

                    "compression": (
                        info.compress_type
                    ),

                    "flags": info.flag_bits,

                    "is_directory": (
                        info.is_dir()
                    ),

                    "is_symlink": (
                        is_symlink(info)
                    ),
                }
            )

    return results


# ==============================================================
# ARCHIVE SUMMARY
# ==============================================================

def get_archive_summary(
    zip_path
):
    """
    Return aggregate archive metrics.

    Useful for the future Analyze screen.

    Returns:
        {
            "file_count": ...,
            "logical_size": ...,
            "compressed_bytes": ...,
            "uncompressed_bytes": ...,
            "compression_ratio": ...,
            "estimated_reclaimable_bytes": ...
        }
    """

    results = analyze_zip(
        zip_path
    )

    logical_size = Path(
        zip_path
    ).stat().st_size

    compressed_bytes = sum(
        item["compressed_size"]
        for item in results
    )

    uncompressed_bytes = sum(
        item["uncompressed_size"]
        for item in results
    )

    if compressed_bytes:

        compression_ratio = (
            uncompressed_bytes
            / compressed_bytes
        )

    else:

        compression_ratio = 0.0

    return {
        "file_count": len(
            results
        ),

        "logical_size": int(
            logical_size
        ),

        "compressed_bytes": int(
            compressed_bytes
        ),

        "uncompressed_bytes": int(
            uncompressed_bytes
        ),

        "compression_ratio": float(
            compression_ratio
        ),

        "estimated_reclaimable_bytes": int(
            compressed_bytes
        ),
    }
