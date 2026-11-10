#!/usr/bin/env python3
"""Switch between the official and isolated applications without replacing either."""
from pathlib import Path
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import time
import tempfile

SUPPORT = Path('/Library/Application Support/org.pqrs.rawmvp/RawRecordMVP-Elements')
OFFICIAL = Path('/Library/Application Support/org.pqrs/Karabiner-Elements')
ACTIVATION = Path('/Library/Application Support/org.pqrs.rawmvp/tmp/parallel-active')
DRIVER = 'org.pqrs.service.daemon.Karabiner-VirtualHIDDevice-Daemon'
CORE = {'original':'org.pqrs.service.daemon.Karabiner-Core-Service',
        'developer':'org.pqrs.service.daemon.RawRecordMVP-Core-Service'}
PREFIX = {'original':'Karabiner', 'developer':'RawRecordMVP'}
REMOVABLE = (Path('/Applications/RawRecordMVP-Elements.app'),
             Path('/Applications/RawRecordMVP-EventViewer.app'), SUPPORT)
CONFIG = Path(os.environ.get('XDG_CONFIG_HOME',str(Path.home()/'.config'))) / 'karabiner-raw-record-mvp'
JOURNAL = CONFIG / 'switch-state.json'


def run(command, required=True, timeout=20):
    result = subprocess.run([str(x) for x in command],capture_output=True,text=True,timeout=timeout)
    if required and result.returncode:
        raise RuntimeError(result.stderr.strip() or 'Command failed: '+str(command[0]))
    return result


def backend_ready(cli):
    # The upstream CLI can exit successfully after an IPC failure. Require a
    # valid device-list response from the daemon rather than only a process PID.
    result=run([cli,'--list-connected-devices'],required=False,timeout=3)
    try:return result.returncode==0 and isinstance(json.loads(result.stdout),list)
    except ValueError:return False


def service_state(domain, label):
    result = run(['/bin/launchctl','print',domain+'/'+label],required=False)
    pid = re.search(r'^\s*pid = (\d+)\s*$',result.stdout,re.M)
    disabled = run(['/bin/launchctl','print-disabled',domain],required=False).stdout
    flag = re.search(r'"'+re.escape(label)+r'"\s*=>\s*(true|false|enabled|disabled)',disabled)
    return {'loaded':result.returncode==0,'pid':int(pid.group(1)) if pid else None,
            'disabled':bool(flag and flag.group(1) in ['true','disabled'])}


def service_plist(mode, daemon, label):
    base = OFFICIAL if mode=='original' else SUPPORT
    prefix = PREFIX[mode]
    kind = 'Privileged Daemons' if daemon else 'Non-Privileged Agents'
    folder = 'LaunchDaemons' if daemon else 'LaunchAgents'
    filename = label.replace('-rev2','')+'.plist'
    return base/(prefix+'-Elements '+kind+' v2.app')/'Contents/Library'/folder/filename


def agent_labels(mode):
    prefix=PREFIX[mode]
    return ['org.pqrs.service.agent.'+prefix+'-Console-User-Server',
            'org.pqrs.service.agent.'+prefix+'-Core-Service-rev2',
            'org.pqrs.service.agent.'+prefix+'-MultitouchExtension']


def pause(domain,label):
    # Official startup preferences remain untouched; bootout lasts only until
    # explicit restoration or the next normal login/boot registration.
    if 'RawRecordMVP-' in label:run(['/bin/launchctl','disable',domain+'/'+label])
    if service_state(domain,label)['loaded']:
        run(['/bin/launchctl','bootout',domain+'/'+label])


def resume(mode,domain,label,state):
    # Enabling does not start a job by itself. Restore effective disabled state
    # after recreating any job that was loaded in the original snapshot.
    if mode=='developer' or (state['disabled'] and state['loaded']):
        run(['/bin/launchctl','enable',domain+'/'+label])
    if state['loaded']:
        if not service_state(domain,label)['loaded']:
            run(['/bin/launchctl','bootstrap',domain,service_plist(mode,domain=='system',label)])
        if state.get('pid'):
            run(['/bin/launchctl','kickstart',domain+'/'+label])
    if state['disabled']:
        run(['/bin/launchctl','disable',domain+'/'+label])


