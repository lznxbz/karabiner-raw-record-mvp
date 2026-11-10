#!/usr/bin/env python3
"""Install the isolated app-only package and preserve the official installation."""
from pathlib import Path, PurePosixPath
import argparse
import hashlib
import json
import os
import plistlib
import shlex
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import package as payload_audit

SUPPORT=Path('/Library/Application Support/org.pqrs.rawmvp/RawRecordMVP-Elements')
ALLOWED=[PurePosixPath('Applications/RawRecordMVP-Elements.app'),
         PurePosixPath('Applications/RawRecordMVP-EventViewer.app'),
         PurePosixPath('Library/Application Support/org.pqrs.rawmvp/RawRecordMVP-Elements')]


def allowed(path):
    if path.name.startswith('._'):
        path=path.with_name(path.name[2:])
    return '..' not in path.parts and any(path==root or root in path.parents or path in root.parents for root in ALLOWED)


def inspect_package(package):
    result=subprocess.check_output(['pkgutil','--payload-files',str(package)],text=True)
    for line in result.splitlines():
        path=PurePosixPath(line)
        if path==PurePosixPath('.'):continue
        if path.is_absolute() or not allowed(path):
            raise RuntimeError('The package contains an unexpected destination.')
    with tempfile.TemporaryDirectory(prefix='raw-record-inspect-package-') as directory:
        expanded=Path(directory)/'expanded'
        subprocess.run(['pkgutil','--expand-full',str(package),str(expanded)],check=True)
        if (expanded/'Scripts').exists():
            raise RuntimeError('The parallel package must not contain installer scripts.')
        payload_audit.audit(expanded/'Payload')
        information=ET.parse(expanded/'PackageInfo').getroot()
        if information.find('scripts') is not None:
            raise RuntimeError('Installer hooks are not allowed in the parallel package.')
        if information.get('identifier')!='org.pqrs.RawRecordMVP.Parallel':
            raise RuntimeError('Unexpected package identity.')
        for bundle in information.iter('bundle'):
            if bundle.get('relocate')=='true':raise RuntimeError('Relocatable bundles are not supported.')


