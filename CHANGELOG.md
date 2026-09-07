# Changelog

Entries use the date of the application version change. Version history before
0.2.0 was reconstructed from repository history without rewriting Git history.

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
