# Firmware workbench: first milestone

The user asked to begin making use of the unlocked bootloader. The first
deliverable is the evidence-gathering foundation for customizing firmware,
with stock-based root as the chosen next step after the user delegated direction.
It must also be useful for
evaluating a replacement ROM later. No physical device was connected during
development; all hardware compatibility remains unverified.

## Approaches

- Stock firmware plus a patched boot image and systemless customization:
  preserves the vendor hardware stack and has the smallest initial scope.
  This is the selected direction, not a claim of device compatibility.
- Replacement ROM/GSI: requires separate investigation of vendor services,
  kernel interfaces, SELinux, and device recovery. Deferred until evidence
  establishes a workable target.

Both need the same first milestone: reproducible device and image reports.

## Deliverable

Keep Python 3.10+ and the standard library. Add a separate `boottool.py` CLI:

- `probe -o device.json`: use read-only ADB commands to collect relevant build,
  bootloader, slot, and partition-name evidence. Missing/denied data is explicit.
  Select one device before collecting; refuse ambiguous, offline or unauthorized
  transports. Do not reboot, request root, or write device state.
- `image IMAGE --json`: inspect Android boot headers v0-v4, distinguish vendor
  boot headers, report kernel/ramdisk sizes, hash and file size. Reject malformed,
  truncated and unsupported inputs. Header metadata is not verification of AVB
  signatures, compatibility, or authenticity.
- `prepare --device device.json --image IMAGE --partition boot -o workspace`:
  preserve an exact copy of an explicitly selected stock image, create a hashed
  manifest and a human-readable next-step report. Require a new output directory,
  use only a supported explicit partition name, and record source provenance as
  user-supplied and unverified. No executable flash commands are generated.

The manifest records schema version, device snapshot, source path, copied image
hash/size/header facts, selected partition, and unresolved blockers (firmware
match, unlock confirmation, target partition, recovery procedure). Image presence
or a hash is never treated as evidence that an image is suitable for this device.

## Existing safety prerequisites

Repair plan comments/force handling, invalid plan inputs, missing explicit plan
paths, guarded-package enforcement, failed and repeated restore handling, and the
misleading hard-deny test. Prevent firmware strip from overwriting its input and
make firmware tests portable to Windows. These are prerequisites for continuing
to expose the existing debloat/firmware commands alongside the new workflow.

## Boundaries

No flash/unlock/relock/erase/root/reboot commands are executed by the new tool.
No automatic AVB bypass, MCU changes, guessed boot slot, ROM download, or claim
that stock firmware is trustworthy. Magisk scripts from the original repository
remain experimental and are not installed by this milestone.

## Verification

Exercise binary-header parsing with synthetic fixtures and malformed images;
test missing and ambiguous devices; assert the complete set of commands emitted
by probe is read-only; test atomic preparation, existing destinations, explicit
partition validation and byte-for-byte stock preservation. Add safety regressions
to the existing suite, then run the entire suite on Windows and review the diff.
