# Raw Record MVP

This is an experimental Karabiner-Elements fork based on **v16.3.0**, commit
`9312593e1` (the full baseline is available through the Git tag). It adds an input-only
`from.raw_record` to basic complex modifications. It does not modify the GUI or
build a new virtual HID driver. The upstream driver package is reused unchanged.

## Scope and current validation

The first target is an M5 remote's Power button, mapped to one F13 click without
also invoking the system power action. Software tests and compilation do **not**
prove that the hardware's default action has been suppressed.

- The Bluetooth descriptor exposes VID 25098, PID 1031, System Control collection
  `0x01/0x80`, report ID 3, and a one-byte input payload.
- The USB example uses VID 6421, PID 4133 from prior protocol observations. Recheck
  the USB descriptor and actual reports on the target Mac before using that example.
- The configured Bluetooth press `[3, 1]` and release `[3, 0]` patterns were exercised
  by the physical tests of the isolated application described below.
- Eight relevant C++ suites passed (2,488 assertions in total), including 83
  raw-record assertions. Six Python profile-helper tests also passed.
- The complete application suite also compiled after backporting the two upstream
  Xcode 27 fixes listed below. Eleven application bundles (including embedded helpers)
  and the CLI passed signature verification with the same Apple Development certificate.
  The resulting local-development image contains an **unsigned installer package**;
  it is not a Developer ID-signed distribution package and does not satisfy the
  standard `install.sh` signature guard. This earlier replacement-installation artifact
  was not installed; use the separate application route below.
- Core Service has been compiled for arm64 and x86_64. A compiled unsigned app is
  **not an installable replacement** for the official signed app.
