# Changelog

Entries use the date of the application version change. Version history before
0.2.0 was reconstructed from repository history without rewriting Git history.

## 0.3.0 - 2026-09-14

Added by: Knarkoffer

### Added

- Show a red "Expiry forecast" row after Updated only when the recent usage
  trend predicts running out before the next reset. Hover over the row for the
  explanation, number of readings, and calendar or working hours used.
- Format forecast dates as "Monday, 14 September at 19:00", or "Today at 19:00"
  with a countdown for same-day estimates in the configured time zone.
- Estimate from the recent average usage increase and selected Personal or Work
  schedule; withhold warnings when history is insufficient, data is stale, usage
  is flat, or the allowance is already exhausted. Restart the trend after usage
  corrections or allocation changes.

## 0.2.3 - 2026-09-14

Added by: Knarkoffer

### Changed

- Detect the local IANA time zone on Windows and use the selected time zone
  consistently for pace calculations, usage-window labels, graph dates, and
  displayed timestamps.
- Pass refresh results back to the Tk main thread through a queue so background
  network work never calls Tk directly.

### Fixed

- Keep the automatic login source selected when settings are saved, rather than
  silently pinning the monitor to the first installed WSL distribution.

## 0.2.2 - 2026-09-13

Added by: Knarkoffer

### Fixed

- Keep usage periods separate when their reported start or reset changes,
  preserving the dates and readings of previous periods.
- Exclude readings outside the selected period from the graph, including older
  mixed history, to prevent false drops and points piled up at the graph edges.
  Existing readings remain stored; deleting history is not required.
- Keep the graph on the current period after a reset when it was already showing
  current usage, while preserving explicitly selected historical periods.

## 0.2.1 - 2026-09-09

Added by: Knarkoffer

### Fixed

- Launch updated source without reinstalling the package when runtime dependencies
  are present, avoiding startup failures caused by expired package-index credentials.
- Check timezone data alongside tray dependencies before starting the app.
- Clarify visible launcher diagnostics with `--diagnose`, while keeping
  `--background` compatible with existing commands and the hidden launcher.

## 0.2.0 - 2026-09-07

Added by: Knarkoffer

### Added

- Added personal-plan usage monitoring alongside enterprise credit allocation.
- Added Personal and Work pacing modes, with a saved initial default based on
  Windows domain membership and immediate pace and graph updates when switching.
- Added workday start and end time selectors in Work mode.
- Added notification-area support, minimize-to-tray behavior, and single-instance
  handling.
- Added application version tracking with a single `VERSION` source, package
  metadata integration, an in-app version label, and launcher-driven local
  upgrades.
- Added specific guidance when a selected WSL distribution is not running.

### Changed

- Improved the usage graph, status messages, launcher diagnostics, application
  icon, and Windows taskbar identity.

### Fixed

- Close temporary test database connections before cleanup on Windows.

## 0.1.0 - 2026-08-18

Added by: Knarkoffer
Source commit: `d112f79` (`Migrated`)

### Added

- Added the initial Windows desktop monitor, including authenticated Codex usage
  retrieval from native Windows or WSL, local history, pace evaluation,
  notifications, settings, tests, and Windows launchers.
