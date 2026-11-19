# Phase 0 audit of `C:\MusicOrganizer`

Date: 2026-11-20. Read-only inspection; nothing in `C:\MusicOrganizer` was changed.
The scripts that matter are copied (unchanged) into `legacy/`. Older versions v2–v14 stay in
`C:\MusicOrganizer\app` and are not needed.

## What exists

| Item | State |
|---|---|
| `app/music_organizer_v15_move.py` (~900 lines) | Matching engine + move logic in one file. Preview by default, `--apply` moves. |
| `app/move_approved_from_report.py` | Moves `AUTO_MATCH` rows from a saved report. Imports v15. |
| `app/music_conflict_resolver.py` | Interactive CLI, per-field L/P/M/K choices with the agreed defaults. |
| `app/scanner.py`, `identifier.py` | Early experiments (SQLite scan table). Superseded. |
| `data/*.json|csv` | Reports v2–v15 plus `organizer-manual-resolution`. v14/v15 = 154 rows. |
| `C:\BeetsEnv` | Python 3.13; mutagen 1.48.1, ytmusicapi 1.12.4, musicapy 2.2.1, beets 2.14.1. |

## Report numbers

- v14: 142 AUTO_MATCH, 7 REVIEW, 3 REVIEW_RELEASE_CONFLICT, 2 REVIEW_DUPLICATE (matches the handoff).
- v15: 141 AUTO_MATCH, 7 REVIEW, **4** REVIEW_RELEASE_CONFLICT, 2 REVIEW_DUPLICATE. One row differs from v14; not yet investigated.
- `organizer-manual-resolution.json`: 8 rows have `manual_resolution` set, so the resolver was run at least once with `--apply`.

## Open question: the test music is gone

`C:\MusicTest\Incoming` and `C:\MusicOrganizer\output` both contain **0 files**. No `source` path and no
`output` path in the v14, v15 or manual-resolution reports exists on disk. The move scripts were evidently
run, but the files are not where the reports say. Needs the owner to say where the library/test copy lives
before any real-file testing.

## Issues found in the legacy code (to fix during the refactor, not before)

Matching/logic
1. All matching, classification, tagging and moving live in one module with Windows paths hardcoded
   (`C:\MusicTest\Incoming`, `C:\MusicOrganizer`).
2. Importing v15 creates `YTMusic()` and `SaavnAPI()` at import time (network clients as a side effect).
   `move_approved_from_report.py` imports v15, so even a preview constructs them.
3. `process(path, apply, ...)` takes an `apply` argument it never uses.
4. Local title falls back to the filename stem when the tag is blank, so "blank title" never counts as blank
   for decisions. The tag write itself still checks the real tag, so fill-blanks works.

Safety
5. Preview mode in `move_approved_from_report.py` still **rewrites** `organizer-v15-report.*`, and it does not
   compute destinations, so a preview cannot show collisions or folder choices.
6. `move_approved` moves the file first, then writes tags. A tag-write failure leaves a moved file with
   incomplete tags; it is recorded only as text in `detail_status`. No operation journal, no undo.
7. Sidecar lookup uses `source.parent.glob(source.stem + ".*")`. Stems containing `[`, `]` or `*` are treated
   as glob patterns and can miss or wrongly match files.
8. Collision handling renames with `(2)` instead of surfacing a decision to the user (it never overwrites,
   which is good).

Resolver
9. Resolved files go to `Output/Manual Review/<Artist>/<Album>/`, not the agreed folder layout
   (Artist/Album, Singles, Soundtracks, Compilations).
10. Each run reloads the v15 report and rewrites `organizer-manual-resolution.*` from it, so earlier decisions
    are not accumulated in one place; state is only implicit in the file system.
11. Preview mode also writes the resolution report files.
12. Candidate album comes from the song-search `album_hint`; year/track number provider values are only the
    report-level ones, which are blank for REVIEW rows.

Cosmetic
13. Final summary prints a literal `\\n`.

