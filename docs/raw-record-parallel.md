# Parallel installation and explicit switching

The parallel build preserves the installed official Karabiner-Elements. It is a
separate local-development application suite with independent paths and identities:

- Settings: `/Applications/RawRecordMVP-Elements.app`
- EventViewer: `/Applications/RawRecordMVP-EventViewer.app`
- Helpers: `/Library/Application Support/org.pqrs.rawmvp/RawRecordMVP-Elements`
- User configuration: `$XDG_CONFIG_HOME/karabiner-raw-record-mvp/karabiner.json`, or
  `~/.config/karabiner-raw-record-mvp/karabiner.json` when XDG_CONFIG_HOME is unset
- User data/logs: the corresponding `karabiner-raw-record-mvp` XDG data directory
- System core log: `/var/log/raw-record-mvp/core_service.log`
- Runtime files and sockets: `/Library/Application Support/org.pqrs.rawmvp/tmp`
- Application identifiers start with `org.pqrs.RawRecordMVP`; launchd labels use
  `RawRecordMVP-` rather than the official `Karabiner-` component names.

The verified official baseline is **16.3.0**. The existing official virtual HID
extension and daemon are reused. They are not packaged, reinstalled, registered,
unregistered, activated, or deactivated by the parallel application.

## Build

Keep a valid Apple Development application-signing identity in the login keychain.
The Apple WWDR intermediate required by that certificate must also be available.
Set the application identity hash in `PQRS_ORG_CODE_SIGN_IDENTITY`, then run:

```sh
bash scripts/raw-record/parallel/build.sh
```

The build prepares an isolated source tree under the ignored `.raw-record-local`
directory. The ordinary source tree retains its original build behavior. Pinned
vendor dependencies are shared unmodified. The generated tree receives independent
application names, bundle identifiers, launchd labels, IPC and configuration paths.
Automatic updates and shared-driver lifecycle actions are disabled in that tree.

The output includes `RawRecordMVP-Parallel.pkg` and `parallel-package.json`. All
application bundles and the CLI are signed with the same development certificate.
The outer installer package is unsigned: this is a local-development artifact, not
a Developer ID-signed/notarized distribution installer. Signing certificates,
private configuration, local logs, and signed binaries must not be published with
source updates.

The package builder accepts only the two new application locations and the separate
helper directory. Every top-level bundle is nonrelocatable. It includes no installer
scripts, no official-uninstall action, and no driver component. Before installation,
the install helper expands the actual package and repeats the payload and signature
checks. The reviewed bytes are staged in a private temporary directory for the
authenticated installer, preserving extended attributes and their package hash.
Shared-driver and original-application destinations are rejected.

## Install without replacing the official application

Use the parallel install helper, not `scripts/raw-record/install.sh` or the ordinary
upstream installer; those are replacement-installation routes.

```sh
python3 scripts/raw-record/parallel/install.py \
  /path/to/RawRecordMVP-Parallel.pkg /path/to/parallel-package.json
```

The Bluetooth M5 must be connected. This helper verifies the package hash, rejects
aliased destination paths, snapshots original executable/configuration/service-plist
fingerprints, and invokes the normal system installer. Administrator authentication
is requested by macOS. No system trust policy, Gatekeeper, SIP, or installer
allow-untrusted override is used. If the installer rejects the package, stop.

After installation, the helper verifies the original fingerprints remain unchanged
and checks the installed isolated executables and switch helper. It creates a separate
Power-to-F13 profile, including the actual combined keyboard/pointer device flags.
Existing isolated configuration is preserved. The official profile is never copied,
selected, replaced, or rewritten; only its virtual keyboard type is read when creating
a fresh isolated configuration.

## Grant permissions before switching

The switch helper is installed at:

```text
/Library/Application Support/org.pqrs.rawmvp/RawRecordMVP-Elements/tools/raw-record-switch.py
```

Run its `setup` action as the logged-in user:

