"""Tidy the input folder after files have been moved out of it.

Safety rules (this deletes things, so they are strict):
  * only folders *inside* the input folder are ever removed; the input folder itself never is,
  * a folder is removable only if everything in it is removable: empty sub-folders, known leftover files
    (.nfo/.xml/Thumbs.db/...) and cover images. Audio, video, documents or any unknown file type make the
    folder (and its parents) permanent,
  * symbolic links are never followed or removed,
  * truly empty folders are removed with rmdir; folders holding leftovers go to the Recycle Bin / Trash so
    nothing is lost for good,
  * the plan is recomputed at apply time, so a stale selection can never delete something new.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .journal import Journal

JUNK_EXT = {".nfo", ".xml", ".txt", ".url", ".log", ".db", ".ini", ".m3u", ".m3u8", ".cue", ".sfv", ".md5"}
JUNK_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}


@dataclass
class CleanupDir:
    path: Path
    kind: str                                  # "empty" | "leftovers"
    files: list[str] = field(default_factory=list)   # relative names of the leftover files inside
    has_images: bool = False
    empty_subfolders: int = 0

    @property
    def default_selected(self) -> bool:
        """Empty folders and pure-junk folders are pre-ticked; anything with cover images is left for the user."""
        return not self.has_images


def _is_leftover(p: Path) -> bool:
    return p.name.lower() in JUNK_NAMES or p.suffix.lower() in JUNK_EXT or p.suffix.lower() in IMAGE_EXT


def plan_cleanup(root: Path) -> list[CleanupDir]:
    root = Path(root).resolve()
    removable: dict[Path, bool] = {}
    files_in: dict[Path, list[Path]] = {}
    subdirs_in: dict[Path, int] = {}

    def visit(d: Path) -> bool:
        ok, files, nsub = True, [], 0
        try:
            entries = sorted(os.scandir(d), key=lambda e: e.name.lower())
        except OSError:
            removable[d] = False
            return False
        for e in entries:
            p = Path(e.path)
            if e.is_symlink():
                ok = False                      # never follow or remove links
            elif e.is_dir(follow_symlinks=False):
                if visit(p):
                    files += files_in[p]
                    nsub += 1 + subdirs_in[p]
                else:
                    ok = False
            elif e.is_file(follow_symlinks=False) and _is_leftover(p):
                files.append(p)
            else:
                ok = False                      # audio, video, documents, unknown: keep
        removable[d], files_in[d], subdirs_in[d] = ok, files, nsub
        return ok

    visit(root)
    found: list[CleanupDir] = []

    def pick(d: Path) -> None:
        for e in sorted(os.scandir(d), key=lambda e: e.name.lower()):
            p = Path(e.path)
            if e.is_dir(follow_symlinks=False) and not e.is_symlink():
                if removable.get(p):
                    fs = files_in[p]
                    found.append(CleanupDir(
                        p, "leftovers" if fs else "empty", [str(f.relative_to(p)) for f in fs],
                        any(f.suffix.lower() in IMAGE_EXT for f in fs), subdirs_in[p]))
                else:
                    pick(p)

    pick(root)
    return found


def apply_cleanup(root: Path, selected: list[str], journal: Journal | None = None, job_id: str = "",
                  trash: Callable[[str], None] | None = None) -> dict:
    """Remove the selected folders if they are still removable. Returns what was done and what was skipped."""
    root = Path(root).resolve()
    allowed = {str(d.path): d for d in plan_cleanup(root)}
    result: dict = {"removed_empty": 0, "trashed": 0, "skipped": []}
    for raw in selected:
        key = str(Path(raw).resolve())
        d = allowed.get(key)
        if d is None:
            result["skipped"].append({"path": raw, "reason": "not removable (changed since the preview, or outside the input folder)"})
            continue
        op = journal.begin(job_id, "cleanup", d.path, d.path) if journal else None
        try:
            if d.kind == "empty":
                for dp, _dn, _fn in os.walk(d.path, topdown=False):
                    os.rmdir(dp)
                result["removed_empty"] += 1
            elif trash is None:
                raise RuntimeError("Recycle Bin support (send2trash) is not available")
            else:
                trash(str(d.path))
                result["trashed"] += 1
            if journal:
                journal.finish(op, "done")
        except Exception as exc:
            if journal:
                journal.finish(op, "failed", f"{type(exc).__name__}: {exc}")
            result["skipped"].append({"path": raw, "reason": f"{type(exc).__name__}: {exc}"})
    return result


def default_trash() -> Callable[[str], None] | None:
    try:
        from send2trash import send2trash
    except Exception:
        return None
    return send2trash