def original_fingerprints():
    paths=[Path('/Applications/Karabiner-Elements.app'),Path('/Applications/Karabiner-EventViewer.app'),
           Path('/Applications/.Karabiner-VirtualHIDDevice-Manager.app'),
           Path('/Library/Application Support/org.pqrs/Karabiner-DriverKit-VirtualHIDDevice/Applications/Karabiner-VirtualHIDDevice-Daemon.app')]
    base=Path('/Library/Application Support/org.pqrs/Karabiner-Elements')
    paths+=list(base.glob('*.app'))
    result={}
    for app in paths:
        metadata=app/'Contents/Info.plist'
        if not metadata.exists():continue
        info=plistlib.loads(metadata.read_bytes())
        binary=app/'Contents/MacOS'/info['CFBundleExecutable']
        for path in [metadata,binary]:result[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
    config=Path(os.environ.get('XDG_CONFIG_HOME',str(Path.home()/'.config')))/'karabiner/karabiner.json'
    if config.exists():result[str(config.resolve())]=hashlib.sha256(config.read_bytes()).hexdigest()
    for path in base.glob('*.app/Contents/Library/Launch*/*.plist'):
        result[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def reject_aliases():
    for relative in ALLOWED:
        destination=Path('/')/str(relative)
        for path in [destination,*destination.parents]:
            if path.is_symlink():raise RuntimeError('An isolated destination is a symbolic link; refusing installation.')
    config=Path(os.environ.get('XDG_CONFIG_HOME',str(Path.home()/'.config')))/'karabiner-raw-record-mvp'
    if config.is_symlink() or (config/'karabiner.json').is_symlink():
        raise RuntimeError('The isolated configuration must not alias another configuration.')


def device_identifiers():
    cli='/Library/Application Support/org.pqrs/Karabiner-Elements/bin/karabiner_cli'
    response=subprocess.run([cli,'--list-connected-devices'],capture_output=True,text=True,timeout=20)
    try:
        devices=json.loads(response.stdout)
        if response.returncode or not isinstance(devices,list):raise ValueError('Invalid device response')
    except ValueError as error:
        raise RuntimeError('The official backend is not communicating. Wake and unlock the Mac, then retry after Karabiner resumes.') from error
    result=[]
    for device in devices:
        ids=device.get('identifiers',device.get('device_identifiers',{}))
        if ids.get('vendor_id')==25098 and ids.get('product_id')==1031:
            filtered={key:ids[key] for key in ['vendor_id','product_id']}
            for key in ['is_keyboard','is_pointing_device','is_consumer','is_game_pad']:
                filtered[key]=bool(ids.get(key,False))
            if filtered not in result:result.append(filtered)
    if not result:raise RuntimeError('Connect the Bluetooth M5 before installing this test configuration.')
    return result


def prepare_configuration(devices):
    directory=Path(os.environ.get('XDG_CONFIG_HOME',str(Path.home()/'.config')))/'karabiner-raw-record-mvp'
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    path=directory/'karabiner.json'
    if path.exists():
        print('The existing isolated configuration was preserved.')
        return path
    original=directory.parent/'karabiner/karabiner.json'
    keyboard='ansi'
    if original.exists():
        for profile in json.loads(original.read_text()).get('profiles',[]):
            if profile.get('selected'):
                keyboard=profile.get('virtual_hid_keyboard',{}).get('keyboard_type_v2','ansi')
    raw={'vendor_id':25098,'product_id':1031,'collection_usage_page':1,'collection_usage':128,
         'report_id':3,'payload_length':1,'key_down':[1],'key_up':[0]}
    manipulator={'type':'basic','from':{'raw_record':raw},'to':[{'key_code':'f13','repeat':False}]}
    data={'global':{'check_for_updates_on_startup':False},'profiles':[{
        'name':'Raw Record MVP','selected':True,'virtual_hid_keyboard':{'keyboard_type_v2':keyboard},
        'devices':[{'identifiers':ids,'ignore':False} for ids in devices],
        'complex_modifications':{'rules':[{'description':'Bluetooth M5 Power to F13','manipulators':[manipulator]}]}}]}
    descriptor=os.open(str(path),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(descriptor,'w') as stream:json.dump(data,stream,indent=4);stream.write('\n')
    return path


def install(package,manifest_file):
    if os.geteuid()==0:raise RuntimeError('Run as the logged-in user; the system will ask for administrator authentication.')
    state=json.loads(manifest_file.read_text())
    if hashlib.sha256(package.read_bytes()).hexdigest()!=state['package_sha256']:
        raise RuntimeError('The reviewed package has changed.')
    if state.get('support_root')!=str(SUPPORT) or state.get('shared_driver_installed') is not False:
        raise RuntimeError('This is not an isolated app-only manifest.')
    original_info=Path('/Applications/Karabiner-Elements.app/Contents/Info.plist')
    if plistlib.loads(original_info.read_bytes()).get('CFBundleShortVersionString')!='16.3.0':
        raise RuntimeError('The verified official 16.3.0 baseline is required.')
    reject_aliases();inspect_package(package)
    devices=device_identifiers()
    before=original_fingerprints()
    config_root=Path(os.environ.get('XDG_CONFIG_HOME',str(Path.home()/'.config')))/'karabiner-raw-record-mvp'
    config_root.mkdir(parents=True,exist_ok=True,mode=0o700)
    descriptor,name=tempfile.mkstemp(prefix='installation-original-',suffix='.json',dir=str(config_root))
    with os.fdopen(descriptor,'w') as stream:json.dump(before,stream,indent=2);stream.write('\n')
    # The authenticated installer may not inherit access to a user's Documents
    # directory. Stage the same reviewed bytes in a private temporary directory;
    # do not remove quarantine attributes or change any system security policy.
    with tempfile.TemporaryDirectory(prefix='raw-record-parallel-install-',dir='/private/tmp') as directory:
        staged=Path(directory)/'RawRecordMVP-Parallel.pkg'
        shutil.copy2(package,staged)
        if hashlib.sha256(staged.read_bytes()).hexdigest()!=state['package_sha256']:
            raise RuntimeError('Temporary installer copy does not match the reviewed package.')
        command=shlex.join(['/usr/sbin/installer','-pkg',str(staged),'-target','/'])
        apple_script='on run arguments\n do shell script (item 1 of arguments) with administrator privileges\nend run'
        subprocess.run(['osascript','-e',apple_script,command],check=True)
    if original_fingerprints()!=before:
        raise RuntimeError('The official installation changed during installation; inspect the saved fingerprints before continuing.')
    for app in state['applications']:
        relative=PurePosixPath(app['path'])
        if not allowed(relative):raise RuntimeError('Unexpected application path in manifest.')
        directory=Path('/')/str(relative)
        metadata=plistlib.loads((directory/'Contents/Info.plist').read_bytes())
        if not metadata['CFBundleIdentifier'].startswith('org.pqrs.RawRecordMVP'):
            raise RuntimeError('Installed application identity mismatch.')
        binary=directory/'Contents/MacOS'/metadata['CFBundleExecutable']
        if hashlib.sha256(binary.read_bytes()).hexdigest()!=app['binary_sha256']:
            raise RuntimeError('Installed binary mismatch.')
        subprocess.run(['codesign','--verify','--deep','--strict','--all-architectures',str(directory)],check=True)
    helper=SUPPORT/'tools/raw-record-switch.py'
    if hashlib.sha256(helper.read_bytes()).hexdigest()!=state['helper_sha256']:
        raise RuntimeError('Installed switch helper mismatch.')
    if hashlib.sha256((SUPPORT/'bin/karabiner_cli').read_bytes()).hexdigest()!=state['cli_sha256']:
        raise RuntimeError('Installed isolated CLI mismatch.')
    for path in [helper,helper.parent,SUPPORT,SUPPORT.parent]:
        information=path.stat()
        if information.st_uid!=0 or information.st_mode & 0o022:
            raise RuntimeError('The installed switch helper and its parent directories must be root-owned and protected.')
    prepare_configuration(devices)
    print('Installed isolated application suite; official executable, service-plist and configuration fingerprints are unchanged.')
    print('The original remains active. Run raw-record-switch.py setup to grant isolated permissions before switching.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package',type=Path);parser.add_argument('manifest',type=Path)
    args=parser.parse_args();install(args.package.resolve(strict=True),args.manifest.resolve(strict=True))


if __name__=='__main__':main()