- The [separate application](raw-record-parallel.md#validation) was subsequently
  installed and tested with the Bluetooth M5. Nine physical Power operations produced
  exactly nine F13 clicks, including long hold, reconnect, sleep/wake, and isolated
  profile switching. The user confirmed no additional system power actions. The
  official application was restored with its saved file/configuration fingerprints
  and service state unchanged. USB and replacement installation remain untested.

No Home/Back rules, Kodi changes, firmware changes, or system power-policy changes
are included.

## JSON

Use `examples/raw-record/m5-bluetooth.json` or `m5-usb.json` as a starting point.
The syntax differs from earlier design sketches: device VID and PID are mandatory
inside the input definition so capture scope is explicit before rule conditions run.

```json
{
  "type": "basic",
  "from": {
    "raw_record": {
      "vendor_id": 25098,
      "product_id": 1031,
      "collection_usage_page": 1,
      "collection_usage": 128,
      "report_id": 3,
      "payload_length": 1,
      "key_down": [1],
      "key_up": [0]
    }
  },
  "to": [{ "key_code": "f13", "repeat": false }]
}
```

All fields are required. Values must be integers, not strings, floats, or booleans.
VID/PID and collection page/usage are 0–65535; report ID is 0–255. Payload length
is 1–4096 bytes. Down/up arrays must have exactly that length, contain bytes 0–255,
and differ. Unknown fields are errors. No masks, wildcards, or partial patterns exist.

Payloads exclude the report ID. A descriptor-declared nonzero ID requires the macOS
callback's leading ID byte and an exact length of `payload_length + 1`; the prefix
must equal the callback ID. An unnumbered report uses ID 0 and has no prefix. The
implementation never guesses from the first payload byte or accepts both layouts
ambiguously. The descriptor must confirm the report, collection, and payload length.

`from.raw_record` cannot be combined with another input type, `integer_value`, or
`simultaneous` options. Existing `from.modifiers` and manipulator conditions remain
available. There is no `to.raw_record`, `to.raw_record_key`, or passthrough
`to.from_event` for raw inputs, including delayed outputs. Use ordinary `to` actions.

Only successfully constructed, enabled manipulators activate capture. Conditions
still run in the normal rule engine; capture of their explicitly scoped device is
independent of whether an application condition currently matches. `device_if`
conditions may further narrow rule evaluation but are not used to infer capture scope.

Use this feature only for inputs the normal pipeline does not already translate.
There is no deduplication between ordinary keys and raw-derived inputs. A composite
report is never discarded: its ordinary IOHIDQueue values continue through the existing
pipeline. An unrecognized raw payload does not imply release; only exact `key_up`
releases the logical input. A missing release can therefore keep it logically pressed
until device teardown or a reset.

## Implementation

`raw_record_definition` validates the input; a process-local registry gives identical
physical definitions the same stable source ID, regardless of application conditions.
IDs never get reused during the process lifetime. The input is represented by an
internal negative usage page (`-1`, unused by the v16.3.0 baseline) and a source ID,
not a real key such as F24. Recheck page allocation when rebasing onto newer upstream.

The report handler emits only down/up transitions, separately for each IOHIDDevice.
Its definitions are immutable; pressed-state mutation stays on the shared dispatcher.
If a vendor handler also exists, a composite retains it and executes child filtering
on the dispatcher to avoid concurrent access to vendor filter state.

The monitor keeps its external signals while replacing the underlying IOKit monitor
when active definitions change. It detaches the old monitor, emits the existing
ungrab lifecycle signal for seized devices, clears pressed state, and recreates capture.
The grabber then reapplies virtual-device readiness and device-ignore gates. Normal
configuration edits that leave physical definitions unchanged retain input identity
and held-state handling. Profile changes can briefly ungrab the affected physical
interface; validate this transition on hardware, particularly while keys are held.

System Control collections are discoverable, but a device with no supported ordinary
type is seized only when a descriptor-validated definition targets it and it is not
ignored. Virtual devices, temporarily ignored devices, and disabled/ready-state
behavior retain the existing safety gates. Ordinary keyboard/consumer/pointer device
ignore preferences remain authoritative. Device selection uses VID/PID, not unique
serial numbers or Bluetooth addresses; multiple identical devices may match, each with
independent pressed state.

Unmatched internal raw keys are consumed before virtual-device output. Their
`raw_record_key` JSON representation exists for diagnostics and event serialization,
not as a user-facing output type.

## Build and test

Requirements are the upstream macOS/Xcode requirements plus Python 3 and the build
tools listed in the upstream README. Install CMake, XcodeGen and xz if missing.
The first configure downloads dependencies pinned by the upstream submodule lock.

```sh
bash scripts/raw-record/build.sh core
bash scripts/raw-record/test.sh
```

The first command compiles Core Service only, with code signing disabled for the
build. It never installs or starts it. The second runs raw-record tests plus relevant
upstream suites under AddressSanitizer and tests the isolated-profile helper.

For an installable build, first create the appropriate Apple signing identities as
explained in the upstream README. Set their hashes in
`PQRS_ORG_CODE_SIGN_IDENTITY` and `PQRS_ORG_INSTALLER_CODE_SIGN_IDENTITY`, then run:

```sh
bash scripts/raw-record/build.sh package
```

This tests first and builds/signs the **complete** upstream application package,
using one consistent application signer. It writes a local DMG manifest with image
and installed Core Service hashes. Do not replace only Core Service inside an
official installation, and do not install the unsigned `core` build.

Apple Development was verified for application signing on the target Mac after
installing Apple's missing WWDR G3 intermediate certificate. It was rejected by
`productsign` for installer signing. An app-signing certificate alone therefore does
not satisfy this script's signed-package installation route. A usable installer
signing identity is required as well; package mode now tests that capability before
compiling and checks the actual generated package before creating an install manifest.
The upstream packaging script can continue after an installer-signing failure, so
an emitted DMG alone is not evidence of an installable signed package.

Xcode 27 full-suite compilation also requires upstream compatibility fixes
`932f051a2b8f5b45f3259066c88a966c2eb6d443` and `2d49576b0`, backported here.
They fix two Swift state initializers and explicitly handle cancellation of an
updater task. No GUI controls are added.

The package/install scripts have preflight guards and syntax checks, but cannot be
validated end to end without valid signing identities and an approved installation.
Certificates, private keys, and tokens must never enter the repository.

## Install, test profile, and recovery

Download the official `Karabiner-Elements-16.3.0.dmg` from the upstream release first.
This baseline's recovery image has SHA-256:

```text
19cce7bed3d48a722242ca683bd1bae406b6a87fed520728d1c91dee175b75e4
```

The recovery package was checked for the upstream Developer ID Installer signature
with Team ID `G43BCU2T37` and Apple notarization. Store the recovery image outside Git.

```sh
bash scripts/raw-record/install.sh MVP.dmg OFFICIAL-16.3.0.dmg
```

The installer validates both image hashes and the package signature, backs up the
existing configuration outside the repository, saves a recovery image, and invokes
the system installer with administrator privileges. The complete app suite is replaced
under `/Applications` and `/Library/Application Support/org.pqrs`; profile isolation
alone does not roll those applications back.

By default it uses the Bluetooth example. An optional third argument selects another
rule file; a fourth supplies the actual config file. Otherwise it honors
`XDG_CONFIG_HOME`, falling back to `~/.config/karabiner/karabiner.json`. Config symlinks
are resolved to their real targets. Backups and journals are private files outside Git.

A signer change requires re-granting Karabiner permissions in macOS. Follow the
upstream instructions for background services, Input Monitoring and Accessibility.
Do not disable SIP, Gatekeeper, or startup security to make this work.

The installer creates an **inactive** `Raw Record MVP Test` profile; it preserves the
original active profile. It explicitly enables only connected rule-target devices using
their actual keyboard/pointer/consumer flags, including combined Bluetooth devices.
The helper prints its private state-journal location. After
permissions are ready, activate it with:

```sh
python3 scripts/raw-record/profile.py activate --state /path/to/state.json
```

Activation verifies that installed Core Service matches the signed build's hash.
Save open work before the first physical Power test. With EventViewer, check one F13
down/up per press, no repeat on hold, correct rearming after release, and no sleep,
shutdown, or power dialog. Also check Home/Back/List, arrows, rear keyboard, other
keyboards, disconnect/reconnect, sleep/wake, and profile switching while held.
A log, a synthetic report, or a successful compile cannot substitute for these checks.

Restore the original active profile after testing:

```sh
python3 scripts/raw-record/profile.py restore --state /path/to/state.json
```

To restore the full application suite as well:

```sh
bash scripts/raw-record/rollback.sh OFFICIAL-16.3.0.dmg /path/to/state.json
```

The rollback script verifies the pinned recovery image and the upstream signer,
restores the original profile selection, and reinstalls the official complete package.
Permissions may need to be granted again. Whole-application rollback itself has not
been executed during software-only validation. The helper never blindly restores a
whole configuration snapshot over newer edits. `profile.py remove --state ...` removes
only the unchanged test profile; it refuses if that profile has since been edited.

Automatic official updates can replace this experimental build. Recheck the installed
binary and revalidate the patch whenever upgrading or rebasing.

## License and upstream

Original project: <https://github.com/pqrs-org/Karabiner-Elements>.
Upstream history and `LICENSE.md` are retained. This fork does not imply upstream
support for the experimental JSON syntax. Personal configurations, full input logs,
addresses, serials, signing material, and private handoff documents are not included.
