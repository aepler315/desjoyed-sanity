# Firmware Workbench Implementation Plan

> **For agentic workers:** Use test-driven development and independent review.

**Goal:** Begin unlocked-bootloader work with evidence-based device/image intake.

**Architecture:** Keep existing CLI compatibility; add `boottool.py` for read-only
device inventory and offline image preparation. Isolate Android binary parsing in
`bootimage.py`. Preserve originals and explicitly carry unknowns into reports.

**Tech Stack:** Python 3.10+, standard library, unittest, ADB for read-only probe.

**Spec:** `docs/superpowers/specs/2026-11-03-firmware-workbench-design.md`

## Global constraints

- All work stays in the fresh checkout on `codex/firmware-workbench`.
- No commands that modify a connected device; no automatic image downloads.
- No new third-party dependencies; no fabricated compatibility claims.

## Review focus

- A commented or malformed plan must never run an ADB mutation.
- Failed restoration must remain pending for retry.
- Probe must pin its ADB transport and report incomplete evidence honestly.
- Malformed/truncated images must not look like usable stock images.
- Preparation must preserve input bytes and refuse existing destinations.

## Task 1: Repair the existing safety boundary

Files: `sanity.py`, `tests/test_sanity.py`, optionally `tests/fake_adb.py`.

- [x] Add failing regressions for commented force, explicit force, plain guarded
  targets, invalid package syntax, absent explicit plan, repeated undo, and failed
  restore retry. Correct hard-deny test journal scope.
- [x] Implement minimal parser and journal fixes. Keep never rules absolute.
- [x] Run `python -m unittest tests.test_sanity -v`.

## Task 2: Parse boot images and harden firmware stripping

Files: `bootimage.py`, `tests/test_bootimage.py`, `fwtool.py`, `tests/test_fwtool.py`.
Interface: `inspect_image(path: Path) -> dict` returns JSON-serializable header
facts with `kind`, `header_version`, `kernel_size`, `ramdisk_size`, `size_bytes`,
`sha256`; unsupported/malformed images raise `ValueError`.

- [x] Add failing tests for boot v0-v4 and vendor boot v3-v4, absent/truncated
  sections, unsupported versions and same-file firmware strip.
- [x] Implement bounded header parsing, streamed hashing and structural checks.
- [x] Fix Windows temporary-file lifecycle in the existing firmware tests.
- [x] Run `python -m unittest tests.test_bootimage tests.test_fwtool -v`.

## Task 3: Read-only device probe and local preparation

Files: `boottool.py`, `tests/test_boottool.py`, `README.md`, `docs/firmware.md`.
Interfaces: `collect_device(adb: Adb) -> dict`,
`prepare_workspace(device: dict, image: Path, partition: str, output: Path) -> dict`.

- [x] Write failing tests for device selection, collection failures and command
  allowlist, image/partition validation, destination refusal and exact copying.
- [x] Implement `probe`, `image`, `prepare`; write JSON in UTF-8; return nonzero
  on invalid inputs and keep partial probe failures visible in the report.
- [x] Document practical commands and explicitly distinguish readiness from
  image authenticity/device compatibility. Replace blanket flash instructions.
- [x] Run complete `python -m unittest discover -v`, CLI help and offline smoke.

## Task 4: Review and completion

- [x] Independent review of actual diff, safety boundaries and tests.
- [x] Fix material findings with regression tests, run complete suite again.
- [x] Report available commands, test evidence, local path and missing device
  evidence. Do not publish firmware or flash as part of this milestone.

## Implementation record

Implemented on `codex/firmware-workbench`. Baseline: 45 tests, 8 Windows fixture
errors. Final: 104 tests pass on Python 3.14.5 / Windows, plus an offline CLI
smoke test covering image JSON and prepared workspace bytes/manifest.

Independent review found a host-locale decoding bug in the shared ADB runner;
an actual UTF-8 subprocess regression failed before the fix and passes afterward.
The runner now explicitly decodes UTF-8. Preparation coverage also includes
kernel-less init_boot, vendor_boot, and source-change detection during copying.

The fresh checkout provides isolation; no second worktree was needed. No
third-party dependencies were installed. `adb devices -l` showed no connected
device; real probe correctly failed without writing a snapshot. Hardware,
stock-image compatibility, fastboot and recovery remain unverified. No device
mutations or firmware flashing occurred. A draft PR records the completed first
milestone; deployment and flashing remain separate work.
