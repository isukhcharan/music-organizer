import os
import sqlite3
import hashlib
from pathlib import Path
from mutagen import File

SOURCE = Path(r"C:\MusicTest\Incoming")
DATABASE = Path(r"C:\MusicOrganizer\data\music.db")

FIELDS = [
    "artist", "albumartist", "title", "album",
    "date", "tracknumber", "discnumber", "genre"
]

def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def get_tag(tags, name):
    value = tags.get(name, "")
    if isinstance(value, (list, tuple)):
        return str(value[0]) if value else ""
    return str(value)

def scan():
    DATABASE.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(DATABASE)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS tracks (
            path TEXT PRIMARY KEY,
            filename TEXT,
            artist TEXT,
            albumartist TEXT,
            title TEXT,
            album TEXT,
            date TEXT,
            tracknumber TEXT,
            discnumber TEXT,
            genre TEXT,
            duration REAL,
            bitrate INTEGER,
            size INTEGER,
            sha256 TEXT,
            scan_status TEXT
        )
    """)

    scanned = 0
    errors = 0

    print(f"Scanning: {SOURCE}")

    for path in SOURCE.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {
            ".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav", ".wma"
        }:
            continue

        try:
            audio = File(path, easy=True)
            if audio is None:
                raise ValueError("Unsupported or unreadable audio file")

            tags = audio.tags or {}
            info = audio.info

            values = {
                "path": str(path.resolve()),
                "filename": path.name,
                **{field: get_tag(tags, field) for field in FIELDS},
                "duration": round(info.length, 2) if info else None,
                "bitrate": getattr(info, "bitrate", None) if info else None,
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
                "scan_status": "OK"
            }

            connection.execute("""
                INSERT OR REPLACE INTO tracks VALUES (
                    :path, :filename, :artist, :albumartist, :title,
                    :album, :date, :tracknumber, :discnumber, :genre,
                    :duration, :bitrate, :size, :sha256, :scan_status
                )
            """, values)

            scanned += 1

            if scanned % 25 == 0:
                print(f"Scanned {scanned} tracks...")

        except Exception as error:
            errors += 1
            print(f"ERROR: {path.name}: {error}")

    connection.commit()

    total = connection.execute(
        "SELECT COUNT(*) FROM tracks"
    ).fetchone()[0]

    missing = connection.execute("""
        SELECT COUNT(*) FROM tracks
        WHERE artist = '' OR title = '' OR album = ''
    """).fetchone()[0]

    duplicates = connection.execute("""
        SELECT COUNT(*) FROM (
            SELECT sha256 FROM tracks
            GROUP BY sha256 HAVING COUNT(*) > 1
        )
    """).fetchone()[0]

    connection.close()

    print("\nScan complete!")
    print(f"Tracks in database: {total}")
    print(f"Missing core metadata: {missing}")
    print(f"Duplicate file groups: {duplicates}")
    print(f"Read errors: {errors}")
    print(f"Database: {DATABASE}")

if __name__ == "__main__":
    scan()
