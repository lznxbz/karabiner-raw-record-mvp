#!/bin/bash
set -euo pipefail
repo_dir=$(cd "$(dirname "$0")/../.." && pwd)
if [[ $# -lt 2 || $# -gt 4 ]]; then
    echo "Usage: $0 MVP.dmg OFFICIAL-16.3.0.dmg [RULE.json] [karabiner.json]" >&2
    exit 2
fi
mvp_dmg=$(cd "$(dirname "$1")" && pwd)/$(basename "$1")
recovery_dmg=$(cd "$(dirname "$2")" && pwd)/$(basename "$2")
rule_file=${3:-$repo_dir/examples/raw-record/m5-bluetooth.json}
config_file=${4:-${XDG_CONFIG_HOME:-$HOME/.config}/karabiner/karabiner.json}
manifest="$mvp_dmg.raw-record.json"
[[ -f "$manifest" ]] || { echo "Missing package build manifest. Run build.sh package first." >&2; exit 1; }
python3 - "$manifest" "$mvp_dmg" "$recovery_dmg" <<'PY'
import hashlib, json, pathlib, plistlib, sys
manifest, mvp, recovery = map(pathlib.Path, sys.argv[1:])
state = json.loads(manifest.read_text())
installed = pathlib.Path('/Applications/Karabiner-Elements.app/Contents/Info.plist')
if not installed.exists() or plistlib.loads(installed.read_bytes()).get('CFBundleShortVersionString') != '16.3.0':
    raise SystemExit('This baseline requires an existing official 16.3.0 installation.')
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
if sha(mvp) != state['dmg_sha256']:
    raise SystemExit('MVP image does not match the successful package build.')
if sha(recovery) != '19cce7bed3d48a722242ca683bd1bae406b6a87fed520728d1c91dee175b75e4':
    raise SystemExit('Recovery image does not match the official 16.3.0 image used for this baseline.')
PY
backup_root="$HOME/Library/Application Support/RawRecordMVP/backups"
mkdir -p "$backup_root"
backup_dir=$(mktemp -d "$backup_root/install.XXXXXX")
chmod 700 "$backup_dir"
cp "$recovery_dmg" "$backup_dir/Karabiner-Elements-16.3.0.dmg"
cp "$manifest" "$backup_dir/build.json"
python3 - "$config_file" "$backup_dir" <<'PY'
from pathlib import Path
import json, os, sys
config = Path(sys.argv[1]).resolve(strict=True)
backup = Path(sys.argv[2])
snapshot = backup / 'karabiner.before-install.json'
snapshot.write_bytes(config.read_bytes())
os.chmod(snapshot, 0o600)
(backup / 'config-path.txt').write_text(str(config) + '\n')
os.chmod(backup / 'config-path.txt', 0o600)
PY
# Capture only the target type flags while the official CLI still communicates
# with its existing services. No addresses or serial numbers enter the snapshot.
python3 "$repo_dir/scripts/raw-record/profile.py" devices --rule "$rule_file" > "$backup_dir/devices.json"
mount_dir=$(mktemp -d /private/tmp/raw-record-install.XXXXXX)
mounted=false
cleanup() {
    if [[ $mounted == true ]]; then hdiutil detach "$mount_dir" >/dev/null; fi
    rmdir "$mount_dir" 2>/dev/null || true
}
trap cleanup EXIT
hdiutil attach -readonly -nobrowse -mountpoint "$mount_dir" "$mvp_dmg" >/dev/null
mounted=true
package_file="$mount_dir/Karabiner-Elements.pkg"
[[ -f "$package_file" ]] || { echo "Unexpected package layout." >&2; exit 1; }
pkgutil --check-signature "$package_file"
echo "Installing the complete Karabiner package under /Applications and /Library/Application Support/org.pqrs."
echo "Recovery files and configuration backup: $backup_dir"
sudo /usr/sbin/installer -pkg "$package_file" -target /
core_hash=$(python3 - "$manifest" <<'PY'
import json, sys
print(json.load(open(sys.argv[1]))['core_sha256'])
PY
)
python3 "$repo_dir/scripts/raw-record/profile.py" create --config "$config_file" \
    --rule "$rule_file" --core-sha256 "$core_hash" --backup-root "$backup_root" --devices "$backup_dir/devices.json"
echo "Installed. Re-grant macOS permissions for the new signer as described in README.md."
echo "The original profile remains selected. Activate the test profile only after permissions are ready."