## Behaviour to preserve (from HANDOFF.md)

- Local tags first, provider fills blanks, automatic path only.
- Filename track order may use the provider position while the embedded tag stays local.
- A track is a Single only if the provider release has exactly one track.
- Never delete duplicates automatically; never overwrite files.
- User picks the provider candidate; candidate 1 is not auto-selected.

## Deliberate changes to matching behaviour (found in live testing, 2026-11-20)

Test set: 39 real mp3s across 9 albums. The legacy logic flagged all 7 tracks of one album as release
conflicts. Cause: tag `Panj-Aab Vol-1.0` vs provider `PANJ-AAB, Vol. 1`.

1. `norm()` now collapses repeated whitespace (it left double spaces after removing punctuation; similarity
   was 0.875 against a 0.88 threshold).
2. Album search is a ladder: `album + artist`, then `album`, then the album reduced to plain words. YouTube
   Music returns nothing for some spellings (`Vol-1.0`); only the last rung finds this one.
3. The album-first artist check compares the provider artist with *all* local track artists, not one guessed
   artist, so multi-artist albums are not rejected.

Verified live: the 32 tracks that matched before are byte-for-byte identical in result; the 7 conflicts now
match. All three changes can only add matches; no threshold was changed.

### Second live round (55 files, incl. 16 messy/problem tracks)

4. `norm()` ignores `(feat. X)` / `[ft. X]` credits. Zulfa, Lamborghini and Surma failed only because the
   provider title carries the credit (similarity 0.4-0.55). Remixes/versions stay distinct.
5. Two *different* files matched to the same track of the same release are flagged `REVIEW_DUPLICATE`
   (not byte-identical, so the hash check misses them). Previously the second would silently become "(2)".
6. A file sitting directly in the input folder no longer takes the input folder's name as its album hint
   (it would have searched for an album called "Videos").
7. Folder-level album artist is grouped by matched release, not source folder, so a loose copy of an album
   track lands with its album. One-track releases are never merged. `release_tracks` is now stored as data
   instead of being parsed out of the status message. `plan_moves` recomputes album artists, because rows
   can be promoted after the scan (e.g. "keep both").
8. **Undo now restores original tags.** The journal records the previous value of every tag that was written
   (fill-blanks or manual overwrite) and undo puts them back. Before this, undoing a manual decision
   returned the file with the provider's tags. Verified: after apply + undo, all 55 files match the originals
   in tags and duration.

Known behaviour, not a bug: YouTube Music returns search candidates in a different order between runs for
poorly tagged files (e.g. the AKHIL rip). Decisions store the chosen candidate itself, not its list position,
and the review must always be done by reading the list.

### JioSaavn / fallback testing (same 55 files)

9. **Provider track-list position is not a track number.** JioSaavn returns an album's tracks in a different
   order than the album and has no track-number field; numbering by list position gave 8 of 9 tracks of
   `Vehli Janta` the wrong prefix. Rows now carry `track_number_source` (`provider` / `local` / `none`):
   a provider number is used only when the provider states it (YouTube Music does); otherwise the file's own
   track tag; otherwise the filename has no `NN - ` prefix and no track tag is invented.

Results, JioSaavn alone: 42/55 auto-matched, 0 wrong. The rest were flagged, not guessed: Mahiya, Rattan
Chitian and Surma are not in JioSaavn's catalog; `Panj-Aab` is named differently there (release conflict);
Meri Zindgi Bana Ja is ambiguous. Where both providers matched, album and track agreed on all 33 + 8 (after
fix 9). Fallback verified live: with YouTube Music simulated down, the chain's results were identical to the
JioSaavn-only scan (55/55, no errors); with YouTube Music empty for two songs, those two fell back per query
and the third stayed on YouTube Music. Apply + undo of JioSaavn-sourced matches restores all 55 files to
their original tags and duration.

Use `python -m musicorganizer scan ... --provider auto|ytmusic|jiosaavn` (default `auto` = YouTube Music
first, JioSaavn fallback).
