# AGENTS.md

## Application Versioning

- Keep the current application version in `VERSION`. This is the single source
  for Python package metadata and the version displayed in the application.
- Record application changes in `CHANGELOG.md` in the same change that updates
  `VERSION`.
- Use semantic versioning: `PATCH` for fixes and wording changes, `MINOR` for
  backward-compatible features, and `MAJOR` for incompatible behavior or data
  changes.
- Each changelog entry must include the version, date, `Added by: <user>`
  attribution, and a concise summary under `Added`, `Changed`, `Fixed`, or
  `Removed` headings as applicable. Include a `Source commit` only when a real
  Git commit or pull-request reference exists.
- When several application changes are still uncommitted, amend the latest
  uncommitted version and changelog entry instead of creating successive version
  entries unless the user explicitly requests a separate release entry.
- Tests-only changes and internal documentation that do not alter the application
  do not require a version bump.
