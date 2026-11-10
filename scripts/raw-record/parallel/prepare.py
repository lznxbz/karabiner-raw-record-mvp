#!/usr/bin/env python3
"""Prepare an isolated build tree without changing the ordinary Karabiner build."""
from pathlib import Path
import argparse
import io
import hashlib
import json
import re
import subprocess
import tarfile

SUPPORT = '/Library/Application Support/org.pqrs.rawmvp'
PRODUCT = 'RawRecordMVP-Elements'
CONFIG = 'karabiner-raw-record-mvp'


def transform(text):
    # The existing, officially signed virtual driver and its daemon stay shared.
    protected = [
        '/Library/Application Support/org.pqrs/Karabiner-DriverKit-VirtualHIDDevice',
        '/Library/Application Support/org.pqrs/Karabiner-VirtualHIDDevice',
        'Karabiner-DriverKit', 'Karabiner-VirtualHID',
    ]
    for i, item in enumerate(protected):
        text = text.replace(item, f'__RAW_MVP_SHARED_{i}__')
    text = re.sub(re.escape('/Library/Application Support/org.pqrs')+r'(?![A-Za-z0-9_.-])', SUPPORT, text)
    text = text.replace('Karabiner-', 'RawRecordMVP-')
    text = text.replace('org.pqrs.Karabiner', 'org.pqrs.RawRecordMVP')
    text = text.replace('"karabiner"', f'"{CONFIG}"')
    text = text.replace('<string>karabiner</string>', f'<string>{CONFIG}</string>')
    text = text.replace('karabiner://', f'{CONFIG}://')
    text = text.replace('/var/log/karabiner/core_service.log', '/var/log/raw-record-mvp/core_service.log')
    for i, item in reversed(list(enumerate(protected))):
        text = text.replace(f'__RAW_MVP_SHARED_{i}__', item)
    return text


def replace_once(path, old, new):
    text = path.read_text()
    if text.count(old) != 1:
        raise RuntimeError(f'Unexpected upstream structure: {path.name}')
    path.write_text(text.replace(old, new, 1))


