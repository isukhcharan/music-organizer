# Music Organizer --- Project Handoff & Development Brief

**Purpose:** This document captures the work completed so far, the
current workflow and rules, and the proposed plan for turning the Python
music-organizing scripts into a cross-platform local web application.

**Intended audience:** Claude or another developer/AI assistant
continuing the project.

**Important:** Treat this document as a handoff based on the project
history. Before modifying code, inspect the actual scripts and reports
in the project directory. Do not assume that every generated script has
already been run successfully.

------------------------------------------------------------------------

## 1. Project goal

Build a personal music library organizer that can:

1.  Scan a user-selected music folder.
2.  Identify tracks and retrieve accurate metadata, especially for
    Punjabi music.
3.  Match tracks to the correct songs and releases/albums.
4.  Preserve trustworthy metadata already present in the local files.
5.  Fill only missing metadata from a trusted provider.
6.  Detect ambiguous matches, release conflicts and duplicate files.
7.  Move confidently matched tracks into a consistent folder structure.
8.  Let the user resolve uncertain tracks through a review workflow.
9.  Produce reports and preserve enough state to resume work safely.

The longer-term goal is a **cross-platform application** for Windows and
Linux, with macOS compatibility kept in mind. It should offer both a
terminal workflow and a browser-based UI served locally on the user's
machine.

This is a personal/local music library tool. It is not intended to be a
cloud service.

------------------------------------------------------------------------

## 2. Existing environment and project locations

### Current development environment

-   Primary development and testing environment: Windows.
-   Shell: PowerShell.
-   Python virtual environment: `C:\BeetsEnv`
-   Use the explicit interpreter: `C:\BeetsEnv\Scripts\python.exe`
-   Organizer project: `C:\MusicOrganizer`
-   Existing scripts directory: `C:\MusicOrganizer\app`
-   Reports/data directory: `C:\MusicOrganizer\data`
-   Output directory: `C:\MusicOrganizer\Output`
-   Example input/test folder: `C:\MusicTest\Incoming`

The user is technically comfortable and works with Python, TypeScript,
React, Node.js and backend systems. They prefer practical, sequential
implementation and do not want repeated unnecessary script rewrites or
excessive test cycles.

### Python packages and tools used

The environment has included:

-   Python
-   Mutagen for reading/writing audio metadata
-   `ytmusicapi` (version 1.12.4 was installed and working)
-   Beets 2.14.1
-   `fpcalc` / Chromaprint 1.6.2
-   `pyacoustid`
-   MusicAPy / Saavn API
-   `beets-jiosaavn`

Beets and the JioSaavn integration were explored for Punjabi metadata,
but the custom organizer evolved to use YouTube Music as its preferred
metadata source.

------------------------------------------------------------------------

## 3. Metadata sources and matching strategy

### Provider priority

The established preference is:

1.  **YouTube Music** --- primary source and trusted metadata source.
2.  **JioSaavn** --- secondary/fallback source where useful.
3.  Other providers only if explicitly added and their results can be
    evaluated reliably.

Spotify was not a preferred dependency; the user does not have Spotify
Premium.

### Punjabi music matching

Punjabi music is an important use case. Generic music metadata services
and MusicBrainz/Picard did not reliably identify all of the user's
Punjabi tracks.

YouTube Music searches were tested with examples such as:

-   `295 Sidhu Moose Wala` → correct `Moosetape` release.
-   `Moosetape Sidhu Moose Wala` → full 32-track album.
-   `Reel Purani Reejh Punjabi` → Veet Baljit release with eight tracks.
-   `Ardaas Punjabi soundtrack` →
    `Ardaas (Original Motion Picture Soundtrack)` by Jatinder Shah, with
    six tracks. It was correctly distinguished from `Ardaas Karaan`.

The matcher should consider both album-first and song-level fallback
matching. It should not assume that a song-title match alone proves that
the release/album is correct.

### Matching and confidence

