#!/usr/bin/env python3
"""Create an isolated test profile and restore it without overwriting other edits."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile

PROFILE_NAME = "Raw Record MVP Test"
CORE_BINARY = Path("/Library/Application Support/org.pqrs/Karabiner-Elements/Karabiner-Core-Service.app/Contents/MacOS/Karabiner-Core-Service")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_config(path, original, updated):
    """Use an atomic replacement and reject changes detected since the read."""
    mode = stat.S_IMODE(path.stat().st_mode)
    fd, temporary = tempfile.mkstemp(prefix=".raw-record-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(json.dumps(updated, indent=4, ensure_ascii=False).encode() + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        if path.read_bytes() != original:
            raise RuntimeError("Configuration changed concurrently; no changes were saved.")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def target_devices(rule, connected=None):
    rules = json.loads(rule.read_text())["rules"]
    targets = {(m["from"]["raw_record"]["vendor_id"], m["from"]["raw_record"]["product_id"])
               for r in rules for m in r["manipulators"] if "raw_record" in m.get("from", {})}
    if not targets:
        return []
    if connected is None:
        cli = "/Library/Application Support/org.pqrs/Karabiner-Elements/bin/karabiner_cli"
        connected = json.loads(subprocess.check_output([cli, "--list-connected-devices"]))
    result = []
    found = set()
    for device in connected:
        ids = device.get("identifiers", device)
        pair = (ids.get("vendor_id"), ids.get("product_id"))
        if pair in targets and not ids.get("is_virtual_device", False):
            normalized = {"vendor_id": pair[0], "product_id": pair[1]}
            for key in ["is_keyboard", "is_pointing_device", "is_game_pad", "is_consumer"]:
                normalized[key] = bool(ids.get(key, False))
            if normalized not in result:
                result.append(normalized)
            found.add(pair)
    if found != targets:
        raise RuntimeError("Connect every target device before creating the test profile.")
    return result


def create(config, backup_root, rule, core_hash, connected=None):
    config = config.resolve(strict=True)
    original = config.read_bytes()
    data = json.loads(original)
    profiles = data["profiles"]
    if any(p.get("name") == PROFILE_NAME for p in profiles):
        raise RuntimeError("The test profile already exists; no changes were saved.")
    selected = [p.get("name") for p in profiles if p.get("selected")]
    if len(selected) != 1 or not selected[0]:
        raise RuntimeError("Exactly one named original profile must be selected.")
    if sum(p.get("name") == selected[0] for p in profiles) != 1:
        raise RuntimeError("The selected profile name is ambiguous.")
    rules = json.loads(rule.read_text())["rules"]
    profile = {"name": PROFILE_NAME, "selected": False,
               "complex_modifications": {"rules": rules}}
    devices = target_devices(rule, connected)
    if devices:
        # A combined keyboard/pointer device is ignored by default in a fresh
        # profile. Enable only the explicitly targeted devices using actual flags.
        profile["devices"] = [{"identifiers": ids, "ignore": False} for ids in devices]
    original_profile = next(p for p in profiles if p.get("selected"))
    if "virtual_hid_keyboard" in original_profile:
        profile["virtual_hid_keyboard"] = original_profile["virtual_hid_keyboard"]
    repo = Path(__file__).resolve().parents[2]
    if backup_root.resolve() == repo or repo in backup_root.resolve().parents:
        raise RuntimeError("Private backups must be stored outside the repository.")
    backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup = Path(tempfile.mkdtemp(prefix="raw-record-", dir=backup_root))
    os.chmod(backup, 0o700)
    snapshot = backup / "karabiner.original.json"
    snapshot.write_bytes(original)
    os.chmod(snapshot, 0o600)
    manifest = {"config": str(config), "original_profile": selected[0],
                "core_sha256": core_hash, "test_profile": profile,
                "original_sha256": digest(original)}
    journal = backup / "state.json"
    journal.write_text(json.dumps(manifest, indent=2) + "\n")
    os.chmod(journal, 0o600)
    profiles.append(profile)
    write_config(config, original, data)
    return journal


def change(journal, action):
    state = json.loads(journal.read_text())
    config = Path(state["config"])
    original = config.read_bytes()
    data = json.loads(original)
    profiles = data["profiles"]
    test_profiles = [p for p in profiles if p.get("name") == PROFILE_NAME]
    if len(test_profiles) != 1:
        raise RuntimeError("The test profile is missing or ambiguous; no changes were saved.")
    target = PROFILE_NAME if action == "activate" else state["original_profile"]
    if sum(p.get("name") == target for p in profiles) != 1:
        raise RuntimeError("The target profile is missing or ambiguous.")
    if action == "activate":
        if not state.get("core_sha256") or digest(CORE_BINARY.read_bytes()) != state["core_sha256"]:
            raise RuntimeError("The installed Core Service does not match the MVP build.")
    for profile in profiles:
        profile["selected"] = profile.get("name") == target
    if action == "remove":
        expected = dict(state["test_profile"])
        current = dict(test_profiles[0])
        expected.pop("selected", None)
        current.pop("selected", None)
        if current != expected:
            raise RuntimeError("The test profile was edited; restore it first and remove it manually.")
        profiles.remove(test_profiles[0])
    write_config(config, original, data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["devices", "create", "activate", "restore", "remove"])
    default_config = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "karabiner/karabiner.json"
    parser.add_argument("--config", type=Path, default=default_config)
    parser.add_argument("--backup-root", type=Path, default=Path.home() / "Library/Application Support/RawRecordMVP/backups")
    parser.add_argument("--rule", type=Path)
    parser.add_argument("--core-sha256")
    parser.add_argument("--state", type=Path)
    parser.add_argument("--devices", type=Path, help="Filtered pre-install device snapshot")
    args = parser.parse_args()
    if args.action == "devices":
        if not args.rule:
            parser.error("devices requires --rule")
        print(json.dumps(target_devices(args.rule), indent=2))
    elif args.action == "create":
        if not args.rule or not args.core_sha256:
            parser.error("create requires --rule and --core-sha256 from the successful package build")
        connected = json.loads(args.devices.read_text()) if args.devices else None
        journal = create(args.config, args.backup_root, args.rule, args.core_sha256, connected)
        print(f"Created an inactive test profile. Private restore journal: {journal}")
    else:
        if not args.state:
            parser.error("--state is required")
        change(args.state, args.action)
        print(f"Profile action completed: {args.action}")


if __name__ == "__main__":
    main()
