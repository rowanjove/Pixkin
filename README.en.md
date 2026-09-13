# Pixkin — AI desktop companion for Windows

[简体中文](README.md) | [English](README.en.md)

Pixkin is a desktop companion for Windows 10/11 x64, with character animation, model conversations, character-package imports, an image-generation workshop, and Bilibili/Douyin live-stream notifications. Characters respond to clicks and dragging, dock at screen edges, and can remain in the system tray.

[Download v1.5.0](https://github.com/rowanjove/Pixkin/releases/tag/v1.5.0) · [Issues](https://github.com/rowanjove/Pixkin/issues) · [Character package format](CHARACTER_PACKAGE_SPEC.md)

The v1.5.0 public version aligns with the internal Capability 2.0 milestone. Releases bundle the runtime; users do not need to install Python. Chat, transcription, and character generation can call external model services using your configuration and may incur charges. This is not a fully offline AI application.

## Install and get started

Choose a Release asset:

- `Pixkin-1.5.0-win64.zip`: recommended folder build; extract the entire folder and run `Pixkin.exe`.
- `Pixkin-Portable-1.5.0.exe`: single-file build; expands its runtime at launch and may start more slowly.
- `Pixkin-Setup-1.5.0.exe`: installer.

See the [v1.5.0 release notes](docs/RELEASE_NOTES_v1.5.0.md) for changes,
verification evidence, and known limitations.

Shanshan is the default character. Switch to Linlin or Pip in Settings, or import a character ZIP. Click the companion to open its conversation bubble; use the tray for Settings and the workshop.

Current binaries do not have commercial code signing and may trigger SmartScreen or security software warnings. Download only from this repository's Releases and check integrity against `SHA256SUMS.txt`.

## Screenshots

These screenshots were rendered from the v1.5.0 release candidate with an isolated configuration. They contain no real account, API key, or user data.

### Conversation bubble

![Pixkin conversation bubble](docs/screenshots/chat-window.png)

### Settings center

![Pixkin settings center](docs/screenshots/settings-center.png)

### Companion workshop

![Pixkin companion workshop](docs/screenshots/pet-lab.png)

## Conversations, memory, and tools

- Chat Completions-compatible endpoints, streaming responses, and Tool Calls.
- Separate conversations per character, with Markdown/JSON export and selective history deletion.
- History retention options: disabled, 7 days, 30 days, or indefinitely.
- Long-term memory stores individually confirmed items, isolates them by character, and shows which memories informed a response.
- Fast/balanced/deep presets, stop, retry, edit-and-resend, and latency/token/cost estimates.
- Tools for arithmetic, time, system and disk information, HTTP(S) pages, and allowlisted applications.
- Confirmation before opening pages or applications; identical tool-and-argument decisions can be remembered for the current session.
- Inspect, export, or clear tool audits. L2 state-changing and L3 high-risk tools are disabled by default.

Cost estimates and capability checks are not provider billing or capability guarantees. Review the destination and data scope before sending.

## Generate a character from references

Add 1–4 reference images in the workshop, supply a name and personality, and choose the generation scope. Outputs are limited to fictional cartoon companions.

| Mode | Planned image calls | Character states |
| --- | --- | --- |
| Basic | 5 | 1 identity image + 4 core states |
| Standard | 20 | 1 identity image + 19 standard states |
| Full | 45 | 1 identity image + 44 states |

Retries and rework can add calls. The plan and hard limit require confirmation before starting. The default budget reserves 3 extra rework calls; reaching the limit stops generation, and raising it requires user action.

Identity, core actions, and final installation have review steps. Tasks support interruption recovery, failed-action retries, candidate selection, static and motion QA, and GIF previews before installation. Authentication or request errors stop the task; eligible temporary errors use bounded backoff retries, each counted against the budget.

Safe mirroring is available only after confirming complete left/right symmetry and can save 6 calls in full mode. It is unsuitable for asymmetric accessories or text. Changing a source action invalidates its mirrored candidates. Remaining-time estimates use measured task durations and exclude time waiting for human review.

Reference images are copied into the local task directory. Generation sends necessary references and descriptions to the configured endpoint. Pricing, speed, and capability depend on the provider and model. See the [workshop state machine](docs/PET_GENERATION_STATE_MACHINE.md) for details.

## Character packages

Character ZIPs require `character.md` with YAML Front Matter metadata, personality, and action mappings, plus PNG/WebP/JPG assets.

v2 characters use a `192 × 208` canvas and support separate frames, action strips, and atlases. Basic, standard, and full tiers validate their required states. Older 9-action workshop tasks remain resumable but are not automatically promoted to standard tier. The initial official characters use 8×9 transparent atlases.

Shanshan, Linlin, and Pip are bundled; Yeye is a separate ZIP. Built-in characters cannot be deleted. Deleting the active custom character returns to Shanshan. Themes can follow Windows or use light/dark mode.

Imports check path traversal, symlinks, file counts, and extracted size. Built-in SHA-256 manifests verify official packages. Third-party packages show author, license, and fingerprint information without automatically trusting the author's claims.

[Package specification](CHARACTER_PACKAGE_SPEC.md) · [v2 design notes](PIXKIN_DESIGN_SPEC_V2.md)

## Voice, shortcuts, and live notifications

Voice is off by default. The microphone is accessed only while push-to-talk is held; audio stays in memory and is sent to a separate HTTPS transcription endpoint. Transcribed text enters the input box for confirmation before it is sent to the chat model. Shortcuts cover show/hide, open chat, stop generation, and microphone mute.

Monitor multiple Bilibili and Douyin rooms, group them, configure overnight quiet hours, and optionally repeat alerts. Notifications normally follow an offline-to-live transition; startup alerts are configurable. Platform credentials are isolated per room, and external adapters require explicit enabling. Platform changes can affect detection; timely notification of every stream is not guaranteed.

The live-status design draws on [fideo-live-record](https://github.com/chenfan0/fideo-live-record)'s plugin and URL-routing approach. Pixkin does not record streams.

## Data and privacy

- Characters, settings, and logs are under `%LOCALAPPDATA%/Pixkin`; history is in `chat-history.sqlite3`.
- Legacy `%LOCALAPPDATA%/DesktopPet` data migrates on first launch.
- API Keys persist in Windows Credential Manager or can remain in memory for the current session.
- History is stored locally; requesting a response sends selected context to the configured model provider.
- Logs, tool audits, and diagnostics redact common credential patterns; inspect them before sharing.
- Anonymous issue ZIPs use a file allowlist, omit reference/generated images, character names, personalities, and API Keys, and verify sizes and SHA-256. Do not substitute an entire task-directory archive.

## Run from source and test

Requires Python 3.11. Images, videos, atlases, and character ZIPs use Git LFS. Install Git LFS before cloning and ensure assets are downloaded.

```powershell
git lfs install
git clone https://github.com/rowanjove/pixkin.git
cd pixkin
git lfs pull
py -3.11 -m pip install -r requirements.txt
py -3.11 main.py
```

Install locked development/build dependencies and run the quality checks:

```powershell
py -3.11 -m pip install --require-hashes -r requirements-lock.txt
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/run_quality.ps1
```

Checks cover version metadata, compilation, Ruff, Pyright, tests, and branch coverage, with a 70% coverage threshold. To run tests alone:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
py -3.11 -m pytest -q
```

## Build Windows releases

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_release.ps1
```

`release/` contains application builds, character ZIPs, the package specification, an SBOM, SHA-256 checksums, and update metadata for formal releases. Version information comes from `core/version.py`; after changing it, run:

```powershell
py -3.11 scripts/generate_version_info.py
```

After changing top-level dependencies, regenerate the lockfile:

```powershell
py -3.11 -m piptools compile --generate-hashes --strip-extras --output-file requirements-lock.txt requirements-build.txt
```

CI's isolated startup, operating-system, and scaling matrix does not replace manual Windows 10/11 checks such as monitor hot-plugging. Complete [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) before release.

## Further reading and license status

[Architecture](docs/M8_ARCHITECTURE.md) · [Security](docs/M9_SECURITY.md) · [Stability and recovery](docs/M10_STABILITY.md) · [Installation, updates, and rollback](docs/M11_INSTALL_UPDATE.md) · [Product capabilities](docs/M12_PRODUCT_CAPABILITIES.md)

The interface and supporting guides are primarily Chinese. The source code in this repository is licensed under [Apache-2.0](LICENSE). Character packs and other bundled assets may have separate authorship or licensing terms; check each asset's metadata before redistribution.
