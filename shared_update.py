from contextlib import contextmanager
import hashlib
import json
import msvcrt
import os
from pathlib import Path
import re
import shutil

import shared_resources as shared


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def base(root):
    root = Path(root).resolve(strict=True)
    directory = root / 'SharedResources'
    if directory.is_junction() or directory.is_symlink() or not directory.resolve().is_relative_to(root):
        raise RuntimeError('Invalid shared resource root.')
    directory.mkdir(exist_ok=True)
    for name in ('current', 'pending-update', 'rollback'):
        path = directory / name
        if path.is_junction() or path.is_symlink():
            raise RuntimeError('Unexpected link at shared update boundary.')
    return directory


def durable_copy(source, destination):
    shutil.copy2(source, destination)
    if digest(source) != digest(destination):
        raise RuntimeError('Backup or staged copy is not identical to its source.')
    with destination.open('r+b') as stream:
        stream.flush()
        os.fsync(stream.fileno())


def durable_write(destination, content):
    with destination.open('wb') as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def assert_no_links(directory):
    if directory.is_junction() or directory.is_symlink():
        raise RuntimeError('Unexpected link in update transaction.')
    pending = [directory]
    while pending:
        with os.scandir(pending.pop()) as entries:
            for item in entries:
                if item.stat(follow_symlinks=False).st_file_attributes & 0x400:
                    raise RuntimeError('Unexpected link in update transaction.')
                if item.is_dir(follow_symlinks=False):
                    pending.append(Path(item.path))


@contextmanager
def locked(directory):
    with (directory / 'update.lock').open('a+b') as stream:
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise RuntimeError('Another resource operation is running.') from error
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def valid_relative(value):
    relative = Path(value)
    if relative.is_absolute() or '..' in relative.parts or not relative.parts:
        raise RuntimeError('Resource path escaped its boundary.')
    if relative.parts[0] not in shared.AREAS and not (len(relative.parts) == 1 and re.fullmatch(r'[0-9a-fA-F]{32}\.script', relative.name)):
        raise RuntimeError('Non-resource file in update index.')
    if relative.name in shared.repair.SKIP or 'shadercache' in relative.parts:
        raise RuntimeError('Private or runtime cache file in update index.')
    return relative


def safe_file(directory, relative):
    if directory.is_junction() or directory.is_symlink():
        raise RuntimeError('Unexpected resource directory connection.')
    path = directory / valid_relative(relative)
    if not path.resolve().is_relative_to(directory.resolve()):
        raise RuntimeError('Resource file resolves outside its directory.')
    return path


def fingerprint(normal):
    normal = Path(normal)
    settings = json.loads((normal.parent / 'user_default.json').read_text(encoding='utf-8'))
    values = {key: settings[key] for key in shared.repair.PATCH_VERSION_KEYS | {'_base_version_'} if key in settings}
    if '_base_version_' not in values or '_key_current_version_' not in values:
        raise RuntimeError('Official game version metadata is incomplete.')
    if any(not re.fullmatch(r'\d+(?:\.\d+)*', str(value)) for value in values.values()):
        raise RuntimeError('Invalid official resource version.')
    return {'versions': values, 'markers': {name: digest(normal / name) for name in shared.repair.MARKERS}}


def environments(root):
    result = []
    directory = base(root)
    for path in directory.glob('migration-env*.json'):
        record = json.loads(path.read_text(encoding='utf-8'))
        if record.get('state') == 'rolled_back':
            continue
        if record.get('state') != 'migrated':
            raise RuntimeError('Incomplete environment migration; recover before starting.')
        env_id = record['environment']
        docs = shared.environment_docs(root, env_id)
        if not all((docs / area).is_junction() and (docs / area).resolve() == (directory / 'current' / area).resolve() for area in shared.AREAS):
            raise RuntimeError('Shared environment connections changed.')
        result.append((env_id, docs))
    if not result:
        raise RuntimeError('No verified shared environment is registered.')
    return result


def resource_index(normal):
    paths = []
    for area in shared.AREAS:
        start = normal / area
        if not start.is_dir():
            raise RuntimeError('Official resource directory missing: ' + area)
        pending = [start]
        while pending:
            with os.scandir(pending.pop()) as entries:
                for entry in entries:
                    stat = entry.stat(follow_symlinks=False)
                    if stat.st_file_attributes & 0x400:
                        raise RuntimeError('Unexpected resource directory connection.')
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name != 'shadercache':
                            pending.append(Path(entry.path))
                    elif entry.name not in shared.repair.SKIP:
                        paths.append(Path(entry.path))
    paths.extend(p for p in normal.iterdir() if p.is_file() and re.fullmatch(r'[0-9a-fA-F]{32}\.script', p.name))
    result = {}
    for count, path in enumerate(paths, 1):
        relative = path.relative_to(normal).as_posix()
        result[relative] = {'sha256': digest(path), 'bytes': path.stat().st_size}
        if count % 3000 == 0:
            print(f'Indexed {count} official resource files...', flush=True)
    return result