The previous versions evolved through several iterations to improve
album-first matching, song fallback, release verification, track-number
handling and classification.

The general policy should remain conservative:

-   Auto-match only when the evidence is sufficiently strong.
-   Keep ambiguous or conflicting matches in the review queue.
-   Distinguish a one-track provider release from a track that happens
    to be the only local track from a larger album.
-   Verify release track counts and track positions when available.
-   Do not assume that the first provider candidate is always correct.
    It is often correct, but the user explicitly says this is not always
    the case.

------------------------------------------------------------------------

## 4. Metadata preservation rules --- critical

The user explicitly chose a **local-tags-first, provider-fills-blanks**
policy for the automatic workflow.

### Automatic workflow rules

For automatically matched tracks:

-   If a local tag is populated, preserve it.
-   If a local tag is blank/missing, fill it from the selected provider
    result when available.
-   Do not overwrite populated local values with provider values.
-   Preserve existing genre; do not replace it with a provider genre.
-   Keep the original source audio files untouched in the copy-based
    workflow. The later move-based workflow should move the original
    file only after a successful operation and should not rewrite the
    source before the move.

Fields covered by the preservation policy include:

-   Title
-   Artist
-   Album
-   Album artist
-   Year/date
-   Track number
-   Genre

The current conflict resolver has a different purpose: it lets the user
deliberately choose local or provider metadata for a specific unresolved
track. Those manual choices are explicit overrides, not the automatic
fill-blanks policy.

### Important track-number nuance

In v14, a known local/provider track-number conflict was handled as
follows:

-   The output filename could use the provider's track position for
    album ordering.
-   The embedded track-number tag remained the local value.
-   The conflict was retained in the report.

This distinction was intentional in that version. A future UI should
make the difference between **filename ordering** and **embedded
track-number tag** clear. Do not silently change this behaviour without
discussing it with the user.

### Local artist credits

Local artist tags sometimes contain more detailed credits than the
provider result, including producers or collaborators. Since populated
local tags are to be preserved, these differences are not a reason to
replace the local artist tag.

------------------------------------------------------------------------

## 5. Folder organization rules

The desired folder structure is:

``` text
Output/
├── Artist/
│   ├── Singles/
│   │   └── Track.ext
│   └── Album/
│       └── Tracks.ext
├── Soundtracks/
│   └── Album/
│       └── Tracks.ext
└── Compilations/
    └── Album/
        └── Tracks.ext
```

Rules:

-   Regular albums: `Artist/Album/Tracks`
-   Standalone singles: `Artist/Singles/Track`
-   Soundtracks: `Soundtracks/Album/Tracks`
-   Compilations / multi-artist album groupings:
    `Compilations/Album/Tracks`
-   Keep the actual per-track artist in the `Artist` metadata tag.
-   Avoid creating a `Various Artists` directory.
-   `Compilation` as an album-artist tag is acceptable for multi-artist
    grouping.
-   Do not classify a track as a standalone single merely because only
    one track from its album exists locally.
-   Use provider release information to distinguish a genuine one-track
    release from a track belonging to a multi-track album.

### v14 singles classification fix

The v14 script included a fix to recognize two provider-detail formats:

-   `Verified in 1-track release`
-   `Matched against album tracklist (1 tracks)`

It classified a release as a standalone single only when the provider
release track count was exactly one. A track such as `Wah Kya Nazare`,
identified as track 2 of a nine-track release (`8 Chances`), should
remain in the album folder even if it is the only local track from that
album.

------------------------------------------------------------------------

## 6. Script and report history

The organizer went through multiple iterations:

-   v3: approximately 70 auto-matches, 74 review items, 10 no-result
    items.
-   v5: improved classification and conflict reporting.
-   v6: album-first matching improvements.
-   v8: preview workflow.
-   v9: folder layout and album-artist inference improvements.
-   v10: improved album/song fallback matching.
-   v11: tag writing changed to preserve populated local tags and fill
    blanks.
