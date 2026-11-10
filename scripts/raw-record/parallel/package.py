#!/usr/bin/env python3
"""Stage and audit an app-only parallel package, never the upstream installer."""
from pathlib import Path
import argparse
import hashlib
import json
import plistlib
import shutil
import subprocess
import tempfile

ROOT = Path('Library/Application Support/org.pqrs.rawmvp/RawRecordMVP-Elements')
SYSTEM_PREFIX = '/Library/Application Support/org.pqrs.rawmvp/RawRecordMVP-Elements/'
APPLICATIONS = {
    'SettingsWindow': 'RawRecordMVP-Elements',
    'EventViewer': 'RawRecordMVP-EventViewer',
    'AppIconSwitcher': 'RawRecordMVP-AppIconSwitcher',
    'MultitouchExtension': 'RawRecordMVP-MultitouchExtension',
    'ServiceManager-Non-Privileged-Agents': 'RawRecordMVP-Elements Non-Privileged Agents v2',
    'ServiceManager-Privileged-Daemons': 'RawRecordMVP-Elements Privileged Daemons v2',
    'Updater': 'RawRecordMVP-Updater',
    'CoreService': 'RawRecordMVP-Core-Service',
    'ConsoleUserServer': 'RawRecordMVP-Console-User-Server',
}


