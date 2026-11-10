from pathlib import Path
import json
import plistlib
import tempfile
import unittest
from unittest.mock import patch, Mock
import package
import prepare
import switch


class PayloadIsolationTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root=Path(self.directory.name)
        for directory,name in package.APPLICATIONS.items():
            parent=Path('Applications') if directory in ['SettingsWindow','EventViewer'] else package.ROOT
            app=self.root/parent/(name+'.app')
            (app/'Contents').mkdir(parents=True)
            (app/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':'org.pqrs.'+name}))
        cli=self.root/package.ROOT/'bin/karabiner_cli'
        cli.parent.mkdir();cli.write_text('fixture')

    def test_complete_payload_is_scoped_to_independent_locations(self):
        self.assertEqual(len(package.audit(self.root,verify_signatures=False)),9)

    def test_official_application_and_shared_driver_are_rejected(self):
        for forbidden in ['Applications/Karabiner-Elements.app/Contents/Info.plist',
                          'Library/Application Support/org.pqrs/Karabiner-Elements/bin/karabiner_cli',
                          'Library/Application Support/org.pqrs/Karabiner-DriverKit-VirtualHIDDevice/driver']:
            path=self.root/forbidden
            path.parent.mkdir(parents=True,exist_ok=True);path.write_text('unwanted')
            with self.assertRaises(RuntimeError):package.audit(self.root,verify_signatures=False)
            path.unlink()
            # Remove empty forbidden parents so that the next case is independent.
            for parent in path.parents:
                if parent==self.root:break
                try:parent.rmdir()
                except OSError:break

    def test_escaping_symlink_is_rejected(self):
        (self.root/package.ROOT/'outside').symlink_to('/Applications/Karabiner-Elements.app')
        with self.assertRaises(RuntimeError):package.audit(self.root,verify_signatures=False)

    def test_original_service_label_and_executable_are_rejected(self):
        app=self.root/package.ROOT/'RawRecordMVP-Elements Privileged Daemons v2.app'
        folder=app/'Contents/Library/LaunchDaemons';folder.mkdir(parents=True)
        service=folder/'service.plist'
        for label,executable in [
            ('org.pqrs.service.daemon.Karabiner-Core-Service',package.SYSTEM_PREFIX+'core'),
            ('org.pqrs.service.daemon.RawRecordMVP-Core-Service','/Library/Application Support/org.pqrs/Karabiner-Elements/core'),
            ('org.pqrs.service.daemon.Karabiner-VirtualHIDDevice-Daemon','/driver')]:
            service.write_bytes(plistlib.dumps({'Label':label,'ProgramArguments':[executable]}))
            with self.assertRaises(RuntimeError):package.audit(self.root,verify_signatures=False)

    def test_official_bundle_identifier_is_rejected(self):
        path=self.root/'Applications/RawRecordMVP-Elements.app/Contents/Info.plist'
        path.write_bytes(plistlib.dumps({'CFBundleIdentifier':'org.pqrs.Karabiner-Elements.Settings'}))
        with self.assertRaises(RuntimeError):package.audit(self.root,verify_signatures=False)


class SwitchRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.config=Path(self.directory.name)
        self.patchers=[patch.object(switch,'check_baseline'),patch.object(switch,'CONFIG',self.config),patch.object(switch,'JOURNAL',self.config/'switch-state.json')]
        for patcher in self.patchers:patcher.start();self.addCleanup(patcher.stop)

    def test_lock_prevents_concurrent_switches(self):
        with switch.operation_lock():
            with self.assertRaises(RuntimeError):
                with switch.operation_lock():pass

    def test_switch_journal_is_private_and_atomic(self):
        switch.save({'mode':'prepared'})
        journal=self.config/'switch-state.json'
        self.assertEqual(journal.stat().st_mode & 0o777,0o600)
        self.assertEqual(json.loads(journal.read_text()),{'mode':'prepared'})
        self.assertEqual(list(self.config.glob('.switch-*')),[])

    def test_canceling_admin_dialog_restores_original_agents_without_another_dialog(self):
        state={'uid':501,'mode':'prepared','core':{'pid':123,'loaded':True,'disabled':False},
               'agents':{label:{'pid':234,'loaded':True,'disabled':False} for label in switch.agent_labels('original')}}
        def status(domain,label):return {'pid':123 if label==switch.CORE['original'] else None,'loaded':True,'disabled':False}
        with patch.object(switch,'service_state',side_effect=status),patch.object(switch,'close_windows'),\
             patch.object(switch,'pause'),patch.object(switch,'resume') as resume,patch.object(switch,'authenticated') as admin,\
             patch.object(switch,'backend_ready',return_value=True):
            switch.restore_original(state)
        admin.assert_not_called()
        self.assertEqual(resume.call_count,3)
        self.assertEqual(json.loads((self.config/'switch-state.json').read_text())['mode'],'original')

    def test_launchctl_disabled_state_supports_boolean_and_word_output(self):
        label=switch.CORE['developer']
        for value,expected in [('true',True),('false',False),('disabled',True),('enabled',False)]:
            responses=[Mock(returncode=0,stdout='pid = 123\n'),Mock(returncode=0,stdout='"'+label+'" => '+value)]
            with patch.object(switch,'run',side_effect=responses):
                self.assertEqual(switch.service_state('system',label)['disabled'],expected)

    def test_backend_readiness_requires_valid_daemon_response(self):
        for code,output,expected in [(0,'[]',True),(0,'',False),(0,'{}',False),(1,'[]',False)]:
            with patch.object(switch,'run',return_value=Mock(returncode=code,stdout=output)):
                self.assertEqual(switch.backend_ready('/fixture/cli'),expected)

    def test_pause_only_controls_the_requested_core(self):
        calls=[]
        with patch.object(switch,'run',side_effect=lambda args,**kw:calls.append(args)),\
             patch.object(switch,'service_state',return_value={'loaded':True}):
            switch.pause('system',switch.CORE['original'])
        self.assertEqual(len(calls),1)
        self.assertTrue(all(switch.CORE['original'] in str(call) for call in calls))
        self.assertTrue(all(switch.DRIVER not in str(call) for call in calls))
        self.assertTrue(all('disable' not in call for call in calls))

    def test_pending_state_cannot_be_overwritten(self):
        switch.save({'mode':'developer'})
        with self.assertRaises(RuntimeError):switch.enter_developer()
        self.assertEqual(json.loads((self.config/'switch-state.json').read_text())['mode'],'developer')

    def test_failure_after_snapshot_attempts_restore(self):
        snapshot={'uid':501,'mode':'prepared','core':{},'agents':{}}
        with patch.object(switch,'service_state',return_value={'pid':111,'loaded':True}),\
             patch.object(switch,'run',return_value=Mock(returncode=0)),\
             patch.object(switch,'original_snapshot',return_value=snapshot),\
             patch.object(switch,'close_windows'),patch.object(switch,'pause'),\
             patch.object(switch,'authenticated',side_effect=RuntimeError('canceled')),\
             patch.object(switch,'restore_original') as restore:
            with self.assertRaises(RuntimeError):switch.enter_developer()
        restore.assert_called_once_with(snapshot)
        self.assertEqual(json.loads((self.config/'switch-state.json').read_text())['mode'],'prepared')



class IndependentConfigurationTests(unittest.TestCase):
    def test_configuration_creation_preserves_original_and_only_maps_power(self):
        import install as installer
        import os
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'XDG_CONFIG_HOME':directory}):
            original=Path(directory)/'karabiner/karabiner.json'
            original.parent.mkdir()
            original.write_text(json.dumps({'profiles':[{'name':'Private original','selected':True,
                'virtual_hid_keyboard':{'keyboard_type_v2':'iso'},'simple_modifications':[{'private':'mapping'}]}]}))
            before=original.read_bytes()
            path=installer.prepare_configuration([{'vendor_id':25098,'product_id':1031,'is_keyboard':True,'is_pointing_device':True}])
            self.assertNotEqual(path.parent,original.parent)
            self.assertEqual(original.read_bytes(),before)
            result=json.loads(path.read_text())
            self.assertEqual(result['profiles'][0]['virtual_hid_keyboard']['keyboard_type_v2'],'iso')
            self.assertNotIn('simple_modifications',result['profiles'][0])
            rules=result['profiles'][0]['complex_modifications']['rules']
            self.assertEqual(len(rules),1)
            self.assertEqual(rules[0]['manipulators'][0]['to'],[{'key_code':'f13','repeat':False}])
            path.write_text('{"existing":"user edit"}')
            installer.prepare_configuration([])
            self.assertEqual(json.loads(path.read_text()),{'existing':'user edit'})

    def test_package_paths_reject_original_locations_and_traversal(self):
        import install as installer
        from pathlib import PurePosixPath
        for path in ['Applications/Karabiner-Elements.app','Library/Application Support/org.pqrs/tmp',
                     'Applications/RawRecordMVP-Elements.app/../../Karabiner-Elements.app']:
            self.assertFalse(installer.allowed(PurePosixPath(path)))
        self.assertTrue(installer.allowed(PurePosixPath('Applications/RawRecordMVP-Elements.app/Contents/Info.plist')))

    def test_existing_destination_alias_is_rejected(self):
        import install as installer
        from pathlib import PurePosixPath
        with tempfile.TemporaryDirectory(dir='/private/tmp') as directory:
            path=Path(directory)/'alias.app';path.symlink_to('/Applications/Karabiner-Elements.app')
            with patch.object(installer,'ALLOWED',[PurePosixPath(str(path))]):
                with self.assertRaises(RuntimeError):installer.reject_aliases()


if __name__=='__main__':unittest.main()