-   v12/v13: further iterations; v14 was based on v13.
-   v14: fixed singles classification for both provider-detail patterns.
-   v15: a move-oriented organizer script was generated for future use.

### v14 report results

The inspected v14 CSV report had 154 rows:

  Status                        Count
  ------------------------- ---------
  AUTO_MATCH                      142
  REVIEW                            7
  REVIEW_RELEASE_CONFLICT           3
  REVIEW_DUPLICATE                  2
  **Total**                   **154**

Other observations:

-   142 tracks were eligible for automatic organization.
-   12 rows were not eligible for automatic copying/moving: 7 general
    reviews, 3 release conflicts and 2 duplicate rows.
-   66 auto-matches used album-first matching.
-   76 auto-matches used album-first plus song-level fallback.
-   61 auto-matched rows were verified one-track provider releases and
    should be classified as standalone singles by the v14 rule.
-   Some tracks were correctly matched to multi-track albums even where
    only one track from the album was present locally.
-   Soundtrack and compilation grouping was part of the intended folder
    logic.

### Examples from the v14 review queue

The following were identified as unresolved or duplicate cases. Treat
these as historical examples; re-check the current report before acting.

General review examples:

-   `Kade Ta Tu Avenga` --- multiple plausible releases.
-   `Lamborghini` --- insufficient confidence in the match.
-   `Lethal Combination` --- candidate title was not close enough.
-   `Jaan Kadh Lai` --- candidate title was not close enough.
-   `Supne` --- multiple plausible releases.
-   `Surma` --- insufficient confidence.
-   `Zulfa` --- candidate title was not close enough.

Release conflicts:

-   `Addiyan Chuk Chuk` --- local album context differed from the
    candidate release.
-   `Haan Karde` --- missing local artist/album context and conflicting
    folder/release hint.
-   `Jaan Ton Pyari` --- missing local artist/album context and
    conflicting folder/release hint.

Duplicates:

-   `Photo` by Singga --- duplicate copy detected.
-   `Rahiye Hasde` by Khan Bhaini --- duplicate copy detected.

The duplicate detector should never automatically delete files. The user
should decide whether to keep, remove or otherwise handle duplicates.

------------------------------------------------------------------------

## 7. Move workflow and conflict resolver scripts

After reviewing v14, the user requested a change from copying approved
tracks to moving them into Output, leaving review and duplicate tracks
in the input folder.

Three scripts were generated and offered:

1.  `move_approved_from_report.py`
    -   Intended to read the existing v14 report.
    -   Preview by default.
    -   With `--apply`, move only `AUTO_MATCH` tracks into Output.
    -   Leave review, release-conflict and duplicate tracks in the input
        folder.
    -   Intended to avoid re-running provider matching just to perform
        the move.
2.  `music_organizer_v15_move.py`
    -   A move-oriented future organizer version.
    -   Intended for subsequent scans/workflows.
3.  `music_conflict_resolver.py`
    -   A separate manual conflict-resolution CLI.

**Do not assume the user has run these scripts.** They were generated
and linked, but the conversation does not confirm successful execution.
Inspect the actual files, check their safety and test their behaviour
before treating them as production-ready.

### Conflict resolver interaction

The user described their usual manual decision pattern after selecting
the correct incoming/provider candidate:

``` text
PPPenterLLenter
```

The metadata field order in the resolver was:

1.  Title
2.  Artist
3.  Album
4.  Album artist
5.  Year
6.  Track number
7.  Genre

The agreed default choices are:

  Field          Default
  -------------- ---------------------------------------
  Title          Provider (`P`)
  Artist         Provider (`P`)
  Album          Provider (`P`)
  Album artist   Leave unchanged / blank (press Enter)
  Year           Local (`L`)
  Track number   Local (`L`)
  Genre          Leave unchanged / blank (press Enter)

The updated resolver was generated so that pressing Enter accepts these
defaults. The user still selects the correct provider candidate
manually; candidate 1 is often correct but not always. The user can
override individual field choices.