def prepare(repo, destination):
    if destination.exists():
        raise RuntimeError('The isolated build directory already exists; choose a new directory.')
    destination.mkdir(parents=True)
    archive = subprocess.check_output(['git', 'archive', 'HEAD'], cwd=repo)
    with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
        stream.extractall(destination, filter='data')
    # Never transform or copy the vendor tree. Both variants use the pinned
    # unmodified upstream headers, libraries and official virtual driver.
    for path in sorted(destination.rglob('*')):
        if not path.is_file() or 'vendor' in path.relative_to(destination).parts:
            continue
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        path.write_text(transform(text))
    for path in sorted(destination.rglob('*'), key=lambda p: len(p.parts), reverse=True):
        if 'vendor' in path.relative_to(destination).parts:
            continue
        name = transform(path.name)
        if name != path.name:
            path.rename(path.with_name(name))

    # Do not register, unregister, reinstall or activate the official driver.
    manager = destination / 'src/apps/ServiceManager-Privileged-Daemons'
    path = manager / 'src/main.swift'
    replace_once(path, '    "org.pqrs.service.daemon.Karabiner-VirtualHIDDevice-Daemon",\n', '')
    path = manager / 'Makefile'
    lines = path.read_text().splitlines(keepends=True)
    path.write_text(''.join(line for line in lines if 'vendor/Karabiner-DriverKit-VirtualHIDDevice/files/LaunchDaemons' not in line))
    path = destination / 'src/apps/ConsoleUserServer/src/runtime.cpp'
    replace_once(path, '  system("/Applications/.Karabiner-VirtualHIDDevice-Manager.app/Contents/MacOS/Karabiner-VirtualHIDDevice-Manager forceActivate &");',
                 '  // The parallel build reuses the installed official virtual driver.')
    path = destination / 'src/apps/SettingsWindow/src/VirtualHIDDeviceManager.swift'
    text = path.read_text()
    start = text.index('  private func run(argument:')
    path.write_text(text[:start] + '''  private func run(argument: String, completion: @escaping (Int32) -> Void) {
    // Shared driver lifecycle belongs to the official installation.
    completion(0)
  }
}
''')
    path = destination / 'src/share/services_utility.hpp'
    text = path.read_text()
    start = text.index('inline void bootout_old_agents() {')
    end = text.index('\n}\n', start) + len('\n}\n')
    text = text[:start] + '''inline void bootout_old_agents() {
  // The parallel build must not unload any official or legacy services.
}
''' + text[end:]
    path.write_text(text)
    # The updater must never install an official replacement package over either
    # application. Updating the isolated build is an explicit build/install step.
    path = destination / 'src/apps/Updater/src/Updater.swift'
    text = path.read_text().replace('updaterController.updater.checkForUpdatesInBackground()', 'return')
    text = text.replace('updaterController.checkForUpdates(nil)', 'return')
    path.write_text(text)
    # A freshly opened isolated GUI cannot compete with a running official core.
    path = destination / 'src/apps/CoreService/src/main.cpp'
    replace_once(path, '#include \"dispatcher_utility.hpp\"', '#include \"dispatcher_utility.hpp\"\n#include \"services_utility.hpp\"\n#include <array>\n#include <fstream>\n#include <sys/sysctl.h>')
    replace_once(path, '  if (root) {\n', '''  if (root) {
    std::array<char, 128> boot_session{};
    size_t boot_session_size = boot_session.size();
    std::string activated_session;
    std::ifstream activation("/Library/Application Support/org.pqrs.rawmvp/tmp/parallel-active");
    std::getline(activation, activated_session);
    if (sysctlbyname("kern.bootsessionuuid", boot_session.data(), &boot_session_size, nullptr, 0) != 0 ||
        activated_session != boot_session.data()) {
      krbn::logger::get_logger()->info("The isolated core is inactive. Use the parallel switch tool to activate this boot session.");
      return 1;
    }
    if (krbn::services_utility::daemon_running("org.pqrs.service.daemon.Karabiner-Core-Service")) {
      krbn::logger::get_logger()->error("The official Core Service is running. Use the parallel switch tool first.");
      return 1;
    }
''')
    # Remove the archive's empty submodule directories and point at already
    # initialized dependencies only after transformations have finished.
    import shutil
    shutil.copyfile(Path(__file__).with_name('F13TestRecorder.swift'),
                    destination/'src/apps/EventViewer/src/F13TestRecorder.swift')
    path=destination/'src/apps/EventViewer/src/EventHistory.swift'
    replace_once(path, '  Task { @MainActor in\n    let entry = EventHistoryEntry(timestamp: timestamp)',
                 '  if usagePage == 0x7 && usage == 0x68 {\n    Task { @MainActor in\n      F13TestRecorder.shared.record(down: integerValue != 0)\n    }\n  }\n\n  Task { @MainActor in\n    let entry = EventHistoryEntry(timestamp: timestamp)')
    vendor = destination / 'vendor'
    shutil.rmtree(vendor)
    vendor.symlink_to((repo / 'vendor').resolve(), target_is_directory=True)
    info = {'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip(),
            'support_root': SUPPORT, 'product': PRODUCT, 'configuration_directory': CONFIG,
            'shared_driver_unchanged': True,
            'transformer_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'f13_recorder_sha256':hashlib.sha256(Path(__file__).with_name('F13TestRecorder.swift').read_bytes()).hexdigest()}
    (destination / 'parallel-build.json').write_text(json.dumps(info, indent=2) + '\n')
    return info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parents[3])
    args = parser.parse_args()
    print(json.dumps(prepare(args.repo.resolve(), args.destination.resolve()), indent=2))


if __name__ == '__main__':
    main()
