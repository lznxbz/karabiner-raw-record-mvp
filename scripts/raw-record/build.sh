#!/bin/bash
set -euo pipefail
repo_dir=$(cd "$(dirname "$0")/../.." && pwd)
cd "$repo_dir"
mode=${1:-core}
[[ $(uname -s) == Darwin ]] || { echo "macOS is required." >&2; exit 1; }
for tool in cmake xcodegen xcodebuild xz; do
    command -v "$tool" >/dev/null || { echo "Missing build tool: $tool" >&2; exit 1; }
done
case "$mode" in core|test|package) ;; *) echo "Usage: $0 [core|test|package]" >&2; exit 2 ;; esac
if [[ $mode == package ]]; then
    : "${PQRS_ORG_CODE_SIGN_IDENTITY:?Set a valid application signing identity.}"
    : "${PQRS_ORG_INSTALLER_CODE_SIGN_IDENTITY:?Set a valid installer signing identity.}"
    [[ $PQRS_ORG_CODE_SIGN_IDENTITY != - && $PQRS_ORG_INSTALLER_CODE_SIGN_IDENTITY != - ]] || {
        echo "Ad hoc signing is not supported for installation." >&2; exit 1;
    }
    security find-identity -v -p codesigning | grep -F "$PQRS_ORG_CODE_SIGN_IDENTITY" >/dev/null || {
        echo "The application signing identity is not available." >&2; exit 1;
    }
    security find-identity -v -p basic | grep -F "$PQRS_ORG_INSTALLER_CODE_SIGN_IDENTITY" >/dev/null || {
        echo "The installer signing identity is not available." >&2; exit 1;
    }
fi
git submodule update --init --recursive
cmake -S vendor -B vendor/build
cmake --build vendor/build
make -C vendor/duktape-src
if [[ $mode == test ]]; then
    exec bash scripts/raw-record/test.sh --prepared
elif [[ $mode == core ]]; then
    python3 scripts/update_version.py
    cd src/apps/CoreService
    xcodegen generate
    xcodebuild -configuration Release -alltargets CODE_SIGNING_ALLOWED=NO SYMROOT="$PWD/build"
    echo "Core Service compiled only. This unsigned build is not installable."
else
    bash scripts/raw-record/test.sh --prepared
    make package
    package_file="$repo_dir/Karabiner-Elements-$(cat version).dmg"
    python3 - "$package_file" "$repo_dir" <<'PYMANIFEST'
import hashlib, json, pathlib, subprocess, sys
image, repo = map(pathlib.Path, sys.argv[1:])
core = repo / 'pkgroot/Library/Application Support/org.pqrs/Karabiner-Elements/Karabiner-Core-Service.app/Contents/MacOS/Karabiner-Core-Service'
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
state = {'dmg_sha256': sha(image), 'core_sha256': sha(core),
         'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()}
pathlib.Path(str(image) + '.raw-record.json').write_text(json.dumps(state, indent=2) + '\n')
PYMANIFEST
    echo "Full package built. Use install.sh with this DMG and an official recovery DMG."
fi
