# Reclaim

## Extract. Verify. Reclaim.

Reclaim is a Windows utility for extracting ZIP archives, verifying their
contents, and reclaiming the physical disk space occupied by their
compressed data.

Instead of manually extracting an archive and then deciding what to do with
the original ZIP, Reclaim combines the workflow into one application:

**Analyze → Extract → Verify → Reclaim**

---

## Why Reclaim?

Extracting a ZIP normally creates a second copy of the data while leaving
the original compressed archive fully allocated on disk.

That is often useful, but when the extracted files are now the desired end
state, the original compressed payload can become redundant.

Reclaim is designed for that workflow.

After a ZIP member has been successfully extracted and verified, Reclaim can
release the physical storage allocated to that member's compressed data
while keeping the ZIP file itself present.

The result is a ZIP whose logical file remains present while its processed
compressed ranges no longer consume the same amount of physical storage.

---

## What Makes Reclaim Different?

The underlying filesystem technique used by Reclaim is established:
sparse files and filesystem-level hole punching can release physical storage
from selected byte ranges without changing a file's logical length.

Reclaim's focus is the end-to-end workflow:

**safe extraction + verification + resumable state + collision handling +
physical reclamation**

The project is not presented as the invention of hole punching itself.
Instead, Reclaim applies that established technique to a focused ZIP
extraction workflow.

---

## Features

- ZIP archive analysis and preview
- Secure extraction
- CRC32 and file-size verification
- Resume support
- Safe cancellation
- Existing-file collision handling
- Skip existing files
- Automatic rename support
- Replace existing files
- Drag-and-drop ZIP archives
- Light and dark themes
- Extraction progress and statistics
- Physical disk-space reclamation
- Windows-focused filename and path validation
- Atomic output placement
- Atomic resume-state saving

---

## How It Works

For each ZIP member, Reclaim follows a verified workflow:

1. Validate the archive and the member metadata.
2. Validate the destination path.
3. Extract into a temporary file.
4. Verify the expected uncompressed size.
5. Verify the ZIP CRC32.
6. Atomically place the verified output.
7. Convert the archive to sparse storage when necessary.
8. Punch the processed compressed byte range.
9. Measure the resulting physical allocation.
10. Save resume state only after successful processing.

This means the original compressed data is not reclaimed before successful
extraction and verification.

---

## Archive Analysis

Before extraction, Reclaim can analyze an archive and display:

- Number of files
- Archive size
- Compressed data
- Uncompressed data
- Compression ratio
- Estimated reclaimable data
- Archive members
- Existing-file collisions

The reclaim figure shown during analysis is an estimate.

Actual physical space reclaimed depends on filesystem allocation,
storage-device behavior, and filesystem block/cluster allocation.

---

## Existing-File Handling

When an output file already exists, Reclaim gives the user explicit control.

### Skip

Leaves the existing file untouched.

### Rename

Creates a non-conflicting name such as:

```text
document.pdf
document (1).pdf
document (2).pdf
```

If the original ZIP member has already been successfully processed and its
compressed data has already been reclaimed, Reclaim can duplicate the
previously verified extracted output instead of attempting to decompress
reclaimed ZIP data.

### Replace

Replaces an existing regular file when explicitly selected.

Symbolic-link destinations and directories are not blindly replaced.

### Cancel

Stops before processing the colliding member.

---

## Resume Support

Reclaim keeps extraction state separately from the ZIP archive.

Completed outputs are verified against:

- Expected file size
- Expected CRC32
- Recorded output path
- Actual output file contents

This prevents Reclaim from blindly trusting a previous completion record.

The state format is versioned and can migrate older Reclaim state data.

---

## Security

Reclaim performs a structural preflight before destructive operations.

It checks for:

- Path traversal
- Absolute paths
- Drive-qualified paths
- UNC paths
- Windows-invalid filename components
- Windows reserved device names
- Symbolic links
- Encrypted ZIP entries
- Unsupported compression methods
- Invalid local-file headers
- Local/central-directory metadata mismatches
- Invalid compressed-data ranges
- Central-directory overlap
- Overlapping compressed ranges
- Windows-equivalent filename collisions

Extraction targets are also checked to avoid traversing symbolic-link
directories or escaping the chosen output folder.

---

## Reclamation

Reclaim does not simply delete the ZIP file.

The ZIP remains present, but successfully processed compressed data can be
released from physical storage using sparse-file and hole-punching behavior.

The logical file size may therefore remain similar while physical disk
allocation becomes substantially smaller.

Example:

```text
Before
Archive logical size:       162.84 MB
Physical allocation:       162.84 MB

After processing
Archive logical size:       162.84 MB
Physical allocation:         substantially lower
Extracted output:           retained separately
```

The exact result depends on the filesystem.

---

## Requirements

### End Users

The packaged Windows release does not require Python to be installed.

### Development

- Windows 10 or Windows 11
- Python 3.11
- CustomTkinter
- tkinterdnd2
- Pillow
- PyInstaller for packaging
- Inno Setup for the Windows installer

---

## Running From Source

Create a virtual environment:

```powershell
python -m venv .venv
```

Install development dependencies:

```powershell
python -m pip install -r requirements.txt
```

Run Reclaim:

```powershell
python smart_gui.py
```

---

## Testing

Compile-check all Python files:

```powershell
Get-ChildItem -Filter *.py | ForEach-Object {
    python -m py_compile $_.FullName
}
```

Run the original regression suite:

```powershell
python test_smart_extract.py
```

Run the v2.1 feature suite:

```powershell
python test_v21_features.py
```

The feature tests cover:

- Archive analysis
- Collision detection
- Skip behavior
- Rename behavior
- Replace behavior
- Cancel behavior

---

## Building

Reclaim is packaged with PyInstaller.

The repository also includes an Inno Setup script for creating the Windows
installer.

Typical release artifacts:

```text
Reclaim-1.0.0-Setup.exe
Reclaim-1.0.0-Portable.zip
```

The installer provides a normal Windows installation experience, while the
portable build can be extracted and run without installation.

---

## Project Structure

```text
Reclaim/
├── smart_gui.py
├── smart_extract.py
├── safe_extract.py
├── state_manager.py
├── zip_analyzer.py
├── hole_punch.py
│
├── test_smart_extract.py
├── test_v21_features.py
│
├── reclaim.ico
├── version_info.txt
├── Reclaim.spec
├── installer.iss
├── requirements.txt
├── README.md
├── LICENSE
└── .gitignore
```

---

## Version

**Reclaim 1.0.0**

**Extract. Verify. Reclaim.**

---

## Author

**Yash Raj Sondhi**

Reclaim is a personal Windows software project focused on secure,
verified, space-efficient ZIP extraction.

---

## License

Reclaim is released under the MIT License.

See [`LICENSE`](LICENSE) for the full license text.
