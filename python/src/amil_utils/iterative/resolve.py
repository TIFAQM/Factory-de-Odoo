"""Conflict resolution operations for iterative refinement.

Provides:
- ``resolve_status`` — list pending conflict files from ``.amil-pending/``.
- ``resolve_accept_new`` — accept the newly generated version of a file.
- ``resolve_keep_mine`` — keep the current version, discard the pending file.
- ``resolve_accept_all`` — accept all pending files at once.

All functions operate on the module directory and its ``.amil-pending/``
subdirectory.  After each resolution operation, empty pending directories
are cleaned up automatically.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from amil_utils.manifest import (
    compute_file_sha256,
    load_manifest,
    save_manifest,
)

logger = logging.getLogger("amil.iterative.resolve")

PENDING_DIR_NAME = ".amil-pending"


def _cleanup_pending(module_dir: Path) -> None:
    """Remove ``.amil-pending/`` if it is empty after resolution.

    Walks the directory tree bottom-up, removing empty subdirectories
    first, then the root pending directory if nothing remains.
    """
    pending_dir = module_dir / PENDING_DIR_NAME
    if not pending_dir.exists():
        return

    # Remove empty subdirectories bottom-up
    for dirpath in sorted(pending_dir.rglob("*"), reverse=True):
        if dirpath.is_dir():
            try:
                dirpath.rmdir()  # Only succeeds if empty
            except OSError as exc:
                logger.debug("Could not remove subdirectory %s: %s", dirpath, exc)

    # Remove the root pending directory if empty
    try:
        pending_dir.rmdir()
        logger.info("Cleaned up empty %s", PENDING_DIR_NAME)
    except OSError as exc:
        logger.debug("Pending directory not empty, keeping: %s", exc)


def resolve_status(module_dir: Path) -> list[str]:
    """List all pending conflict files from ``.amil-pending/``.

    Returns a list of relative paths (relative to the pending directory).
    Returns an empty list when the pending directory does not exist or is
    empty.
    """
    pending_dir = module_dir / PENDING_DIR_NAME
    if not pending_dir.exists():
        return []

    result: list[str] = []
    for file_path in sorted(pending_dir.rglob("*")):
        if file_path.is_file():
            rel = str(file_path.relative_to(pending_dir))
            result.append(rel)

    return result


def resolve_accept_new(module_dir: Path, relative_path: str) -> bool:
    """Accept the pending version of a file, replacing the module copy.

    For files in the ``removed/`` subdirectory, acceptance means confirming
    deletion: the file is removed from the module directory.

    For regular files, the pending version is copied to the module directory,
    overwriting the existing file.  The manifest artifact entry's SHA256 is
    updated to reflect the new content.

    Returns ``True`` if the file was resolved, ``False`` if the pending
    file was not found.
    """
    pending_dir = module_dir / PENDING_DIR_NAME
    pending_file = pending_dir / relative_path

    if not pending_file.exists():
        return False

    # Check if this is a removal confirmation
    if relative_path.startswith("removed/"):
        # The actual module file path is after "removed/"
        actual_rel = relative_path[len("removed/"):]
        actual_file = module_dir / actual_rel
        if actual_file.exists():
            actual_file.unlink()
            logger.info("Deleted module file: %s", actual_rel)
    else:
        # Copy pending file to module directory
        dest = module_dir / relative_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pending_file, dest)
        logger.info("Accepted new version: %s", relative_path)

        # Update manifest artifact hash
        _update_manifest_hash(module_dir, relative_path)

    # Remove the pending file
    pending_file.unlink()
    _cleanup_pending(module_dir)
    return True


def resolve_keep_mine(module_dir: Path, relative_path: str) -> bool:
    """Keep the current module file, discarding the pending version.

    For files in the ``removed/`` subdirectory, keeping means the file
    stays in the module directory (removal is cancelled).

    Returns ``True`` if the pending file was removed, ``False`` if it
    was not found.
    """
    pending_dir = module_dir / PENDING_DIR_NAME
    pending_file = pending_dir / relative_path

    if not pending_file.exists():
        return False

    pending_file.unlink()
    logger.info("Kept current version, discarded pending: %s", relative_path)

    _cleanup_pending(module_dir)
    return True


def resolve_accept_all(module_dir: Path) -> int:
    """Accept all pending files at once.

    Calls ``resolve_accept_new()`` for each pending file.

    Returns the count of resolved files.
    """
    pending_files = resolve_status(module_dir)
    if not pending_files:
        return 0

    count = 0
    for rel_path in pending_files:
        if resolve_accept_new(module_dir, rel_path):
            count += 1

    return count


def _update_manifest_hash(module_dir: Path, relative_path: str) -> None:
    """Update the manifest's artifact entry SHA256 for a resolved file.

    Loads the manifest, finds the matching artifact entry, updates
    its SHA256, and saves the manifest back.  Does nothing if the
    manifest or entry is not found.
    """
    manifest = load_manifest(module_dir)
    if manifest is None:
        return

    file_path = module_dir / relative_path
    if not file_path.exists():
        return

    try:
        new_sha = compute_file_sha256(file_path)
    except OSError:
        return

    # Find and update the artifact entry
    updated = False
    new_files = []
    for entry in manifest.artifacts.files:
        if entry.path == relative_path:
            from amil_utils.manifest import ArtifactEntry
            new_files.append(ArtifactEntry(path=entry.path, sha256=new_sha))
            updated = True
        else:
            new_files.append(entry)

    if updated:
        manifest.artifacts.files = new_files
        save_manifest(manifest, module_dir)
        logger.debug("Updated manifest hash for %s", relative_path)