def finish_cleanup(directory):
    marker = directory / 'cleanup-authorized.json'
    if not marker.exists():
        return
    record = json.loads(marker.read_text(encoding='utf-8'))
    if record.get('transaction') not in {'pending-update', 'rollback'} or record.get('authorized') is not True:
        raise RuntimeError('Invalid transaction cleanup marker.')
    path = directory / record['transaction']
    if path.exists():
        if path.is_junction() or path.is_symlink() or path.parent.resolve() != directory.resolve():
            raise RuntimeError('Transaction cleanup path escaped its boundary.')
        assert_no_links(path)
        shutil.rmtree(path)
    marker.unlink()


def remove_transaction(directory, path):
    if path.name not in {'pending-update', 'rollback'} or not path.resolve().is_relative_to(directory.resolve()):
        raise RuntimeError('Invalid transaction cleanup path.')
    for current, dirs, files in os.walk(path, followlinks=False):
        for name in dirs + files:
            item = Path(current) / name
            if item.is_symlink() or item.is_junction():
                raise RuntimeError('Unexpected link inside transaction backup.')
    # This durable marker remains outside the deleted tree if cleanup is interrupted.
    shared.save_json(directory / 'cleanup-authorized.json', {'transaction': path.name, 'authorized': True})
    finish_cleanup(directory)


def restore_transaction(root, transaction):
    directory = base(root)
    if transaction.name not in {'pending-update', 'rollback'} or transaction.parent.resolve() != directory.resolve():
        raise RuntimeError('Invalid recovery directory.')
    assert_no_links(transaction)
    record = json.loads((transaction / 'journal.json').read_text(encoding='utf-8'))
    current = directory / 'current'
    for item in reversed(record['applied']):
        target = safe_file(current, item['relative'])
        saved = safe_file(transaction / 'old', item['relative'])
        if saved.exists():
            if digest(saved) != item['old_hash']:
                raise RuntimeError('Resource rollback backup is damaged.')
            os.replace(saved, target)
        elif item['old_hash'] is not None:
            if not target.exists() or digest(target) != item['old_hash']:
                raise RuntimeError('Original resource rollback data is missing.')
        elif target.exists():
            target.unlink()
    for env_id in record['settings_applied']:
        saved = transaction / 'settings' / f'{env_id}.json'
        destination = shared.environment_docs(root, env_id).parent / 'user_default.json'
        expected = record['settings_hashes'][str(env_id)]
        if saved.is_file() and digest(saved) == expected:
            os.replace(saved, destination)
        elif digest(destination) != expected:
            raise RuntimeError('Configuration rollback data is missing or damaged.')
    for item in record.get('hotpatch_applied', []):
        name, env_id = item['name'], item['environment']
        if not re.fullmatch(r'[0-9a-fA-F]{32}\.script', name):
            raise RuntimeError('Invalid hotpatch rollback name.')
        target = shared.environment_docs(root, env_id) / name
        saved = transaction / 'hotpatch' / str(env_id) / name
        if saved.exists():
            if digest(saved) != item['old_hash']:
                raise RuntimeError('Hotpatch rollback backup is damaged.')
            os.replace(saved, target)
        elif item['old_hash'] is not None:
            if not target.exists() or digest(target) != item['old_hash']:
                raise RuntimeError('Original hotpatch rollback data is missing.')
        elif target.exists():
            target.unlink()
    saved_state = transaction / 'old-state.json'
    if saved_state.exists() and digest(saved_state) == record['old_state_hash']:
        os.replace(saved_state, directory / 'state.json')
    elif record['had_state']:
        if digest(directory / 'state.json') != record['old_state_hash']:
            raise RuntimeError('Previous update index is missing.')
    elif not record['had_state'] and (directory / 'state.json').exists():
        (directory / 'state.json').unlink()
    remove_transaction(directory, transaction)


