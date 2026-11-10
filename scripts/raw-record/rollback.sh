#!/bin/bash
set -euo pipefail
repo_dir=$(cd "$(dirname "$0")/../.." && pwd)
if [[ $# -lt 1 || $# -gt 2 ]]; then
    echo "Usage: $0 OFFICIAL-16.3.0.dmg [profile-state.json]" >&2
    exit 2
fi
recovery_dmg=$(cd "$(dirname "$1")" && pwd)/$(basename "$1")
expected=19cce7bed3d48a722242ca683bd1bae406b6a87fed520728d1c91dee175b75e4
actual=$(shasum -a 256 "$recovery_dmg" | awk '{print $1}')
[[ $actual == "$expected" ]] || { echo "Recovery image checksum mismatch." >&2; exit 1; }
# Restore only profile selection; never overwrite the user's entire live config.
if [[ $# == 2 ]]; then
    python3 "$repo_dir/scripts/raw-record/profile.py" restore --state "$2"
fi
mount_dir=$(mktemp -d /private/tmp/raw-record-rollback.XXXXXX)
mounted=false
cleanup() {
    if [[ $mounted == true ]]; then hdiutil detach "$mount_dir" >/dev/null; fi
    rmdir "$mount_dir" 2>/dev/null || true
}
trap cleanup EXIT
hdiutil attach -readonly -nobrowse -mountpoint "$mount_dir" "$recovery_dmg" >/dev/null
mounted=true
package_file="$mount_dir/Karabiner-Elements.pkg"
signature=$(pkgutil --check-signature "$package_file")
printf '%s\n' "$signature"
printf '%s\n' "$signature" | grep -F '(G43BCU2T37)' >/dev/null || {
    echo "The recovery package is not signed by the expected upstream developer." >&2; exit 1;
}
echo "Reinstalling the complete official 16.3.0 package. macOS permissions may need to be re-granted."
sudo /usr/sbin/installer -pkg "$package_file" -target /
echo "Official package restored. Configuration backups were preserved."