The resolver should ideally support:

-   Local value
-   Selected provider value
-   Manual value
-   Keep unchanged
-   Blank values without accidental data loss

For the web UI, represent these as clear controls rather than requiring
the user to type `P`, `L`, `M`, or `K`.

------------------------------------------------------------------------

## 8. Proposed application: cross-platform local web app

The user decided that the application should be **cross-platform**, not
Windows-only.

### Platform goals

-   Windows: primary development and first packaging target.
-   Linux: supported from the beginning.
-   macOS: keep compatibility in mind; avoid Windows-only assumptions in
    core logic.

### Proposed stack

  -----------------------------------------------------------------------
  Layer                   Technology              Purpose
  ----------------------- ----------------------- -----------------------
  Backend                 Python + FastAPI        Local API,
                                                  orchestration, file
                                                  operations

  Frontend                React + TypeScript      Browser-based user
                                                  interface

  Frontend tooling        Vite                    Development and
                                                  production frontend
                                                  build

  Persistence             SQLite                  Jobs, scan results,
                                                  matches, decisions,
                                                  history

  Live updates            WebSockets              Scan progress and
                                                  operation status

  Metadata                Mutagen                 Read and write audio
                                                  tags

  Provider integrations   Existing Python         YouTube Music and
                          integrations            configured fallbacks

  CLI                     Python CLI              Terminal mode using the
                                                  same application core
  -----------------------------------------------------------------------

Use the same processing engine for both the terminal and web interface.
Do not implement matching or file organization twice.

### High-level architecture

``` text
                    Music Organizer
                           |
                    Application Core
                           |
              +------------+------------+
              |                         |
          Terminal CLI                FastAPI
                                          |
                                      React UI
                                          |
                         +----------------+
                         |
                  Music Processing Core
                         |
          +--------------+----------------+
          |              |                |
        Scanner        Matcher          Organizer
          |              |                |
          +---------- SQLite ------------+
                         |
                  Conflict Resolver
```

### Launch experience

When the user runs the application, offer:

-   **Terminal mode:** run the CLI workflow in the terminal.
-   **UI mode:** start the local backend, open the user's default
    browser at a loopback URL such as `http://127.0.0.1:<port>`, and
    show the dashboard.

The UI should be local-only by default. Bind to `127.0.0.1`, not
`0.0.0.0`, unless the user explicitly opts into network access. No cloud
account, hosted backend or external database is required.

### Cross-platform filesystem handling

Avoid hardcoded Windows paths and shell commands in the core
application.

-   Use `pathlib.Path` for paths.
-   Use platform-neutral file operations.
-   Handle path separators and case sensitivity appropriately.
-   Do not assume that a Windows drive letter exists.
-   Keep platform-specific launchers and packaging separate from the
    core engine.
-   Use a safe folder picker or native file-dialog mechanism for
    selecting input and output directories. A browser page cannot freely
    browse arbitrary local paths without an explicit supported
    mechanism.
-   Keep provider configuration and credentials out of source code.

------------------------------------------------------------------------

## 9. Proposed user experience and workflow

### Stage 1 --- Choose mode

At startup:

-   Terminal
-   Web interface

Both modes call the same core services.

### Stage 2 --- Select directories

The UI should allow the user to select:

-   Input music directory
-   Output directory
-   Optional reports/workspace directory

Show the selected paths and validate that they exist and are accessible.
Show the number of discovered audio files before starting.

### Stage 3 --- Scan and match

The backend should:

1.  Discover supported audio files.
2.  Read existing tags.
3.  Detect exact duplicates.
4.  Search provider metadata.
5.  Evaluate candidates and release context.
6.  Classify each track as auto-match, review, release conflict,
    duplicate or no result.
7.  Persist results as they are produced.

Display live progress, for example:

-   Files discovered
-   Files processed
-   Auto-matched
-   Needs review
-   Release conflicts
-   Duplicates
-   No results
-   Current operation / errors

