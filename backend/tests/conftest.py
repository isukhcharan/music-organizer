from pathlib import Path

import pytest
from mutagen.id3 import ID3, TALB, TCON, TIT2, TPE1, TRCK

# One valid MPEG-1 Layer III frame header (128 kbps, 44.1 kHz) + padding = a tiny decodable "silent" mp3.
_FRAME = b"\xff\xfb\x90\x00" + b"\x00" * 413


@pytest.fixture
def make_mp3(tmp_path):
    """Create a minimal mp3 with the given tags (blank/None tags are omitted)."""

    def _make(name="song.mp3", folder=None, **tags) -> Path:
        directory = Path(folder) if folder else tmp_path
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        path.write_bytes(_FRAME * 8)
        frames = {"title": TIT2, "artist": TPE1, "album": TALB, "tracknumber": TRCK, "genre": TCON}
        id3 = ID3()
        for key, frame in frames.items():
            if tags.get(key):
                id3.add(frame(encoding=3, text=tags[key]))
        id3.save(path)
        return path

    return _make