def confirm_update(root):
    shared.guard_idle()
    directory = base(root)
    with locked(directory):
        finish_cleanup(directory)
        rollback = directory / 'rollback'
        if (directory / 'pending-update').exists():
            raise RuntimeError('An interrupted update must be recovered first.')
        if rollback.exists():
            assert_no_links(rollback)
            record = json.loads((rollback / 'journal.json').read_text(encoding='utf-8'))
            state_path = directory / 'state.json'
            if record.get('state') != 'committed' or not state_path.is_file() or digest(state_path) != record.get('committed_state_hash'):
                raise RuntimeError('Current update is not intact; rollback backup was retained.')
            state = json.loads(state_path.read_text(encoding='utf-8'))
            for relative, metadata in state['files'].items():
                if digest(safe_file(directory / 'current', relative)) != metadata['sha256']:
                    raise RuntimeError('Current resources changed; rollback backup was retained.')
            for _, docs in environments(root):
                settings = json.loads((docs.parent / 'user_default.json').read_text(encoding='utf-8'))
                if any(settings.get(key) != value for key, value in state['fingerprint']['versions'].items()):
                    raise RuntimeError('Current environment version changed; rollback backup was retained.')
                for relative, metadata in state['files'].items():
                    if len(Path(relative).parts) == 1 and digest(docs / relative) != metadata['sha256']:
                        raise RuntimeError('Environment hotpatch changed; rollback backup was retained.')
            for item in record.get('hotpatch_applied', []):
                if item['name'] not in state['files'] and (shared.environment_docs(root, item['environment']) / item['name']).exists():
                    raise RuntimeError('Removed environment hotpatch is still present; rollback backup was retained.')
            remove_transaction(directory, rollback)
    return {'state': 'update_confirmed', 'rollback_copies_retained': 0}


def check_ready(root, normal, pending_allowed=False):
    directory = base(root)
    if ((directory / 'pending-update').exists() and not pending_allowed) or not (directory / 'state.json').is_file():
        return False
    record = json.loads((directory / 'state.json').read_text(encoding='utf-8'))
    source = fingerprint(normal)
    if record['fingerprint'] != source:
        return False
    for marker, expected in source['markers'].items():
        if digest(directory / 'current' / marker) != expected:
            return False
    for _, docs in environments(root):
        settings_path = docs.parent / 'user_default.json'
        raw = settings_path.read_bytes()
        if b'\n' in raw or b'\r' in raw:
            return False
        settings = json.loads(raw.decode('utf-8'))
        if any(settings.get(key) != value for key, value in source['versions'].items()):
            return False
        for relative, metadata in record['files'].items():
            if len(Path(relative).parts) == 1:
                local_patch = docs / relative
                if not local_patch.is_file() or digest(local_patch) != metadata['sha256']:
                    return False
    return True


