# Pixkin v1.5.0

Pixkin v1.5.0 is the public release for the Capability 2.0 milestone. It keeps
the public version line at 1.5 while completing the runtime, privacy, recovery,
character ecosystem, and release hardening work.

## Highlights

- Unified `PixkinKernel`, service container, event bus, action dispatcher, and
  provider registry lifecycle boundaries.
- Default-deny desktop context permissions for window, process, idle, clipboard,
  screen, microphone, plugin, MCP, filesystem, network, and external actions.
- Explicit model-data-scope notice with re-confirmation when the authorized
  context or memory scope changes.
- Per-character chat history, retention controls, export/delete flows, bounded
  memory, redacted tool audits, crash diagnostics, and atomic local backups.
- Resumable character generation with planned-call budgets, bounded retries,
  identity/core/QA/final review gates, candidate history, static and motion QA,
  and safe installation only after review.
- v2 character packages with the unified `192 × 208` canvas, multi-frame
  animation, official SHA-256 verification, and optional Yeye import package.
- Separate voice input/output boundaries, push-to-talk capture, interruption,
  global shortcuts, live-room monitoring, and strict plugin/MCP permission checks.
- Signed HTTPS update-manifest protocol, downgrade protection, user-data backup,
  rollback metadata, and a per-user Inno Setup installer.

## Windows assets

- `Pixkin-1.5.0-win64.zip` — recommended onedir build.
- `Pixkin-Portable-1.5.0.exe` — single-file portable build.
- `Pixkin-Setup-1.5.0.exe` — per-user installer.
- `shanshan.zip`, `linlin.zip`, `pip.zip`, and `yeye.zip` — character packages.
- `SBOM.cdx.json` — CycloneDX dependency inventory.
- `SHA256SUMS.txt` — SHA-256 checksums for every release asset in the package.
- `update-stable.json` — signed stable-channel update manifest.

## Verification

- 459 tests passed; 46 subtests passed.
- Branch coverage: 75.03% (70% release threshold).
- Ruff, architecture checks, and Pyright on Python 3.11 passed.
- Folder, portable, and installer builds completed on Windows 10 x64.
- Folder and portable packaged startup/exit smoke tests passed in isolated
  application-data directories.
- `pip-audit` reported no known vulnerabilities for `requirements-lock.txt`.
- The update manifest signature was verified with the repository public key.

## Known limitations

- Binaries are not commercially code-signed; Windows SmartScreen may display a
  warning on first launch.
- Real microphone, external model, live-platform, multi-monitor hot-plug, and
  clean-machine compatibility checks remain manual acceptance scenarios.
- The application sends only the user-authorized context to the configured
  provider, but it is not an offline AI application; provider charges and
  retention policies depend on the selected service.