Avoid blocking the browser while long-running work is happening. Use
background jobs and WebSocket progress events.

### Stage 4 --- Automatically organize approved matches

The user wants approved tracks moved into Output, not copied, while
review and duplicate tracks remain in the input directory.

Important safety requirements:

-   Preview planned operations.
-   Check destination collisions before moving.
-   Do not overwrite existing files.
-   Record original path and destination path.
-   Do not remove or modify files classified as review, release
    conflict, duplicate or no result.
-   If metadata needs writing, ensure the operation is safe and
    recoverable.
-   If a move or metadata operation fails, report it and leave the
    source recoverable.
-   Make operations idempotent where possible, so retrying a job does
    not create duplicate output or corrupt state.

### Stage 5 --- Conflict review UI

This is a central feature.

For each unresolved track, show:

-   Current local metadata
-   Candidate provider results
-   Candidate title, artist, album, album artist, year, track number and
    provider/release details where available
-   Why the track was flagged
-   Candidate confidence or matching evidence, if the engine can provide
    it

The user manually selects the correct provider candidate. Do not
auto-select candidate 1 just because it is often correct.

After selecting a candidate, show per-field decisions:

-   Use local
-   Use selected provider candidate
-   Enter a manual value
-   Keep unchanged / leave blank

Preselect the user's usual defaults:

  Field          Default
  -------------- ----------------
  Title          Provider
  Artist         Provider
  Album          Provider
  Album artist   Keep unchanged
  Year           Local
  Track number   Local
  Genre          Keep unchanged

Allow the user to change any individual choice.

Useful review controls:

-   Previous / next track
-   Save decision
-   Save and next
-   Skip for later
-   Keyboard shortcuts
-   Filter by review reason
-   Show remaining review count
-   Persist every decision immediately

The UI should make it obvious when local or provider values are blank. A
blank local field should not accidentally erase a populated provider
field when the user intended to use the provider.

### Stage 6 --- Duplicates

Show duplicate groups and their file paths. Do not delete duplicates
automatically.

Potential user decisions:

-   Keep both
-   Keep selected file and mark others for later removal
-   Move duplicate to a separate review folder
-   Skip for now

Any destructive deletion should require explicit confirmation and be
logged.

### Stage 7 --- Completion and reports

Show a summary:

-   Total files scanned
-   Auto-matched
-   Moved successfully
-   Needs review
-   Manually resolved
-   Duplicates
-   Skipped
-   Errors

Allow export of CSV/JSON reports. Keep a job history so the user can
inspect or resume earlier runs.

------------------------------------------------------------------------

## 10. Safety, reliability and recovery requirements

These should be treated as core requirements, not later polish.

### Preview / dry run

Before moving files, show what will happen:

-   Source path
-   Destination path
-   Metadata changes, if any
-   Collision warnings
-   Files that will remain untouched

### No accidental overwrites

If the destination filename already exists:

-   Do not overwrite.
-   Show a collision warning.
-   Offer a safe rename or manual resolution strategy.
-   Record the decision.

### Operation journal and undo

Record every file operation in SQLite:

-   Job ID
-   Source path
-   Destination path
-   Operation type
-   Timestamp
-   Status
-   Error message, if any

Provide an undo/recovery feature for moves where the destination is
still available and safe to restore.

### Resume support

If the application closes or the machine restarts:

-   Keep scan and matching results.
-   Keep manual conflict decisions.
-   Keep completed file operations.
-   Allow the user to resume pending work without re-running provider
    searches unnecessarily.

### Source safety

The user has emphasized that original files should not be unexpectedly
modified or lost.

For automatic processing, preserve populated local tags and fill blanks
only. For manual conflict resolution, apply only the explicit field
choices. Use safe temporary-file or backup strategies when modifying
tags, and ensure file movement can be recovered.

### Local security

-   Bind the server to loopback by default.
-   Do not expose the service to the LAN or internet by default.
-   Do not embed provider credentials in frontend code.
-   Validate all paths received by API endpoints.
-   Restrict file operations to user-selected directories and their
    intended output locations.