def admin(action,mode,loaded=False,disabled=False,had_pid=False):
    if os.geteuid()!=0:
        raise RuntimeError('Administrator authentication is required.')
    installed=SUPPORT/'tools/raw-record-switch.py'
    if Path(__file__).resolve()!=installed:
        raise RuntimeError('Use the root-owned installed switch helper.')
    for path in [installed,installed.parent,SUPPORT,SUPPORT.parent]:
        information=path.stat()
        if information.st_uid!=0 or information.st_mode & 0o022:
            raise RuntimeError('The installed switch helper and its parent directories must be root-owned and protected.')
    # No user-controlled label, path, shell fragment, or driver operation is
    # accepted by this privileged entry point.
    if action=='remove':
        if mode!='developer':raise RuntimeError('Only the isolated installation can be removed.')
        if ACTIVATION.exists():ACTIVATION.unlink()
        if service_state('system',CORE['developer'])['loaded']:
            run(['/bin/launchctl','bootout','system/'+CORE['developer']])
        run(['/bin/launchctl','enable','system/'+CORE['developer']])
        for path in REMOVABLE:
            if path.is_symlink():raise RuntimeError('Refusing to remove an aliased application location.')
            if path.exists():shutil.rmtree(path)
    elif action=='pause':
        if mode=='developer' and ACTIVATION.exists():ACTIVATION.unlink()
        before=service_state('system',CORE[mode])
        try:pause('system',CORE[mode])
        except Exception:
            resume(mode,'system',CORE[mode],before)
            raise
    else:
        if mode=='developer':
            if service_state('system',CORE['original'])['pid']:
                raise RuntimeError('The official core must stop before activation.')
            session=run(['/usr/sbin/sysctl','-n','kern.bootsessionuuid']).stdout.strip()
            if not session:raise RuntimeError('Cannot determine the current boot session.')
            ACTIVATION.parent.mkdir(parents=True,exist_ok=True,mode=0o755)
            information=ACTIVATION.parent.stat()
            if information.st_uid!=0 or information.st_mode & 0o022:
                raise RuntimeError('The activation directory is not protected.')
            descriptor=os.open(str(ACTIVATION),os.O_WRONLY|os.O_CREAT|os.O_TRUNC|getattr(os,'O_NOFOLLOW',0),0o600)
            with os.fdopen(descriptor,'w') as stream:stream.write(session+'\n')
        resume(mode,'system',CORE[mode],{'loaded':loaded,'disabled':disabled,'pid':1 if had_pid else None})


def authenticated(action,mode,state=None):
    state=state or {'loaded':False,'disabled':False,'pid':None}
    command=['/usr/bin/python3','-I',SUPPORT/'tools/raw-record-switch.py','--admin',action,
             '--mode',mode,'--loaded',str(int(state['loaded'])),
             '--disabled',str(int(state['disabled'])),'--had-pid',str(int(bool(state.get('pid'))))]
    apple_script='on run arguments\n do shell script (item 1 of arguments) with administrator privileges\nend run'
    run(['osascript','-e',apple_script,shlex.join([str(x) for x in command])],timeout=180)


@contextlib.contextmanager
def operation_lock():
    CONFIG.mkdir(parents=True,exist_ok=True,mode=0o700)
    descriptor=os.open(str(CONFIG/'switch.lock'),os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o600)
    try:
        try:fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('Another switch operation is in progress.')
        yield
    finally:os.close(descriptor)


def save(state):
    CONFIG.mkdir(parents=True,exist_ok=True,mode=0o700)
    descriptor,name=tempfile.mkstemp(prefix='.switch-',dir=str(CONFIG))
    temporary=Path(name)
    try:
        with os.fdopen(descriptor,'w') as stream:
            json.dump(state,stream,indent=2)
            stream.write('\n');stream.flush();os.fsync(stream.fileno())
        temporary.replace(JOURNAL)
    finally:
        if temporary.exists():temporary.unlink()


def original_snapshot(uid):
    config=Path(os.environ.get('XDG_CONFIG_HOME',str(Path.home()/'.config')))/'karabiner/karabiner.json'
    return {'uid':uid,'core':service_state('system',CORE['original']),
            'agents':{label:service_state('gui/'+str(uid),label) for label in agent_labels('original')},
            'original_configuration':str(config.resolve()),
            'original_configuration_sha256':hashlib.sha256(config.read_bytes()).hexdigest() if config.exists() else None,
            'mode':'prepared'}


def wait_for(predicate,seconds=20):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        try:ready=predicate()
        except subprocess.TimeoutExpired:ready=False
        if ready:return
        time.sleep(0.25)
    raise RuntimeError('The service transition did not complete in time.')


def close_windows(mode,uid):
    prefix=PREFIX[mode]
    for app in [prefix+'-Elements',prefix+'-EventViewer']:
        executable='/Applications/'+app+'.app/Contents/MacOS/'+app
        run(['/usr/bin/pkill','-u',str(uid),'-f','^'+re.escape(executable)+'($| )'],required=False)


def restore_original(state):
    uid=state['uid'];domain='gui/'+str(uid)
    close_windows('developer',uid)
    for label in agent_labels('developer'):pause(domain,label)
    # If the user canceled the first admin dialog, the original core never
    # stopped. Restore its agents without requiring another password dialog.
    original_running=bool(service_state('system',CORE['original'])['pid'])
    developer_running=bool(service_state('system',CORE['developer'])['pid'])
    if developer_running or not original_running or state.get('original_paused',False):
        authenticated('pause','developer')
        wait_for(lambda:not service_state('system',CORE['developer'])['pid'])
    if not original_running:
        authenticated('resume','original',state['core'])
    for label,previous in state['agents'].items():resume('original',domain,label,previous)
    if state['core'].get('pid'):
        wait_for(lambda:bool(service_state('system',CORE['original'])['pid']))
        wait_for(lambda:backend_ready(OFFICIAL/'bin/karabiner_cli'))
    state['mode']='original'
    save(state)
    print('Official Karabiner restored. Original application files and configuration were not overwritten.')


