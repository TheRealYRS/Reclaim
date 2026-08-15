import ctypes
import ctypes.wintypes as wintypes
from pathlib import Path


# ==================================================
# Windows constants
# ==================================================

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

FSCTL_SET_SPARSE = 0x000900C4
FSCTL_SET_ZERO_DATA = 0x000980C8

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000

FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
FILE_SHARE_DELETE = 0x00000004

OPEN_EXISTING = 3


# ==================================================
# Windows structure
# ==================================================

class FILE_ZERO_DATA_INFORMATION(
    ctypes.Structure
):

    _fields_ = [
        (
            "FileOffset",
            ctypes.c_longlong
        ),
        (
            "BeyondFinalZero",
            ctypes.c_longlong
        ),
    ]


# ==================================================
# Windows API
# ==================================================

kernel32 = ctypes.WinDLL(
    "kernel32",
    use_last_error=True
)


CreateFileW = kernel32.CreateFileW

CreateFileW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.LPVOID,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.HANDLE,
]

CreateFileW.restype = wintypes.HANDLE


DeviceIoControl = kernel32.DeviceIoControl

DeviceIoControl.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPVOID,
    wintypes.DWORD,
    wintypes.LPVOID,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    wintypes.LPVOID,
]

DeviceIoControl.restype = wintypes.BOOL


CloseHandle = kernel32.CloseHandle

CloseHandle.argtypes = [
    wintypes.HANDLE
]

CloseHandle.restype = wintypes.BOOL


# ==================================================
# Open file
# ==================================================

def open_file(path):

    path = Path(path)

    if not path.is_file():
        raise FileNotFoundError(
            f"File does not exist: {path}"
        )

    handle = CreateFileW(
        str(path),
        GENERIC_READ | GENERIC_WRITE,
        (
            FILE_SHARE_READ
            | FILE_SHARE_WRITE
            | FILE_SHARE_DELETE
        ),
        None,
        OPEN_EXISTING,
        0,
        None,
    )

    if handle == INVALID_HANDLE_VALUE:

        raise ctypes.WinError(
            ctypes.get_last_error()
        )

    return handle


# ==================================================
# Make sparse
# ==================================================

def make_sparse(path):

    path = Path(path)

    handle = open_file(path)

    try:

        returned = wintypes.DWORD()

        success = DeviceIoControl(
            handle,
            FSCTL_SET_SPARSE,
            None,
            0,
            None,
            0,
            ctypes.byref(returned),
            None,
        )

        if not success:

            raise ctypes.WinError(
                ctypes.get_last_error()
            )

    finally:

        CloseHandle(handle)


# ==================================================
# Punch hole
# ==================================================

def punch_hole(path, start, end):

    path = Path(path)

    if not path.is_file():
        raise FileNotFoundError(
            f"File does not exist: {path}"
        )

    try:

        start = int(start)
        end = int(end)

    except (TypeError, ValueError) as error:

        raise ValueError(
            "Hole-punch offsets must be integers."
        ) from error

    file_size = path.stat().st_size

    # Empty range: nothing to reclaim.
    if start == end:
        return 0

    if start < 0:
        raise ValueError(
            f"Invalid hole-punch start: {start}"
        )

    if end <= start:
        raise ValueError(
            f"Invalid hole-punch range: "
            f"{start} -> {end}"
        )

    if end > file_size:
        raise ValueError(
            f"Hole-punch range exceeds file size: "
            f"{start} -> {end}, "
            f"file size={file_size}"
        )

    handle = open_file(path)

    try:

        zero_data = FILE_ZERO_DATA_INFORMATION(
            start,
            end,
        )

        returned = wintypes.DWORD()

        success = DeviceIoControl(
            handle,
            FSCTL_SET_ZERO_DATA,
            ctypes.byref(zero_data),
            ctypes.sizeof(zero_data),
            None,
            0,
            ctypes.byref(returned),
            None,
        )

        if not success:

            raise ctypes.WinError(
                ctypes.get_last_error()
            )

    finally:

        CloseHandle(handle)

    return end - start


# ==================================================
# Physical allocation
# ==================================================

def get_allocated_size(path):

    path = Path(path)

    if not path.is_file():
        raise FileNotFoundError(
            f"File does not exist: {path}"
        )

    GetCompressedFileSizeW = (
        kernel32.GetCompressedFileSizeW
    )

    GetCompressedFileSizeW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]

    GetCompressedFileSizeW.restype = (
        wintypes.DWORD
    )

    high = wintypes.DWORD()

    low = GetCompressedFileSizeW(
        str(path),
        ctypes.byref(high)
    )

    if low == 0xFFFFFFFF:

        error = ctypes.get_last_error()

        if error != 0:

            raise ctypes.WinError(
                error
            )

    return (
        (high.value << 32)
        | low
    )