-   Avoid arbitrary shell execution from API requests.

------------------------------------------------------------------------

## 11. Suggested project structure

This is a proposed structure, not an existing implementation:

``` text
MusicOrganizer/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── api/
│   │   │   ├── routes/
│   │   │   └── websocket.py
│   │   ├── core/
│   │   │   ├── config.py
│   │   │   ├── jobs.py
│   │   │   └── events.py
│   │   ├── services/
│   │   │   ├── scanner.py
│   │   │   ├── matcher.py
│   │   │   ├── provider_clients/
│   │   │   ├── metadata.py
│   │   │   ├── organizer.py
│   │   │   ├── conflict_resolver.py
│   │   │   └── duplicate_detector.py
│   │   ├── database/
│   │   │   ├── models.py
│   │   │   └── repository.py
│   │   └── schemas/
│   ├── tests/
│   └── pyproject.toml
│
├── frontend/
│   ├── src/
│   │   ├── app/
│   │   ├── pages/
│   │   ├── components/
│   │   ├── features/
│   │   │   ├── library/
│   │   │   ├── scan/
│   │   │   ├── review/
│   │   │   ├── duplicates/
│   │   │   └── history/
│   │   └── services/
│   └── package.json
│
├── scripts/
│   ├── launcher.py
│   ├── start_windows.bat
│   └── start_linux.sh
│
├── data/
├── Output/
├── reports/
└── README.md
```

The project structure can be adjusted after inspecting the current code.
Avoid moving or renaming working scripts until their behaviour and
dependencies are understood.

------------------------------------------------------------------------

## 12. Implementation roadmap

Build incrementally. Do not rewrite the whole application in one step.

### Phase 0 --- Audit the existing implementation

Before coding:

1.  Inspect all current scripts, especially v14, v15 and the conflict
    resolver.
2.  Inspect the latest CSV and JSON reports.
3.  Identify shared logic, duplicated code and command-line-only code.
4.  Confirm actual behaviour of the move script and conflict resolver.
5.  Identify provider dependencies and configuration.
6.  Document known bugs and safety concerns.

Do not assume generated scripts were executed successfully. The
conversation only confirms that they were generated and offered to the
user.

### Phase 1 --- Refactor into a reusable core

Separate:

-   File discovery
-   Metadata reading/writing
-   Provider search
-   Candidate matching
-   Release/album verification
-   Duplicate detection
-   Classification
-   Folder destination calculation
-   File movement
-   Conflict decision application
-   Reporting

The CLI and web app should call these services. Preserve existing
matching behaviour and metadata rules during the refactor.

### Phase 2 --- Add persistence and job management

Use SQLite to store:

-   Jobs and settings
-   Discovered tracks
-   Local metadata snapshots
-   Provider candidates
-   Match status and reasons
-   User decisions
-   File operation journal
-   Errors and progress

Make it possible to resume a job and avoid unnecessary repeat searches.

### Phase 3 --- Build FastAPI backend

Suggested API capabilities:

-   Get application status/configuration
-   Select or validate directories
-   Start scan/matching job
-   Read job progress
-   Get track results
-   Get review queue
-   Get provider candidates for a track
-   Save conflict decisions
-   Preview organization
-   Apply organization
-   View duplicates
-   Export reports
-   Undo eligible file operations

Use background tasks or a proper job worker for long-running operations,
and WebSockets for progress events.

### Phase 4 --- Build React UI

Pages:

1.  Welcome / mode selection
2.  Dashboard
3.  Folder configuration
4.  Scan progress
5.  Match results
6.  Conflict review
7.  Duplicate review
8.  Operation preview
9.  Completion summary
10. Job history / recovery

Keep the UI straightforward and efficient for reviewing many tracks.

### Phase 5 --- CLI and launcher

Keep terminal mode available and route it through the same core
services.

UI mode should:

