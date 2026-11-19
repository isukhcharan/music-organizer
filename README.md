# Music Organizer

Personal, local-only music library organizer (strong on Punjabi music). Scans a folder, matches tracks
against YouTube Music (JioSaavn as fallback), keeps populated local tags, fills blanks, and moves confident
matches into `Artist/Album`, `Artist/Singles`, `Soundtracks/` or `Compilations/`. Uncertain tracks go through
a review step.

Planned: Python + FastAPI backend, React + TypeScript UI, SQLite state, plus a terminal mode, all on one core.
Windows first, Linux supported, macOS kept in mind.

## Status

Phase 0 (audit) done. See [docs/AUDIT.md](docs/AUDIT.md) and the original brief
[docs/HANDOFF.md](docs/HANDOFF.md).

`legacy/` holds unchanged copies of the working scripts from `C:\MusicOrganizer\app`. They are the reference
for behaviour while the core is refactored out of them.

Phase 1 core is in `backend/musicorganizer/` (no network needed to test it):

| Module | Role |
|---|---|
| `text`, `tags`, `layout` | normalising/similarity, tag read + blank-fill, folder layout rules |
| `providers`, `matcher`, `pipeline` | YouTube Music / JioSaavn behind one interface; album-first + song fallback |
| `duplicates` | byte-identical detection (report only, never deletes) |
| `organizer`, `journal` | preview plan, copy-tag-verify-then-remove moves, no overwrites, undo |
| `resolver`, `store` | per-field manual decisions with your defaults; SQLite jobs/rows/decisions |

## Use (terminal)

```
python -m musicorganizer scan  --input <music> --output <library>   # identifies, moves nothing
python -m musicorganizer plan  --job <id>                           # preview every move
python -m musicorganizer apply --job <id> --yes                     # move approved tracks
python -m musicorganizer undo  --job <id>                           # put everything back
```

## Develop

```
python -m venv .venv
.venv\Scripts\python -m pip install -e backend[dev]
cd backend && ..\.venv\Scripts\python -m pytest
```

## Next

FastAPI backend and the React review UI (interactive review is not in the CLI yet).

## Not in git

`data/`, `reports/`, `Output/` and databases are ignored; they contain personal library metadata.