def audit(root, verify_signatures=True, identity=None):
    """Reject any payload capable of replacing official application files."""
    roots = [Path('Applications/RawRecordMVP-Elements.app'),
             Path('Applications/RawRecordMVP-EventViewer.app'), ROOT]
    for path in root.rglob('*'):
        relative = path.relative_to(root)
        if path.name.startswith('._') and path.is_file() and not path.is_symlink():
            counterpart=relative.with_name(relative.name[2:])
            metadata_scoped=any(counterpart==allowed or counterpart in allowed.parents or allowed in counterpart.parents for allowed in roots)
            # pkgutil may materialize Apple's extended-attribute sidecars while
            # expanding a package. They must describe an allowed existing item.
            if metadata_scoped and (root/counterpart).exists() and path.read_bytes()[:4]==b'\x00\x05\x16\x07':
                continue
        if path.is_dir() and not path.is_symlink():
            if any(relative == allowed or relative in allowed.parents or allowed in relative.parents for allowed in roots):
                continue
        elif any(relative == allowed or allowed in relative.parents for allowed in roots):
            if path.is_symlink():
                resolved = path.resolve()
                if root.resolve() not in resolved.parents:
                    raise RuntimeError('A payload symlink escapes the package root.')
            continue
        raise RuntimeError('Unexpected install location: ' + str(relative))
    apps = [root / path for path in roots[:2]]
    apps += [p for p in (root / ROOT).glob('*.app')]
    if len(apps) != 9:
        raise RuntimeError('The complete nine-application suite is required.')
    for app in apps:
        info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
        if not info['CFBundleIdentifier'].startswith('org.pqrs.RawRecordMVP'):
            raise RuntimeError('An application retained the official bundle identifier.')
        if verify_signatures:
            subprocess.run(['codesign','--verify','--deep','--strict','--all-architectures',str(app)],check=True)
        for path in app.glob('Contents/Library/Launch*/*.plist'):
            service = plistlib.loads(path.read_bytes())
            if 'RawRecordMVP-' not in service['Label']:
                raise RuntimeError('Foreign/shared service found in isolated application.')
            arguments = service.get('ProgramArguments',[])
            if not arguments or not arguments[0].startswith(SYSTEM_PREFIX):
                raise RuntimeError('A service executable points outside the isolated installation.')
            if any('Karabiner-VirtualHID' in str(value) for value in arguments):
                raise RuntimeError('The shared driver must not be packaged or managed.')
    cli = root / ROOT / 'bin/karabiner_cli'
    if not cli.is_file():
        raise RuntimeError('The isolated command line tool is missing.')
    if verify_signatures:
        subprocess.run(['codesign','--verify','--strict','--all-architectures',str(cli)],check=True)
    if verify_signatures and identity:
        for component in [*root.rglob('*.app'), cli]:
            with tempfile.TemporaryDirectory(prefix='raw-record-certificate-') as directory:
                prefix = Path(directory) / 'certificate'
                subprocess.run(['codesign','-d','--extract-certificates='+str(prefix),str(component)],
                               check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                actual = hashlib.sha1(Path(str(prefix)+'0').read_bytes()).hexdigest()
                if actual.upper() != identity.upper():
                    raise RuntimeError('The payload has mixed signing identities.')
    return apps


def stage(source, destination, tools):
    if destination.exists():
        raise RuntimeError('Choose a fresh staging directory.')
    destination.mkdir(parents=True)
    for directory,name in APPLICATIONS.items():
        built = source / 'src/apps' / directory / 'build/Release' / (name+'.app')
        if not built.is_dir():
            raise RuntimeError('Missing build output: '+name)
        parent = destination / ('Applications' if directory in ['SettingsWindow','EventViewer'] else ROOT)
        parent.mkdir(parents=True,exist_ok=True)
        shutil.copytree(built,parent/built.name,symlinks=True)
    binary_dir = destination / ROOT / 'bin'
    binary_dir.mkdir()
    shutil.copy2(source/'src/bin/cli/build/Release/karabiner_cli',binary_dir/'karabiner_cli')
    tools_dir = destination / ROOT / 'tools'
    tools_dir.mkdir()
    shutil.copy2(tools/'switch.py',tools_dir/'raw-record-switch.py')
    scripts_dir = destination / ROOT / 'scripts'
    scripts_dir.mkdir()
    uninstall = scripts_dir / 'uninstaller.applescript'
    uninstall.write_text('display dialog "Remove only RawRecordMVP? The official installation and shared driver will be preserved." buttons {"Cancel", "Remove"} default button "Cancel"\n'
                         'do shell script "/usr/bin/python3 \"/Library/Application Support/org.pqrs.rawmvp/RawRecordMVP-Elements/tools/raw-record-switch.py\" uninstall"\n')
    shutil.copy2(source/'src/scripts/repair.sh',destination/ROOT/'repair.sh')
    shutil.copy2(source/'parallel-build.json',destination/ROOT/'parallel-build.json')
    for path in destination.rglob('*'):
        if path.is_symlink():
            continue
        if path.is_dir():path.chmod(0o755)
        elif not path.stat().st_mode & 0o111:path.chmod(0o644)


def package(source, output, tools, signing_identity):
    output.mkdir(parents=True,exist_ok=True)
    root = output/'payload'
    stage(source,root,tools)
    subprocess.run(['bash',str(source/'scripts/codesign.sh'),str(root)],check=True,
                   env={**__import__('os').environ,'PQRS_ORG_CODE_SIGN_IDENTITY':signing_identity})
    apps = audit(root,identity=signing_identity)
    # Repair must restore the signed copy, not the pre-sign build output.
    import tarfile
    with tarfile.open(root/ROOT/'RawRecordMVP-Elements.app.tar.gz','w:gz') as archive:
        archive.add(root/'Applications/RawRecordMVP-Elements.app',arcname='RawRecordMVP-Elements.app')
    # Record executable identities after signing, not hashes of pre-sign binaries.
    signed = []
    for app in apps:
        info = plistlib.loads((app/'Contents/Info.plist').read_bytes())
        binary = app/'Contents/MacOS'/info['CFBundleExecutable']
        signed.append({'path':str(app.relative_to(root)), 'bundle_identifier':info['CFBundleIdentifier'],
                       'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest()})
    components = output/'components.plist'
    components.write_bytes(plistlib.dumps([{
        'RootRelativeBundlePath':str(app.relative_to(root)),
        'BundleIsRelocatable':False,'BundleIsVersionChecked':False,
        'BundleOverwriteAction':'upgrade',
    } for app in apps]))
    archive = output/'RawRecordMVP-Parallel.pkg' 
    subprocess.run(['pkgbuild','--root',str(root),'--identifier','org.pqrs.RawRecordMVP.Parallel',
                    '--version','16.3.0','--install-location','/','--ownership','recommended',
                    '--component-plist',str(components),str(archive)],check=True)
    # No preinstall/postinstall scripts and no driver component are included.
    manifest = {'package_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
                'applications':signed,'support_root':'/'+str(ROOT),
                'configuration_directory':'karabiner-raw-record-mvp',
                'package_signed':False,'shared_driver_installed':False,
                'cli_sha256':hashlib.sha256((root/ROOT/'bin/karabiner_cli').read_bytes()).hexdigest(),
                'helper_sha256':hashlib.sha256((root/ROOT/'tools/raw-record-switch.py').read_bytes()).hexdigest()}
    (output/'parallel-package.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print('Created isolated, app-only local development package:',archive.name)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('output',type=Path)
    parser.add_argument('--identity',required=True)
    args=parser.parse_args()
    package(args.source.resolve(),args.output.resolve(),Path(__file__).resolve().parent,args.identity)


if __name__=='__main__':main()