def sync_shared_resources(root, normal, budget=2 * 1024 ** 3):
    shared.guard_idle()
    normal = Path(normal).resolve(strict=True)
    directory = base(root)
    with locked(directory):
        finish_cleanup(directory)
        pending = directory / 'pending-update'
        if pending.exists():
            raise RuntimeError('Interrupted update detected. Run recover-update first.')
        if check_ready(root, normal):
            return {'ready': True, 'changed_files': 0, 'state': 'unchanged'}
        if (directory / 'rollback').exists():
            raise RuntimeError('Confirm the previous successful game update before updating again.')
        source = fingerprint(normal)
        registered = environments(root)
        for _, docs in registered:
            data = json.loads((docs.parent / 'user_default.json').read_text(encoding='utf-8'))
            if data.get('_base_version_') != source['versions']['_base_version_']:
                raise RuntimeError('Base installation version differs; do not force an update.')
        state_path = directory / 'state.json'
        previous = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {'files': {}}
        indexed = previous['files'] if previous.get('fingerprint') == source else resource_index(normal)
        current = directory / 'current'
        changes = []
        for relative, metadata in indexed.items():
            target = safe_file(current, relative)
            if not target.is_file() or digest(target) != metadata['sha256']:
                changes.append(relative)
        removals = [relative for relative in previous['files'] if relative not in indexed]
        hotpatch_actions = {}
        for env_id, docs in registered:
            actions = []
            for relative, metadata in indexed.items():
                if len(Path(relative).parts) == 1:
                    local_patch = docs / relative
                    if not local_patch.is_file() or digest(local_patch) != metadata['sha256']:
                        actions.append(relative)
            actions.extend(relative for relative in removals if len(Path(relative).parts) == 1 and (docs / relative).exists())
            hotpatch_actions[env_id] = actions
        required = sum(indexed[name]['bytes'] + (safe_file(current, name).stat().st_size if safe_file(current, name).exists() else 0) for name in changes)
        required += sum(safe_file(current, name).stat().st_size for name in removals if safe_file(current, name).exists())
        required += sum((docs.parent / 'user_default.json').stat().st_size for _, docs in registered)
        for env_id, docs in registered:
            for relative in hotpatch_actions[env_id]:
                existing = docs / relative
                required += existing.stat().st_size if existing.exists() else 0
                required += indexed[relative]['bytes'] if relative in indexed else 0
        next_state = {'fingerprint': source, 'files': indexed}
        required += len(json.dumps(next_state, ensure_ascii=True, indent=2).encode('utf-8'))
        required += state_path.stat().st_size if state_path.exists() else 0
        if required > budget:
            raise RuntimeError(f'Incremental update needs {required} bytes, exceeding its {budget}-byte limit. No changes made.')
        if fingerprint(normal) != source:
            raise RuntimeError('Official resources changed during indexing; no changes made.')
        pending.mkdir()
        record = {'applied': [], 'settings_applied': [], 'settings_hashes': {}, 'hotpatch_applied': [],
                  'had_state': state_path.exists(), 'old_state_hash': digest(state_path) if state_path.exists() else None,
                  'state': 'staging'}
        shared.save_json(pending / 'journal.json', record)
        try:
            if state_path.exists():
                durable_copy(state_path, pending / 'old-state.json')
            for relative in changes:
                stage = safe_file(pending / 'new', relative)
                stage.parent.mkdir(parents=True, exist_ok=True)
                durable_copy(safe_file(normal, relative), stage)
                if digest(stage) != indexed[relative]['sha256']:
                    raise RuntimeError('Staged resource verification failed.')
            if fingerprint(normal) != source:
                raise RuntimeError('Official resources changed during staging.')
            for relative in changes + removals:
                shared.guard_idle()
                target = safe_file(current, relative)
                saved = safe_file(pending / 'old', relative)
                if target.exists():
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    durable_copy(target, saved)
                old_hash = digest(saved) if saved.exists() else None
                record['applied'].append({'relative': relative, 'old_hash': old_hash})
                shared.save_json(pending / 'journal.json', record)
                if relative in indexed:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(safe_file(pending / 'new', relative), target)
                elif target.exists():
                    target.unlink()
            for env_id, docs in registered:
                settings_path = docs.parent / 'user_default.json'
                data = json.loads(settings_path.read_text(encoding='utf-8'))
                updated = dict(data)
                updated.update(source['versions'])
                if updated != data or b'\n' in settings_path.read_bytes() or b'\r' in settings_path.read_bytes():
                    saved = pending / 'settings' / f'{env_id}.json'
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    durable_copy(settings_path, saved)
                    record['settings_applied'].append(env_id)
                    record['settings_hashes'][str(env_id)] = digest(saved)
                    shared.save_json(pending / 'journal.json', record)
                    staged = settings_path.with_suffix('.shared-tmp')
                    durable_write(staged, json.dumps(updated, ensure_ascii=True, separators=(',', ':')).encode('utf-8'))
                    os.replace(staged, settings_path)
                # Root hotpatch files are small; keep per-environment copies, never account files.
                for relative in hotpatch_actions[env_id]:
                    if len(Path(relative).parts) == 1:
                        local_patch = docs / relative
                        saved = pending / 'hotpatch' / str(env_id) / relative
                        if local_patch.exists():
                            saved.parent.mkdir(parents=True, exist_ok=True)
                            durable_copy(local_patch, saved)
                        record['hotpatch_applied'].append({'environment': env_id, 'name': relative,
                                                          'old_hash': digest(saved) if saved.exists() else None})
                        shared.save_json(pending / 'journal.json', record)
                        if relative in indexed:
                            staged = local_patch.with_suffix('.shared-tmp')
                            durable_copy(current / relative, staged)
                            os.replace(staged, local_patch)
                        elif local_patch.exists():
                            local_patch.unlink()
            if fingerprint(normal) != source:
                raise RuntimeError('Official resource version changed before commit.')
            for relative in changes:
                if digest(safe_file(current, relative)) != indexed[relative]['sha256']:
                    raise RuntimeError('Installed resource verification failed.')
            for relative in removals:
                if safe_file(current, relative).exists():
                    raise RuntimeError('Removed resource is still present.')
            for env_id, docs in registered:
                for relative in hotpatch_actions[env_id]:
                    if len(Path(relative).parts) == 1:
                        if relative in indexed:
                            if digest(docs / relative) != indexed[relative]['sha256']:
                                raise RuntimeError('Installed hotpatch verification failed.')
                        elif (docs / relative).exists():
                            raise RuntimeError('Removed hotpatch is still present.')
            shared.save_json(state_path, next_state)
            if not check_ready(root, normal, pending_allowed=True):
                raise RuntimeError('Final update readiness check failed.')
            record['state'] = 'committed'
            record['committed_state_hash'] = digest(state_path)
            shared.save_json(pending / 'journal.json', record)
            if previous['files'] or record['applied'] and (pending / 'old').exists() or record['settings_applied']:
                os.replace(pending, directory / 'rollback')
            else:
                remove_transaction(directory, pending)
        except Exception:
            if pending.exists():
                restore_transaction(root, pending)
            raise
        return {'ready': True, 'changed_files': len(changes), 'removed_files': len(removals),
                'transaction_bytes': required, 'state': 'synchronized'}


def recover_update(root):
    shared.guard_idle()
    directory = base(root)
    with locked(directory):
        finish_cleanup(directory)
        pending = directory / 'pending-update'
        if pending.exists():
            restore_transaction(root, pending)
    return {'state': 'interrupted_update_recovered'}