def check_baseline():
    official_info=Path('/Applications/Karabiner-Elements.app/Contents/Info.plist')
    if plistlib.loads(official_info.read_bytes()).get('CFBundleShortVersionString')!='16.3.0':
        raise RuntimeError('This switch helper supports the verified official 16.3.0 baseline only.')


def enter_developer():
    uid=os.getuid();domain='gui/'+str(uid)
    check_baseline()
    if JOURNAL.exists():
        previous=json.loads(JOURNAL.read_text())
        if previous.get('mode') in ['prepared','developer']:
            raise RuntimeError('A prior switch is pending. Run original first to restore its saved state.')
    if not service_state('system',DRIVER)['pid']:
        raise RuntimeError('The installed official virtual HID daemon must already be running.')
    manager=SUPPORT/'RawRecordMVP-Elements Privileged Daemons v2.app/Contents/MacOS/RawRecordMVP-Elements Privileged Daemons v2'
    # Re-registering an already approved owned service reloads the job after
    # the previous switch booted it out. It does not grant missing OS approval.
    run([manager,'register-core-daemons'])
    if run([manager,'core-daemons-enabled'],required=False).returncode:
        raise RuntimeError('Run setup and approve the isolated background service in System Settings first.')
    if not service_state('system',CORE['developer'])['loaded']:
        raise RuntimeError('The isolated daemon is not registered. Complete setup first.')
    state=original_snapshot(uid)
    save(state)
    try:
        close_windows('original',uid)
        for label in agent_labels('original'):pause(domain,label)
        authenticated('pause','original')
        state['original_paused']=True;save(state)
        wait_for(lambda:not service_state('system',CORE['original'])['pid'])
        # The isolated service was previously registered through SMAppService;
        # this operation only restarts that already approved job.
        authenticated('resume','developer',{'loaded':True,'disabled':False,'pid':1})
        for label in agent_labels('developer'):
            resume('developer',domain,label,{'loaded':label!=agent_labels('developer')[2], 'disabled':False,'pid':1})
        cli=SUPPORT/'bin/karabiner_cli'
        wait_for(lambda:backend_ready(cli))
        if service_state('system',CORE['original'])['pid']:
            raise RuntimeError('Both cores are running; returning to the original application.')
        state['mode']='developer';save(state)
        run(['open','/Applications/RawRecordMVP-Elements.app'])
        print('Isolated backend is communicating. Verify device grabbing before any physical Power test.')
    except Exception:
        try:restore_original(state)
        except Exception as error:print('Automatic restore needs attention: '+str(error),file=sys.stderr)
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',nargs='?',choices=['setup','status','developer','original','uninstall'])
    parser.add_argument('--admin',choices=['pause','resume','remove'])
    parser.add_argument('--mode',choices=['original','developer'])
    for name in ['loaded','disabled','had-pid']:parser.add_argument('--'+name,choices=['0','1'],default='0')
    args=parser.parse_args()
    if args.admin:
        if not args.mode:parser.error('--mode is required')
        return admin(args.admin,args.mode,args.loaded=='1',args.disabled=='1',args.had_pid=='1')
    if os.geteuid()==0:raise RuntimeError('Run the switch command as the logged-in user, not with sudo.')
    if args.action=='setup':
        run(['open','/Applications/RawRecordMVP-Elements.app'])
        print('Grant permissions to the RawRecordMVP application and its own background service. The official core remains running.')
    elif args.action=='status':
        print(json.dumps({mode:service_state('system',label) for mode,label in CORE.items()},indent=2))
    elif args.action=='uninstall':
        with operation_lock():
            if JOURNAL.exists():
                state=json.loads(JOURNAL.read_text())
                if state.get('mode') in ['prepared','developer']:restore_original(state)
            if service_state('system',CORE['developer'])['pid']:
                raise RuntimeError('Restore the official application before uninstalling the isolated one.')
            close_windows('developer',os.getuid())
            agents=SUPPORT/'RawRecordMVP-Elements Non-Privileged Agents v2.app/Contents/MacOS/RawRecordMVP-Elements Non-Privileged Agents v2'
            daemons=SUPPORT/'RawRecordMVP-Elements Privileged Daemons v2.app/Contents/MacOS/RawRecordMVP-Elements Privileged Daemons v2'
            run([agents,'unregister-multitouch-extension-agent'])
            run([agents,'unregister-core-agents'])
            run([daemons,'unregister-core-daemons'])
            authenticated('remove','developer')
            print('Only isolated application files were removed. Original Karabiner and shared driver were preserved.')
    elif args.action=='developer':
        with operation_lock():enter_developer()
    elif args.action=='original':
        if not JOURNAL.exists():raise RuntimeError('No previous switch state is available.')
        with operation_lock():restore_original(json.loads(JOURNAL.read_text()))
    else:parser.error('Choose setup, status, developer or original')


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(str(error),file=sys.stderr);sys.exit(1)
