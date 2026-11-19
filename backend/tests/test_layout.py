from pathlib import Path

from musicorganizer.layout import (
    destination_dir,
    destination_filename,
    infer_album_artists,
    is_standalone_single,
)

OUT = Path("OUT")


def row(**kw):
    base = {"status": "AUTO_MATCH", "source": r"in\Album A\t.mp3", "local_album": "", "matched_album": "",
            "matched_artist": "", "local_artist": "", "detail_status": ""}
    base.update(kw)
    return base


def test_single_detected_from_both_detail_formats():
    assert is_standalone_single(row(detail_status="Verified in 1-track release; provider artist and title checked"))
    assert is_standalone_single(row(detail_status="Matched against album tracklist (1 tracks); album coverage 100%."))


def test_lone_track_of_multitrack_album_is_not_a_single():
    # "Wah Kya Nazare": track 2 of 9, only local track from the album (v14 fix).
    r = row(detail_status="Matched against album tracklist (9 tracks); album coverage 100%.",
            matched_album="8 Chances", matched_artist="Harnoor")
    assert not is_standalone_single(r)
    infer_album_artists([r])
    assert destination_dir(r, OUT) == OUT / "Harnoor" / "8 Chances"


def test_single_goes_to_artist_singles():
    r = row(detail_status="Verified in 1-track release", matched_album="Photo", matched_artist="Singga")
    infer_album_artists([r])
    assert destination_dir(r, OUT) == OUT / "Singga" / "Singles"


def test_soundtrack_and_compilation_layout():
    ost = row(detail_status="Matched against album tracklist (6 tracks)",
              matched_album="Ardaas (Original Motion Picture Soundtrack)", matched_artist="Jatinder Shah")
    comp = [row(source=r"in\Mix\a.mp3", detail_status="Matched against album tracklist (4 tracks)",
                matched_album="Mix Tape", matched_artist=a) for a in ("Artist One", "Artist Two")]
    infer_album_artists([ost, *comp])
    assert destination_dir(ost, OUT) == OUT / "Soundtracks" / "Ardaas (Original Motion Picture Soundtrack)"
    assert destination_dir(comp[0], OUT) == OUT / "Compilations" / "Mix Tape"


def test_loose_copy_of_an_album_track_follows_the_album_folder():
    detail = "Matched against album tracklist (7 tracks); album coverage 100%."
    in_album = [row(source=r"in\Album - P\t%d.mp3" % i, matched_album="PANJ-AAB, Vol. 1", detail_status=detail,
                    matched_artist=a) for i, a in enumerate(["Amrit Maan", "Mehtab Virk", "Nit Mann"])]
    loose = row(source=r"in\Proposal.mp3", matched_album="PANJ-AAB, Vol. 1", matched_artist="Mehtab Virk",
                detail_status="Individual song fallback: Verified in 7-track release; provider artist and title checked")
    infer_album_artists([*in_album, loose])
    assert destination_dir(loose, OUT) == destination_dir(in_album[1], OUT) == OUT / "Compilations" / "PANJ-AAB, Vol. 1"


def test_unrelated_singles_with_the_same_title_are_not_merged_into_a_compilation():
    a = row(source=r"in\A\x.mp3", matched_album="Intro", matched_artist="Artist One",
            detail_status="Verified in 1-track release")
    b = row(source=r"in\B\y.mp3", matched_album="Intro", matched_artist="Artist Two",
            detail_status="Verified in 1-track release")
    infer_album_artists([a, b])
    assert destination_dir(a, OUT) == OUT / "Artist One" / "Singles"
    assert destination_dir(b, OUT) == OUT / "Artist Two" / "Singles"


def test_non_auto_rows_are_not_given_album_artists():
    r = row(status="REVIEW", matched_artist="X")
    infer_album_artists([r])
    assert "resolved_albumartist" not in r


def test_filename_pads_track_and_sanitises_title():
    assert destination_filename(2, 'Wah: Kya?', ".MP3", "stem") == "02 - Wah Kya.mp3"


def test_release_size_field_is_used_when_detail_text_was_overwritten():
    from musicorganizer.layout import release_track_count
    assert release_track_count({"release_tracks": 7, "detail_status": "Same release track as another file"}) == 7
    assert release_track_count({"detail_status": "Verified in 3-track release"}) == 3     # legacy reports
    assert release_track_count({"detail_status": ""}) is None
