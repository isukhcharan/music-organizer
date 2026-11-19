from mutagen import File as MutagenFile

from musicorganizer.tags import fill_blank_tags, read_local


def easy(path):
    return MutagenFile(path, easy=True)


def test_read_local_uses_filename_when_title_blank(make_mp3):
    p = make_mp3("Lamborghini.mp3", artist="Jass")
    local = read_local(p)
    assert local["title"] == "Lamborghini"
    assert local["artist"] == "Jass"
    assert local["album"] == ""


def test_read_local_folder_hint_ignores_generic_folder_names(make_mp3, tmp_path):
    assert read_local(make_mp3("a.mp3", folder=tmp_path / "Incoming"))["folder_album_hint"] == ""
    assert read_local(make_mp3("b.mp3", folder=tmp_path / "Album - Moosetape"))["folder_album_hint"] == "Moosetape"


def test_file_directly_in_input_root_gets_no_folder_hint(make_mp3, tmp_path):
    root = tmp_path / "Videos"
    loose = make_mp3("loose.mp3", folder=root)
    nested = make_mp3("n.mp3", folder=root / "Album - Real Album")
    assert read_local(loose)["folder_album_hint"] == "Videos"            # without root: legacy behaviour
    assert read_local(loose, root)["folder_album_hint"] == ""            # with root: no bogus album hint
    assert read_local(nested, root)["folder_album_hint"] == "Real Album"


def test_fill_blank_never_overwrites_populated_tags(make_mp3):
    p = make_mp3(title="Local Title", artist="Local Artist", genre="Punjabi")
    written = fill_blank_tags(p, {
        "title": "Provider Title", "artist": "Provider Artist", "album": "Provider Album",
        "date": "2020", "genre": "Pop", "tracknumber": "3",
    })
    tags = easy(p)
    assert tags["title"] == ["Local Title"]
    assert tags["artist"] == ["Local Artist"]
    assert tags["genre"] == ["Punjabi"]
    assert tags["album"] == ["Provider Album"]
    assert tags["tracknumber"] == ["3"]
    assert set(written) == {"album", "date", "tracknumber"}


def test_fill_blank_ignores_empty_provider_values(make_mp3):
    p = make_mp3(title="T")
    assert fill_blank_tags(p, {"album": "", "artist": None}) == []
