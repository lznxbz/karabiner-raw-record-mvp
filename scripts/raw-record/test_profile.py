import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import profile as profile_tool


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.config = self.root / 'karabiner.json'
        self.original = {'global': {'check_for_updates_on_startup': True},
                         'profiles': [{'name': 'Original', 'selected': True, 'simple_modifications': []}]}
        self.config.write_text(json.dumps(self.original))
        self.rule = self.root / 'rule.json'
        self.rule.write_text(json.dumps({'rules': [{'description': 'Example', 'manipulators': []}]}))
        self.journal = profile_tool.create(self.config, self.root / 'backups', self.rule, 'test-hash')

    def test_creation_is_inactive_and_backup_is_private(self):
        data = json.loads(self.config.read_text())
        self.assertTrue(data['profiles'][0]['selected'])
        self.assertFalse(data['profiles'][1]['selected'])
        self.assertEqual(self.journal.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.journal.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(json.loads((self.journal.parent / 'karabiner.original.json').read_text()), self.original)

    def test_restore_preserves_unrelated_edits(self):
        data = json.loads(self.config.read_text())
        data['global']['unrelated_setting'] = 42
        data['profiles'][0]['simple_modifications'] = [{'example': 1}]
        self.config.write_text(json.dumps(data))
        profile_tool.change(self.journal, 'restore')
        data = json.loads(self.config.read_text())
        self.assertEqual(data['global']['unrelated_setting'], 42)
        self.assertEqual(data['profiles'][0]['simple_modifications'], [{'example': 1}])
        profile_tool.change(self.journal, 'remove')
        self.assertEqual(len(json.loads(self.config.read_text())['profiles']), 1)

    def test_official_binary_cannot_activate_raw_profile(self):
        with patch.object(profile_tool, 'CORE_BINARY', self.rule):
            with self.assertRaises(RuntimeError):
                profile_tool.change(self.journal, 'activate')
        self.assertTrue(json.loads(self.config.read_text())['profiles'][0]['selected'])

    def test_duplicate_profile_and_concurrent_edit_are_rejected(self):
        with self.assertRaises(RuntimeError):
            profile_tool.create(self.config, self.root / 'backups', self.rule, 'test-hash')
        with self.assertRaises(RuntimeError):
            profile_tool.write_config(self.config, b'outdated', self.original)

    def test_combined_keyboard_pointer_is_explicitly_enabled(self):
        self.rule.write_text(json.dumps({'rules': [{'manipulators': [
            {'from': {'raw_record': {'vendor_id': 25098, 'product_id': 1031}}}]}]}))
        connected = [{'identifiers': {'vendor_id': 25098, 'product_id': 1031,
                      'is_keyboard': True, 'is_pointing_device': True,
                      'device_address': 'private-address'}}]
        devices = profile_tool.target_devices(self.rule, connected)
        self.assertTrue(devices[0]['is_keyboard'])
        self.assertTrue(devices[0]['is_pointing_device'])
        self.assertNotIn('device_address', devices[0])
        with self.assertRaises(RuntimeError):
            profile_tool.target_devices(self.rule, [])
        other = self.root / 'other.json'
        other.write_text(json.dumps(self.original))
        profile_tool.create(other, self.root / 'backups', self.rule, 'hash', connected)
        profile = json.loads(other.read_text())['profiles'][1]
        self.assertFalse(profile['devices'][0]['ignore'])
        self.assertEqual(profile['devices'][0]['identifiers'], devices[0])

    def test_modified_test_profile_is_preserved(self):
        data = json.loads(self.config.read_text())
        data['profiles'][1]['complex_modifications']['rules'].append({'description': 'User edit'})
        self.config.write_text(json.dumps(data))
        with self.assertRaises(RuntimeError):
            profile_tool.change(self.journal, 'remove')