1.  Start the local backend.
2.  Wait until the server is ready.
3.  Open the browser at the local URL.
4.  Shut down cleanly when the user exits the application.

Provide platform-specific launchers for Windows and Linux. Keep the
underlying Python code platform-neutral.

### Phase 6 --- Testing and packaging

Test on Windows first, then Linux. Add macOS testing when practical.

Test cases should include:

-   Empty or inaccessible input folder
-   Unsupported audio file
-   Missing metadata
-   Populated local tags
-   Provider result with different metadata
-   Multiple provider candidates
-   One-track release vs multi-track album
-   Track-number conflict
-   Duplicate files
-   Destination filename collision
-   Interrupted move
-   Interrupted scan
-   Resume after restart
-   Undo a move
-   User choosing local/provider/manual values
-   Provider/API unavailable

Package only after the application works reliably in development.

------------------------------------------------------------------------

## 13. Important product decisions already made

-   Cross-platform architecture: **Yes**.
-   First development/test target: Windows.
-   Linux support: required.
-   macOS: keep compatibility in mind.
-   Application type: local web app, not cloud-hosted.
-   UI: React + TypeScript.
-   Backend: Python + FastAPI.
-   Persistence: SQLite.
-   Terminal mode: retain it.
-   UI mode: open localhost automatically.
-   Matching engine: preserve and refactor existing logic, do not start
    over.
-   Approved tracks: move to Output, rather than copy, in the new move
    workflow.
-   Review/conflict/duplicate tracks: leave in input until the user
    resolves them.
-   Duplicate deletion: never automatic.
-   Auto metadata: preserve populated local tags; fill blanks only.
-   Manual metadata: follow explicit per-field user choices.
-   Candidate selection: user chooses the correct incoming/provider
    candidate; candidate 1 is often correct but not guaranteed.
-   Conflict resolver defaults: provider for title/artist/album; leave
    album artist unchanged; local for year/track number; leave genre
    unchanged.
-   Safety: preview, no overwrites, persistent decisions, operation
    journal and recovery.

------------------------------------------------------------------------

## 14. Guidance for the developer/AI continuing this project

Please follow these principles:

1.  **Inspect before changing.** Read the actual current scripts and
    reports. This document is a handoff, not a substitute for source
    code.
2.  **Preserve working behaviour.** The matching logic has been iterated
    on extensively, especially for Punjabi tracks and album-level
    matching.
3.  **Do not create unnecessary script versions.** Refactor deliberately
    and keep changes focused.
4.  **Do not silently alter metadata rules.** Local populated tags must
    remain untouched during automatic processing.
5.  **Do not silently move or delete files.** Preview operations and
    require explicit application.
6.  **Do not assume the first provider candidate is always correct.**
    The user must be able to choose.
7.  **Do not rerun expensive provider matching unnecessarily.** Persist
    candidates and results.
8.  **Keep CLI and UI on one engine.**
9.  **Make the app cross-platform by design.** Avoid platform-specific
    assumptions in the core.
10. **Work in small phases.** Finish and verify the backend refactor
    before building a large UI on top of unstable logic.

### Recommended first task

Start with **Phase 0: audit the existing project**. Ask the user to
provide or make available the current `C:\MusicOrganizer` project folder
and the latest scripts/reports. Inspect the code, map the existing
workflow, and propose a safe refactoring plan before changing anything.

------------------------------------------------------------------------

## 15. Current known file names from the handoff

The following files were generated and linked in the previous
conversation:

-   `move_approved_from_report.py`
-   `music_organizer_v15_move.py`
-   `music_conflict_resolver.py` (updated with the user's default field
    choices)

The v14 report files previously inspected were:

-   `organizer-v14-report.csv`
-   `organizer-v14-report.json`

Their previous working paths in the conversation environment were under
`/mnt/data`. The user's Windows project paths are listed earlier in this
document. Confirm the current files and versions with the user rather
than assuming they are still in place.

------------------------------------------------------------------------

**End of handoff document.**
