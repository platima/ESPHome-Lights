# CLAUDE.md — AI Assistant Context

This file provides project context for AI coding assistants (GitHub Copilot,
Claude, etc.).  It is read at the start of each session so the assistant
understands the project without re-discovering everything.

## Project Overview

TBC

## Dev Environment

- **OS:** Windows 11 with VS Code as the primary IDE
- **WSL2:** Debian instance available for Linux-native tooling (gcc, west, etc.)
- **Terminals:** PowerShell in VS Code; Debian WSL2 accessible if needed

## Tech Stack

TBC

## Key Files

TBC

## Conventions

### Language

Australian English in **all** comments, log messages, and documentation.
Examples: initialise, behaviour, colour, licence, serialisation, organisation,
optimise, minimise, recognise.

### Versioning (SemVer)

Semantic Versioning tracked in the `VERSION` file at the repo root.

| Bump  | When                                               |
|-------|----------------------------------------------------|
| PATCH | Each individual commit (bug fix, small improvement) |
| MINOR | Phase or milestone complete (push + update README)  |
| MAJOR | Breaking protocol or API change                     |

### Git Workflow

1. Create a **feature or fix branch** off `master` (`feature/<name>`, `fix/<name>`).
2. Make changes, commit with a **Conventional Commits** message
   (`feat:`, `fix:`, `chore:`, `docs:`).
3. **Bump the PATCH** version in `VERSION` with each commit.
4. When the phase/milestone is complete: bump **MINOR**, update `README.md`,
   commit, and push.
5. Merge back to `master`.

### Documentation & Testing

- **Update docs with every change.** If a feature, config, or file changes,
  update `README.md`, `CLAUDE.md`, and inline comments in the same commit.
- **Create documentation if it's missing.** Never leave a new subsystem
  undocumented.
- **Keep unit tests passing.** Run `tests/test_audio.c` after any change to
  the ADPCM encoder/decoder, ring buffer, or packet header. Add new tests
  for new logic.
- **Update `TODO.md`** when tasks are completed or new work is identified.
  This file is the persistent plan — if a session is lost, the next session
  picks up from `TODO.md`.

### Standard Task Completion Checklist

Every piece of work (feature, fix, refactor) must complete **all** of these
steps before the task is considered done. Do not skip steps, and do not batch
them silently — each must be visible in the plan.

1. Implement the change.
2. Update or create unit tests to cover the change.
3. Run unit tests — fix and repeat until all pass.
4. Update inline code comments (Australian English).
5. Update `README.md` if the change affects usage, structure, or config.
6. Update `CLAUDE.md` if the change affects project context.
7. Update `TODO.md` — mark completed items, add new items if identified.
8. Bump version in `VERSION` (PATCH per commit, MINOR per milestone).
9. `git add -A && git commit` with a Conventional Commits message.
10. At milestone completion: bump MINOR, push, update README version.

### Logging

Verbose/emoji diagnostic logs are gated behind `CONFIG_APP_VERBOSE_LOGS`.
Set to `n` in `prj.conf` for production builds.

## Build & Test

TBC
## Current State

- **Version:** 0.0.1
- **Status:** Basic test code committed

## Known Limitations

TBC