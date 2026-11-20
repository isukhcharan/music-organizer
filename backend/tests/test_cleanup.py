import os
import shutil

import pytest

from musicorganizer.cleanup import apply_cleanup, plan_cleanup
from musicorganizer.journal import Journal


def touch(p, data=b"x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def names(dirs, root):
    return {str(d.path.relative_to(root.resolve())): d for d in dirs}


def test_classification(tmp_path):
    r = tmp_path / "in"
    (r / "empty").mkdir(parents=True)
    (r / "nested" / "a" / "b").mkdir(parents=True)                       # only empty sub-folders
    touch(r / "junk" / "album.nfo")
    touch(r / "junk" / "01.xml")
    touch(r / "junk" / "Thumbs.db")
    touch(r / "art" / "folder.jpg")
    touch(r / "art" / "album.nfo")
    touch(r / "keep_audio" / "song.mp3")
    touch(r / "keep_audio" / "cover.jpg")
    touch(r / "keep_video" / "clip.mp4")
    touch(r / "keep_unknown" / "notes.docx")
    touch(r / "mixed" / "gone" / "x.nfo")                                # removable child, kept parent
    touch(r / "mixed" / "song.flac")
    found = names(plan_cleanup(r), r)
    assert set(found) == {"empty", "nested", "junk", "art", os.path.join("mixed", "gone")}
    assert found["empty"].kind == "empty" and found["nested"].kind == "empty" and found["nested"].empty_subfolders == 2
    assert found["junk"].kind == "leftovers" and found["junk"].default_selected
    assert found["art"].has_images and not found["art"].default_selected    # cover art is not pre-ticked


def test_root_itself_is_never_offered_even_if_empty(tmp_path):
    r = tmp_path / "in"
    r.mkdir()
    assert plan_cleanup(r) == []
    (r / "sub").mkdir()
    assert [d.path.name for d in plan_cleanup(r)] == ["sub"] and r.exists()


def test_apply_removes_empty_trashes_leftovers_and_journals(tmp_path):
    r = tmp_path / "in"
    (r / "e" / "deep").mkdir(parents=True)
    touch(r / "j" / "a.nfo")
    touch(r / "keep" / "s.mp3")
    trashed = []
    j = Journal(tmp_path / "s.db")
    res = apply_cleanup(r, [str(r / "e"), str(r / "j")], j, "J",
                        trash=lambda p: (trashed.append(p), shutil.rmtree(p)))
    assert res == {"removed_empty": 1, "trashed": 1, "skipped": []}
    assert not (r / "e").exists() and trashed == [str((r / "j").resolve())]
    assert (r / "keep" / "s.mp3").exists() and r.exists()
    assert [(o["op_type"], o["status"]) for o in j.operations("J")] == [("cleanup", "done"), ("cleanup", "done")]
    j.close()


def test_stale_or_outside_selection_is_refused(tmp_path):
    r = tmp_path / "in"
    (r / "e").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    touch(r / "late" / "song.mp3")                      # a folder that holds a track
    res = apply_cleanup(r, [str(outside), str(r / "late"), str(r)], trash=None)
    assert res["removed_empty"] == 0 and len(res["skipped"]) == 3
    assert outside.exists() and (r / "late" / "song.mp3").exists() and r.exists()


def test_folder_that_gained_a_file_after_preview_is_not_deleted(tmp_path):
    r = tmp_path / "in"
    (r / "e").mkdir(parents=True)
    selected = [str(d.path) for d in plan_cleanup(r)]
    touch(r / "e" / "new_track.mp3")                    # the user drops a file in between preview and apply
    res = apply_cleanup(r, selected)
    assert res["removed_empty"] == 0 and (r / "e" / "new_track.mp3").exists()


def test_leftovers_need_recycle_bin_support(tmp_path):
    r = tmp_path / "in"
    touch(r / "j" / "a.nfo")
    res = apply_cleanup(r, [str(r / "j")], trash=None)
    assert res["trashed"] == 0 and "Recycle Bin" in res["skipped"][0]["reason"] and (r / "j" / "a.nfo").exists()


def test_symlinked_folders_are_never_followed(tmp_path):
    r = tmp_path / "in"
    target = tmp_path / "precious"
    touch(target / "a.nfo")
    r.mkdir()
    try:
        os.symlink(target, r / "link", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this system")
    assert plan_cleanup(r) == []
    assert (target / "a.nfo").exists()