```sh
python3 "/Library/Application Support/org.pqrs.rawmvp/RawRecordMVP-Elements/tools/raw-record-switch.py" setup
```

Grant the required background-service, Accessibility, and Input Monitoring permissions
to the **RawRecordMVP** applications. Leave the official permissions unchanged.

Simply opening the isolated Settings application does not activate its input engine.
The root core requires a root-owned activation marker for the current boot session,
and refuses to start while the verified official core is running. The marker can only
be written by the installed switch helper's narrowly scoped administrator entry point.

## Switch and restore

Run the helper as the normal logged-in user, without `sudo`:

```sh
python3 "/Library/Application Support/org.pqrs.rawmvp/RawRecordMVP-Elements/tools/raw-record-switch.py" status
python3 "/Library/Application Support/org.pqrs.rawmvp/RawRecordMVP-Elements/tools/raw-record-switch.py" developer
```

The developer action re-registers an already approved isolated daemon after a prior
bootout, then checks its macOS Service Management approval. Both switch and restore
require a valid device-list response from the backend; a running PID or the upstream
CLI exit code alone does not prove communication. It saves the original loaded-service state in a
private journal, closes the official Settings/EventViewer windows, unloads the official
user agents and core, and activates the isolated core for this boot session. It keeps
the official virtual HID daemon running. Original launchd startup preferences are not
disabled; temporary `bootout` operations are used. A lock prevents concurrent switches.

If startup fails or administrator authentication is canceled, the helper attempts to
restore the saved official state. A pending recovery journal cannot be overwritten
by another developer switch. The privileged helper accepts only fixed service names,
fixed application locations, and enumerated actions; it does not execute user-supplied
shell fragments or labels. It runs Python in isolated mode.

After testing, restore the original application:

```sh
python3 "/Library/Application Support/org.pqrs.rawmvp/RawRecordMVP-Elements/tools/raw-record-switch.py" original
```

This unloads the isolated core/agents, clears its activation marker, and restores the
original loaded-service state. Application files and profile contents are not restored
from snapshots because they were never overwritten. Configuration edits made elsewhere
are not discarded. The official Settings window may be reopened normally afterward.

A new boot invalidates the isolated activation marker. The official startup preferences
remain intact. If a switch was interrupted, run `original` to finish restoring the saved
state before starting another test. Hardware sleep/wake, crash recovery, login/reboot,
and service transition behavior still require actual-machine validation; software tests
cannot establish those results by themselves.

Do not press Power for the first hardware test until device grabbing is confirmed and
open work is saved. Verify one F13 click per press, no default power action, rearming,
long hold, reconnect, ordinary keys, and restoration of the original profile behavior.

## Optional F13 hardware-test recorder

The isolated EventViewer includes an opt-in observer for hardware verification:

```sh
open -a /Applications/RawRecordMVP-EventViewer.app --args --record-f13
```

Start it while the viewer is closed, grant its own Input Monitoring permission if
requested, and keep the ordinary **Capture Input Events** view selected. Raw-input
capture views can release the selected physical device and must not be used during
the Power mapping test. The normal event capture observes virtual F13 output without
releasing the M5.

The recorder is disabled without the argument. It appends only F13 down/up and times
to `f13-test.jsonl` in the isolated XDG data directory, creating its directory with
mode 0700 and file with mode 0600. It never records other keys, text, raw reports or
device identifiers. These private test logs must not be committed or published.
The observer has no role in the mapping pipeline and cannot establish the absence
of system power actions; that still requires the user's physical test and observation.

## Remove only the isolated installation

```sh
python3 "/Library/Application Support/org.pqrs.rawmvp/RawRecordMVP-Elements/tools/raw-record-switch.py" uninstall
```

The helper first restores a pending switch, unregisters only isolated services, and
removes only the two RawRecordMVP applications and their helper directory. It leaves
the official application, its configuration, and the shared virtual driver untouched.
Private isolated configuration and recovery journals are retained.
