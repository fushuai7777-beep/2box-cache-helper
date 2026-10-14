import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import shared_resources as shared


class SharedUpdateTests(unittest.TestCase):
    def fixture(self, directory):
        root = Path(directory) / 'box'
        normal = Path(directory) / 'normal/Documents'
        docs = root / 'Env/2/users/administrator/appdata/local/lagrange_online_branch/Documents'
        for directory in [normal, docs]:
            for area in shared.AREAS:
                (directory / area).mkdir(parents=True)
            for marker in shared.repair.MARKERS:
                (directory / marker).write_bytes(b'fixture-marker-1')
            (directory / 'res/asset.bin').write_bytes(b'old-asset')
        settings = {'_base_version_': '1.1.985817', '_key_current_version_': '5.2.1304779',
                    'patch_normal_dds_ver': '1304779', 'window_type': 'WINDOW_TYPE_NORMAL',
                    'account': 'fixture-private-account'}
        for directory in [normal, docs]:
            (directory.parent / 'user_default.json').write_text(json.dumps(settings), encoding='utf-8')
        with patch.object(shared, 'guard_idle'):
            shared.migrate_environment(root, 2)
        return root, normal, docs

    def change_source(self, normal):
        (normal / 'res/asset.bin').write_bytes(b'new-asset')
        (normal / 'patch/patch_list').write_bytes(b'fixture-marker-2')
        path = normal.parent / 'user_default.json'
        settings = json.loads(path.read_text())
        settings['_key_current_version_'] = '5.2.1304780'
        settings['patch_normal_dds_ver'] = '1304780'
        path.write_text(json.dumps(settings), encoding='utf-8')

    def test_seed_then_same_version_does_not_copy_or_scan_assets(self):
        self.assertTrue(callable(getattr(shared, 'sync_shared_resources', None)), 'Incremental sync is not implemented.')
        with tempfile.TemporaryDirectory() as directory:
            root, normal, docs = self.fixture(directory)
            with patch.object(shared, 'guard_idle'):
                shared.sync_shared_resources(root, normal)
                with patch('shutil.copy2', side_effect=AssertionError('Unexpected copy')):
                    result = shared.sync_shared_resources(root, normal)
            self.assertEqual(result['changed_files'], 0)
            self.assertTrue(result['ready'])

    def test_only_changed_files_copied_and_settings_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root, normal, docs = self.fixture(directory)
            with patch.object(shared, 'guard_idle'):
                shared.sync_shared_resources(root, normal)
                self.change_source(normal)
                result = shared.sync_shared_resources(root, normal)
            self.assertEqual(result['changed_files'], 2)
            self.assertEqual((docs / 'res/asset.bin').read_bytes(), b'new-asset')
            settings = json.loads((docs.parent / 'user_default.json').read_text())
            self.assertEqual(settings['account'], 'fixture-private-account')
            self.assertEqual(settings['window_type'], 'WINDOW_TYPE_NORMAL')
            self.assertEqual(settings['patch_normal_dds_ver'], '1304780')
            self.assertNotIn(b'\n', (docs.parent / 'user_default.json').read_bytes())

    def test_failed_copy_keeps_old_version_and_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            root, normal, docs = self.fixture(directory)
            with patch.object(shared, 'guard_idle'):
                shared.sync_shared_resources(root, normal)
                original_settings = (docs.parent / 'user_default.json').read_bytes()
                self.change_source(normal)
                with patch('shutil.copy2', side_effect=OSError('fixture copy failure')):
                    with self.assertRaises(OSError):
                        shared.sync_shared_resources(root, normal)
            self.assertEqual((docs / 'res/asset.bin').read_bytes(), b'old-asset')
            self.assertEqual((docs.parent / 'user_default.json').read_bytes(), original_settings)

    def test_budget_rejection_happens_before_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root, normal, docs = self.fixture(directory)
            with patch.object(shared, 'guard_idle'):
                shared.sync_shared_resources(root, normal)
                self.change_source(normal)
                with self.assertRaises(RuntimeError):
                    shared.sync_shared_resources(root, normal, budget=1)
            self.assertEqual((docs / 'res/asset.bin').read_bytes(), b'old-asset')

    def test_old_rollback_requires_confirmation_before_another_update(self):
        with tempfile.TemporaryDirectory() as directory:
            root, normal, docs = self.fixture(directory)
            with patch.object(shared, 'guard_idle'):
                shared.sync_shared_resources(root, normal)
                self.change_source(normal)
                shared.sync_shared_resources(root, normal)
                (normal / 'patch/patch_list').write_bytes(b'fixture-marker-3')
                with self.assertRaises(RuntimeError):
                    shared.sync_shared_resources(root, normal)
                shared.confirm_update(root)
                self.assertTrue(shared.sync_shared_resources(root, normal)['ready'])

    def test_version_field_change_without_marker_change_still_syncs(self):
        with tempfile.TemporaryDirectory() as directory:
            root, normal, docs = self.fixture(directory)
            with patch.object(shared, 'guard_idle'):
                shared.sync_shared_resources(root, normal)
                path = normal.parent / 'user_default.json'
                settings = json.loads(path.read_text())
                settings['patch_normal_dds_ver'] = '1304780'
                path.write_text(json.dumps(settings), encoding='utf-8')
                shared.sync_shared_resources(root, normal)
            self.assertEqual(json.loads((docs.parent / 'user_default.json').read_text())['patch_normal_dds_ver'], '1304780')

    def test_commit_failure_restores_environment_hotpatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root, normal, docs = self.fixture(directory)
            name = 'a' * 32 + '.script'
            with patch.object(shared, 'guard_idle'):
                shared.sync_shared_resources(root, normal)
                self.change_source(normal)
                (normal / name).write_bytes(b'new-hotpatch')
                original = shared.save_json

                def fail_commit(path, value):
                    if path.name == 'state.json':
                        raise OSError('fixture commit failure')
                    return original(path, value)

                with patch.object(shared, 'save_json', side_effect=fail_commit):
                    with self.assertRaises(OSError):
                        shared.sync_shared_resources(root, normal)
            self.assertFalse((docs / name).exists(), 'Failed update left a private hotpatch behind.')
            self.assertEqual((docs / 'res/asset.bin').read_bytes(), b'old-asset')

    def test_source_version_change_during_indexing_is_rejected(self):
        import shared_update as update
        with tempfile.TemporaryDirectory() as directory:
            root, normal, docs = self.fixture(directory)
            original = update.resource_index

            def change_after_index(path):
                result = original(path)
                self.change_source(path)
                return result

            with patch.object(shared, 'guard_idle'), patch.object(update, 'resource_index', side_effect=change_after_index):
                with self.assertRaises(RuntimeError):
                    shared.sync_shared_resources(root, normal)
            self.assertEqual((docs / 'res/asset.bin').read_bytes(), b'old-asset')

    def test_recovery_interrupted_then_repeated_does_not_delete_restored_asset(self):
        import shared_update as update
        with tempfile.TemporaryDirectory() as directory:
            root, normal, docs = self.fixture(directory)
            with patch.object(shared, 'guard_idle'):
                shared.sync_shared_resources(root, normal)
                self.change_source(normal)
                shared.sync_shared_resources(root, normal)
                transaction = root / 'SharedResources/rollback'
                original_replace = update.os.replace
                count = 0

                def interrupt_second_restore(source, target):
                    nonlocal count
                    if 'old' in Path(source).parts:
                        count += 1
                        if count == 2:
                            raise OSError('fixture recovery interruption')
                    return original_replace(source, target)

                with patch.object(update.os, 'replace', side_effect=interrupt_second_restore):
                    with self.assertRaises(OSError):
                        update.restore_transaction(root, transaction)
                update.restore_transaction(root, transaction)
            self.assertEqual((docs / 'res/asset.bin').read_bytes(), b'old-asset')
            self.assertEqual((docs / 'patch/patch_list').read_bytes(), b'fixture-marker-1')

    def test_current_directory_connection_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root, normal, docs = self.fixture(directory)
            current = root / 'SharedResources/current'
            unrelated = root / 'unrelated'
            current.rename(unrelated)
            shared.create_junction(current, unrelated)
            try:
                with patch.object(shared, 'guard_idle'):
                    with self.assertRaises(RuntimeError):
                        shared.sync_shared_resources(root, normal)
                self.assertEqual((unrelated / 'res/asset.bin').read_bytes(), b'old-asset')
            finally:
                current.rmdir()

    def test_partial_index_backup_failure_preserves_original_index(self):
        import shared_update as update
        with tempfile.TemporaryDirectory() as directory:
            root, normal, docs = self.fixture(directory)
            with patch.object(shared, 'guard_idle'):
                shared.sync_shared_resources(root, normal)
                state = root / 'SharedResources/state.json'
                original_bytes = state.read_bytes()
                self.change_source(normal)
                original_copy = update.shutil.copy2

                def partial_copy(source, target):
                    if Path(target).name == 'old-state.json':
                        Path(target).write_bytes(b'{')
                        raise OSError('fixture partial backup')
                    return original_copy(source, target)

                with patch.object(update.shutil, 'copy2', side_effect=partial_copy):
                    with self.assertRaises(OSError):
                        shared.sync_shared_resources(root, normal)
            self.assertEqual(state.read_bytes(), original_bytes)

    def test_corruption_after_install_is_detected_before_commit(self):
        import shared_update as update
        with tempfile.TemporaryDirectory() as directory:
            root, normal, docs = self.fixture(directory)
            with patch.object(shared, 'guard_idle'):
                shared.sync_shared_resources(root, normal)
                self.change_source(normal)
                original_replace = update.os.replace

                def corrupt_install(source, target):
                    result = original_replace(source, target)
                    if 'new' in Path(source).parts and Path(target).name == 'asset.bin':
                        Path(target).write_bytes(b'corrupt')
                    return result

                with patch.object(update.os, 'replace', side_effect=corrupt_install):
                    with self.assertRaises(RuntimeError):
                        shared.sync_shared_resources(root, normal)
            self.assertEqual((docs / 'res/asset.bin').read_bytes(), b'old-asset')

    def test_removed_hotpatch_is_removed_from_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root, normal, docs = self.fixture(directory)
            name = 'b' * 32 + '.script'
            (normal / name).write_bytes(b'fixture-old-patch')
            with patch.object(shared, 'guard_idle'):
                shared.sync_shared_resources(root, normal)
                (normal / name).unlink()
                self.change_source(normal)
                shared.sync_shared_resources(root, normal)
            self.assertFalse((docs / name).exists())

    def test_confirmation_preserves_backup_when_current_index_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root, normal, docs = self.fixture(directory)
            with patch.object(shared, 'guard_idle'):
                shared.sync_shared_resources(root, normal)
                self.change_source(normal)
                shared.sync_shared_resources(root, normal)
                (root / 'SharedResources/state.json').unlink()
                with self.assertRaises(RuntimeError):
                    shared.confirm_update(root)
            self.assertTrue((root / 'SharedResources/rollback').exists())

    def test_multiline_settings_are_normalized_without_account_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root, normal, docs = self.fixture(directory)
            with patch.object(shared, 'guard_idle'):
                shared.sync_shared_resources(root, normal)
                path = docs.parent / 'user_default.json'
                before = json.loads(path.read_text())
                path.write_text(json.dumps(before, indent=2), encoding='utf-8')
                shared.sync_shared_resources(root, normal)
            self.assertNotIn(b'\n', path.read_bytes())
            self.assertEqual(json.loads(path.read_text()), before)

    def test_confirmation_rejects_corrupt_private_hotpatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root, normal, docs = self.fixture(directory)
            name = 'c' * 32 + '.script'
            with patch.object(shared, 'guard_idle'):
                shared.sync_shared_resources(root, normal)
                self.change_source(normal)
                (normal / name).write_bytes(b'fixture-patch')
                shared.sync_shared_resources(root, normal)
                (docs / name).write_bytes(b'corrupt-patch')
                with self.assertRaises(RuntimeError):
                    shared.confirm_update(root)
            self.assertTrue((root / 'SharedResources/rollback').exists())

    def test_cleanup_interruption_after_journal_removal_can_be_recovered(self):
        import shared_update as update
        with tempfile.TemporaryDirectory() as directory:
            root, normal, docs = self.fixture(directory)
            with patch.object(shared, 'guard_idle'):
                shared.sync_shared_resources(root, normal)
                self.change_source(normal)
                shared.sync_shared_resources(root, normal)
                original = update.shutil.rmtree

                def partial_cleanup(path):
                    (Path(path) / 'journal.json').unlink()
                    raise OSError('fixture cleanup interruption')

                with patch.object(update.shutil, 'rmtree', side_effect=partial_cleanup):
                    with self.assertRaises(OSError):
                        shared.confirm_update(root)
                update.recover_update(root)
                self.assertTrue(shared.check_ready(root, normal))
                self.assertFalse((root / 'SharedResources/rollback').exists())

    def test_missing_private_hotpatch_is_repaired_without_source_change(self):
        with tempfile.TemporaryDirectory() as directory:
            root, normal, docs = self.fixture(directory)
            name = 'd' * 32 + '.script'
            (normal / name).write_bytes(b'fixture-patch')
            with patch.object(shared, 'guard_idle'):
                shared.sync_shared_resources(root, normal)
                (docs / name).unlink()
                self.assertFalse(shared.check_ready(root, normal))
                shared.sync_shared_resources(root, normal)
            self.assertEqual((docs / name).read_bytes(), b'fixture-patch')


if __name__ == '__main__':
    unittest.main()
