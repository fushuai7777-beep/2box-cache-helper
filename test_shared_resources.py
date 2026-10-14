import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import shared_resources as shared


class MigrationTests(unittest.TestCase):
    def fixture(self, directory):
        root = Path(directory)
        docs = root / 'Env/2/users/administrator/appdata/local/lagrange_online_branch/Documents'
        for area in shared.AREAS:
            (docs / area).mkdir(parents=True)
            (docs / area / 'fixture.bin').write_bytes(b'fixture-resource')
        (docs.parent / 'user_default.json').write_bytes(b'{"window_type":"WINDOW_TYPE_NORMAL"}')
        (docs / 'upload_album_file').mkdir()
        (docs / 'upload_album_file/personal.jpg').write_bytes(b'personal-photo')
        return root, docs

    def test_migration_moves_resources_and_keeps_personal_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root, docs = self.fixture(directory)
            before = (docs.parent / 'user_default.json').read_bytes()
            with patch.object(shared, 'guard_idle'):
                shared.migrate_environment(root, 2)
                shared.migrate_environment(root, 2)
            for area in shared.AREAS:
                self.assertTrue((docs / area).is_junction())
                self.assertEqual((docs / area).resolve(), (root / 'SharedResources/current' / area).resolve())
                self.assertEqual((docs / area / 'fixture.bin').read_bytes(), b'fixture-resource')
            self.assertEqual((docs.parent / 'user_default.json').read_bytes(), before)
            self.assertEqual((docs / 'upload_album_file/personal.jpg').read_bytes(), b'personal-photo')
            with patch.object(shared, 'guard_idle'):
                shared.rollback_migration(root, 2)
            self.assertFalse((docs / 'res').is_junction())
            self.assertEqual((docs / 'res/fixture.bin').read_bytes(), b'fixture-resource')

    def test_connection_failure_rolls_back_every_move(self):
        with tempfile.TemporaryDirectory() as directory:
            root, docs = self.fixture(directory)
            original = shared.create_junction
            count = 0

            def fail_second(link, target):
                nonlocal count
                count += 1
                if count == 2:
                    raise OSError('fixture failure')
                original(link, target)

            with patch.object(shared, 'guard_idle'), patch.object(shared, 'create_junction', side_effect=fail_second):
                with self.assertRaises(OSError):
                    shared.migrate_environment(root, 2)
            for area in shared.AREAS:
                self.assertFalse((docs / area).is_junction())
                self.assertEqual((docs / area / 'fixture.bin').read_bytes(), b'fixture-resource')

    def test_running_process_and_invalid_environment_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root, docs = self.fixture(directory)
            with patch.object(shared, 'guard_idle', side_effect=RuntimeError('running')):
                with self.assertRaises(RuntimeError):
                    shared.migrate_environment(root, 2)
            with patch.object(shared, 'guard_idle'):
                with self.assertRaises(ValueError):
                    shared.migrate_environment(root, '../1')
            self.assertTrue((docs / 'res/fixture.bin').exists())

    def test_recovery_after_interruption_before_connection_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            root, docs = self.fixture(directory)
            current = root / 'SharedResources/current'
            current.mkdir(parents=True)
            (docs / 'patch').rename(current / 'patch')
            shared.save_json(root / 'SharedResources/migration-env2.json',
                             {'state': 'moving', 'environment': 2, 'areas': ['patch']})
            with patch.object(shared, 'guard_idle'):
                shared.rollback_migration(root, 2)
            self.assertEqual((docs / 'patch/fixture.bin').read_bytes(), b'fixture-resource')
            self.assertFalse((current / 'patch').exists())

    def test_unknown_connection_is_not_modified(self):
        with tempfile.TemporaryDirectory() as directory:
            root, docs = self.fixture(directory)
            (docs / 'res/fixture.bin').unlink()
            (docs / 'res').rmdir()
            unrelated = root / 'unrelated'
            unrelated.mkdir()
            (unrelated / 'personal.bin').write_bytes(b'personal')
            shared.create_junction(docs / 'res', unrelated)
            try:
                with patch.object(shared, 'guard_idle'):
                    with self.assertRaises(RuntimeError):
                        shared.migrate_environment(root, 2)
                self.assertEqual((unrelated / 'personal.bin').read_bytes(), b'personal')
            finally:
                (docs / 'res').rmdir()

    def test_pruned_peer_backup_cannot_be_restored_as_a_complete_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root, docs = self.fixture(directory)
            peer = root / 'Env/3/users/administrator/appdata/local/lagrange_online_branch/Documents'
            for area in shared.AREAS:
                (peer / area).mkdir(parents=True)
                (peer / area / 'old.bin').write_bytes(b'old-resource')
            with patch.object(shared, 'guard_idle'):
                shared.migrate_environment(root, 2)
                shared.attach_environment(root, 3)
                journal = root / 'SharedResources/migration-env3.json'
                record = json.loads(journal.read_text())
                record['resource_backup_pruned'] = True
                shared.save_json(journal, record)
                with self.assertRaisesRegex(RuntimeError, 'deduplicated'):
                    shared.rollback_migration(root, 3)
            self.assertTrue((peer / 'res').is_junction())
            self.assertEqual((peer / 'res/fixture.bin').read_bytes(), b'fixture-resource')

    def test_peer_attach_and_rollback_keep_owner_resources_intact(self):
        self.assertTrue(callable(getattr(shared, 'attach_environment', None)))
        with tempfile.TemporaryDirectory() as directory:
            root, docs = self.fixture(directory)
            peer = root / 'Env/3/users/administrator/appdata/local/lagrange_online_branch/Documents'
            for area in shared.AREAS:
                (peer / area).mkdir(parents=True)
                (peer / area / 'old.bin').write_bytes(b'old-peer-resource')
            with patch.object(shared, 'guard_idle'):
                shared.migrate_environment(root, 2)
                shared.attach_environment(root, 3)
                self.assertEqual((peer / 'res/fixture.bin').read_bytes(), b'fixture-resource')
                with self.assertRaises(RuntimeError):
                    shared.rollback_migration(root, 2)
                shared.rollback_migration(root, 3)
            self.assertEqual((peer / 'res/old.bin').read_bytes(), b'old-peer-resource')
            self.assertTrue((docs / 'res').is_junction())
            self.assertEqual((docs / 'res/fixture.bin').read_bytes(), b'fixture-resource')


if __name__ == '__main__':
    unittest.main()
