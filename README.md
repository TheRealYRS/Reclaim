# Reclaim

## Extract. Verify. Reclaim.

Reclaim is a Windows utility for extracting ZIP archives, verifying
their contents, and reclaiming the physical disk space occupied by
their compressed data.

Instead of manually extracting an archive and then deciding what to
do with the original ZIP, Reclaim combines the workflow into one
application:

**Analyze → Extract → Verify → Reclaim**

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

## How It Works

Reclaim extracts each ZIP member into a temporary file.

The extracted file is verified using:

- Expected uncompressed size
- CRC32

Only after successful verification does Reclaim reclaim the physical
storage occupied by that member's compressed data.

The ZIP file remains present, but reclaimed compressed ranges no longer
consume the same physical disk allocation.

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

The reclaim value shown during analysis is an estimate. Actual physical
space reclaimed depends on filesystem allocation and storage behavior.

## Security

Reclaim validates archive structure before destructive operations.

It checks for:

- Path traversal
- Absolute paths
- Drive-qualified paths
- UNC paths
- Windows-invalid filenames
- Windows reserved device names
- Symbolic links
- Encrypted ZIP entries
- Unsupported compression methods
- Invalid local headers
- Invalid compressed-data ranges
- Overlapping compressed ranges
- Windows-equivalent filename collisions

## Resume Support

Reclaim maintains extraction state so interrupted operations can resume.

Completed outputs are verified before they are considered complete.
This prevents the application from blindly trusting a previous state file.

## Existing Files

When an output file already exists, Reclaim can:

- Skip the file
- Rename automatically
- Replace an existing regular file
- Cancel extraction

Rename mode produces names such as:

```text
document.pdf
document (1).pdf
document (2).pdf
