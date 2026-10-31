"""In-place Intake plans and recoverable file operations; no Shopify execution.

Reuse the preparation algorithms, Intake flock, catalogue-operation lock and
scanner. The small file journal lives in the existing operation receipt. It is
not a queue: interrupted file changes require explicit continuation.
"""
from __future__ import annotations

import errno
import hashlib
import hmac
import json
import os
import re
import shutil
import stat
import threading
import unicodedata
import uuid
from pathlib import Path, PurePosixPath

from flask import current_app

from app import db
from app import image_preparation as images
from app.intake_folder_editor import _canonical_spec, _proposal_issues, _future_mappings
from app.intake_grouping import (
    GroupingRejected, acquire_intake_operation, finish_intake_operation,
    acquire_intake_mutation_guard, release_intake_mutation_guard,
    _specialised_promote_noreplace,
)
from app.intake_metadata_builder import metadata_preview
from app.intake_handoff import _catalogue_readiness
from app.models import CatalogueOperation, Product, Settings
from app.utils.operation_control import (
    acquire_catalogue_operation, finish_catalogue_operation, sanitize_operation_error,
)

STAGES = {
    'group': ('Group images', 'intake_group'),
    'folders': ('Folder names and SKU setup', 'intake_folder_edit'),
    'rename': ('Rename images', 'intake_image_rename'),
    'metadata': ('Product JSON', 'intake_metadata_save'),
    'handoff': ('Move and register', 'intake_catalogue_handoff'),
}
JOURNAL = 'intake_file_transaction'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def folded(value):
    return unicodedata.normalize('NFC', str(value)).casefold()


def file_hash(path):
    value = hashlib.sha256()
    # Do not follow a file swapped to a symlink after preview.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('Unsafe file; symbolic/hard links are not modified')
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            value.update(block)
        after = os.fstat(handle.fileno())
        if (info.st_size, info.st_mtime_ns, info.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise ValueError('File changed while inspected; refresh the preview')
    return value.hexdigest()


def folder(root, relative):
    path, selected = images.resolve_intake_folder(root, relative)
    parts = PurePosixPath(selected).parts
    if not parts or any(part.startswith('.') for part in parts) or selected == images.PREPARED_DIRECTORY:
        raise ValueError('Select a product/collection folder inside Intake, not its root or a private work directory')
    return path, selected


def snapshot(path, *, private=None):
    """Bounded real-file inventory, including unrelated files that must survive."""
    directories, files, issues = [], [], []
    root = Path(path)
    stack = [root]
    seen = set()
    while stack:
        current = stack.pop()
        if current.is_symlink():
            raise ValueError('Unsafe symbolic link in Intake path')
        for entry in sorted(current.iterdir(), key=lambda p: (p.name.casefold(), p.name)):
            rel = entry.relative_to(root).as_posix()
            if private and rel == private:
                continue
            info = entry.lstat()
            if len(directories) + len(files) >= images.MAX_PREVIEW_FILES:
                raise ValueError('Intake folder exceeds the 5,000-entry operation bound')
            key = folded(rel)
            if key in seen:
                raise ValueError('Case/Unicode filename collision: ' + rel)
            seen.add(key)
            if stat.S_ISLNK(info.st_mode):
                raise ValueError('Symbolic links are not permitted: ' + rel)
            if stat.S_ISDIR(info.st_mode):
                if entry.name.startswith('.'):
                    raise ValueError('Private or interrupted work requires review: ' + rel)
                directories.append({'path': rel, 'mode': stat.S_IMODE(info.st_mode)})
                stack.append(entry)
            elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                kind, _ = images._classify_entry(entry)
                files.append({'path': rel, 'size': info.st_size, 'mtime_ns': info.st_mtime_ns,
                    'mode': stat.S_IMODE(info.st_mode), 'sha256': file_hash(entry), 'kind': kind})
                if kind not in {'image'} and entry.name not in {'product_info.json', 'sku_index.json'}:
                    issues.append({'state': 'warning', 'message': 'Preserved without modification: ' + rel})
            else:
                raise ValueError('Unsafe special/hard-linked file: ' + rel)
    directories.sort(key=lambda r: r['path'])
    files.sort(key=lambda r: (r['path'].casefold(), r['path']))
    return {'root_mode': stat.S_IMODE(root.stat().st_mode),
            'directories': directories, 'files': files, 'issues': issues}


def _blocked(plan, message):
    plan['issues'].append({'state': 'blocking', 'message': message})


def _folder_snapshot(path, tree):
    rows = []
    for item in tree['directories']:
        name = item['path']
        children = sum(PurePosixPath(r['path']).parent.as_posix() == name for r in tree['directories'])
        count = sum(PurePosixPath(r['path']).parent.as_posix() == name and r['kind'] == 'image' for r in tree['files'])
        empty = not any(PurePosixPath(r['path']).parent.as_posix() == name for r in tree['files']) and not children
        rows.append({**item, 'name': PurePosixPath(name).name, 'child_count': children,
                     'image_count': count, 'empty': empty})
    return {'root_name': path.name, 'folders': rows, 'images': [
        {**r, 'name': PurePosixPath(r['path']).name,
         'folder': '' if PurePosixPath(r['path']).parent.as_posix() == '.' else PurePosixPath(r['path']).parent.as_posix()}
        for r in tree['files'] if r['kind'] == 'image']}


def _map_parent(value, renames):
    parent = PurePosixPath(value).parent.as_posix()
    return str(PurePosixPath(renames.get(parent, parent)) / PurePosixPath(value).name)


def plan(stage, relative, options=None):
    if stage not in STAGES:
        raise ValueError('Unknown Intake stage')
    options = options or {}
    root = images.configured_intake_root()
    path, selected = folder(root, relative)
    tree = snapshot(path)
    result = {'stage': stage, 'source': selected, 'result': selected, 'options': options,
        'tree': tree, 'issues': list(tree['issues']), 'moves': [], 'writes': [],
        'directories': [r['path'] for r in tree['directories']], 'rows': [], 'products': []}
    targets = {r['path']: r['path'] for r in tree['files']}
    result['intake_binding'] = digest(str(Path(root).resolve(strict=True)))
    if stage == 'group':
        base = images.grouping_preview(root, selected, in_place=True)
        # Existing algorithm, but destinations are inside the original folder.
        result['issues'] += [i for i in base['issues'] if i.get('code') != 'existing_result']
        for item in base['mappings']:
            source = item['source_name']
            target = str(PurePosixPath(item['proposed_group']) / source)
            targets[source] = target
            if item['proposed_group'] not in result['directories']:
                result['directories'].append(item['proposed_group'])
        if not base['mappings']:
            result['issues'].append({'state': 'informational', 'message': 'No loose supported images need grouping; existing subfolders are unchanged.'})
    elif stage == 'folders':
        source = _folder_snapshot(path, tree)
        canonical, findings = _canonical_spec(source, options)
        result['options'] = {k: canonical[k] for k in ['root_name', 'renames', 'created', 'remove_empty', 'preview_prefix']}
        result['folder_inputs'] = source['folders']
        result['issues'] += findings + _proposal_issues(source, canonical)
        result['directories'] = list(canonical['renames'].values()) + canonical['created']
        result['result'] = str(PurePosixPath(selected).parent / canonical['root_name'])
        for value in targets:
            targets[value] = _map_parent(value, canonical['renames'])
        future, findings = _future_mappings(source, canonical)
        result['future_images'] = future
        result['issues'] += findings
    elif stage == 'rename':
        prefix = str(options.get('prefix') or '').strip()
        base = images.rename_preview(root, selected, prefix)
        result['issues'] += base['issues']
        result['prefix'] = base['normalised_prefix']
        for item in base['mappings']:
            value = str(PurePosixPath(*item['folder_parts'], item['source_filename']))
            if item['recommended_filename']:
                targets[value] = str(PurePosixPath(*item['folder_parts'], item['recommended_filename']))
        # The legacy alphabetical enumeration is retained for new filenames.
        # Recognise an already complete canonical numbered set numerically so
        # galleries of 100+ images don't reshuffle on a repeat execution.
        by_parent = {}
        for item in base['mappings']:
            parent = str(PurePosixPath(*item['folder_parts']))
            by_parent.setdefault(parent, []).append(item)
        for parent, group in by_parent.items():
            recognised = []
            for item in group:
                expected = item.get('recommended_filename') or ''
                stem = re.sub(r'_\d+\.[^.]+$', '', expected)
                match = re.fullmatch(re.escape(stem) + r'_(\d{2,})\.(jpg|jpeg|png|webp)', item['source_filename'])
                if match: recognised.append(int(match[1]))
            if len(recognised) == len(group) and sorted(recognised) == list(range(1, len(group) + 1)):
                for item in group:
                    value = str(PurePosixPath(*item['folder_parts'], item['source_filename']))
                    targets[value] = value
        # Existing SKU maps may contain image/path references. Never silently
        # reinterpret them; a specific mapping must be corrected by the merchant.
    elif stage == 'metadata':
        preview = metadata_preview(root, selected, options.get('document'), independent=True)
        result['metadata'] = preview
        result['issues'] += preview['findings']
        result['writes'] = [{'path': 'product_info.json', 'text': preview['json_text']}]
    else:
        _handoff_plan(result, path)
    if stage != 'handoff':
        seen = {}
        for row in tree['files']:
            target = targets[row['path']]
            key = folded(target)
            if key in seen:
                _blocked(result, 'Destination collision: ' + target)
            seen[key] = target
            if key in {folded(d) for d in result['directories']}:
                _blocked(result, 'A file and folder share a destination: ' + target)
            if target != row['path']:
                result['moves'].append({**row, 'target': target})
        for row in tree['files']:
            if row['path'].endswith('.json') and result['moves']:
                document = (path / row['path']).read_text(encoding='utf-8')
                # Existing canonical fields normally hold no image paths. Flag
                # exact authored references rather than rewriting free text.
                try:
                    values = json.loads(document)
                except ValueError:
                    _blocked(result, 'Existing JSON cannot be parsed: ' + row['path'])
                    continue
                def strings(value):
                    if isinstance(value, str): yield value
                    elif isinstance(value, list):
                        for child in value: yield from strings(child)
                    elif isinstance(value, dict):
                        for child in value.values(): yield from strings(child)
                changed = {m['path'] for m in result['moves']} | {PurePosixPath(m['path']).name for m in result['moves']}
                if any(v in changed for v in strings(values)):
                    _blocked(result, 'Authored file references require review before renaming: ' + row['path'])
        parent = path.parent
        new_name = PurePosixPath(result['result']).name
        if any(folded(p.name) == folded(new_name) and p.name != path.name for p in parent.iterdir()):
            _blocked(result, 'Containing-folder destination already exists: ' + new_name)
        result['rows'] = [{'before': selected + '/' + r['path'],
                           'after': result['result'] + '/' + targets[r['path']], 'kind': r['kind'],
                           'thumbnail': images.intake_image_token(selected + '/' + r['path']) if r['kind'] == 'image' else None}
                          for r in tree['files']]
    readiness = images.intake_readiness()
    if not readiness['writable']:
        _blocked(result, readiness['message'])
    result['ready'] = not any(i.get('state') == 'blocking' for i in result['issues'])
    result['digest'] = digest(result)
    return result


def _handoff_plan(result, path):
    metadata = metadata_preview(images.configured_intake_root(), result['source'], independent=True)
    result['issues'] += metadata['findings']
    result['metadata'] = metadata
    catalogue = _catalogue_readiness()
    if not catalogue['writable']:
        _blocked(result, catalogue['message'])
    result['destination'] = path.name
    settings = Settings.query.first()
    # Bind the configured destination, not a user-supplied absolute path.
    result['catalogue_binding'] = digest(str(catalogue['root']))
    if catalogue['root']:
        root = catalogue['root']
        if images._within(root, path) or images._within(path, root):
            _blocked(result, 'Intake and catalogue source/destination must be separate trees')
        if any(folded(p.name) == folded(path.name) for p in root.iterdir()):
            _blocked(result, 'Catalogue destination already exists; Intake never merges or replaces it')
    if not any(r['kind'] == 'image' for r in result['tree']['files']):
        _blocked(result, 'No supported product images were found')
    for row in result['tree']['files']:
        if row['kind'] != 'image' and PurePosixPath(row['path']).name not in {'product_info.json', 'sku_index.json'}:
            _blocked(result, 'Remove or review unsupported catalogue file: ' + row['path'])
        if PurePosixPath(row['path']).name == 'product_info.json':
            from app.product_info import validate_product_info
            document = json.loads((path / row['path']).read_text(encoding='utf-8'))
            validation = validate_product_info(document, 'collection' if row['path'] == 'product_info.json' else 'override')
            for issue in validation.errors:
                _blocked(result, row['path'] + ': ' + issue.message)
    if not settings or not settings.output_folder or not settings.url_prefix:
        _blocked(result, 'Configure the existing scanner output folder and image URL before registration')
    prefix = str(metadata['document'].get('sku_prefix') or '')
    names = ([path.name] if metadata['document'].get('collection_type') == 'Single Variable'
             else sorted(metadata['analysis'].get('product_folders') or [path.name]))
    from app.utils.sku_manager import load_sku_index
    index_data = load_sku_index(path, log=lambda *a, **k: None)
    counter = index_data.get('counter', 0)
    if not isinstance(counter, int) or isinstance(counter, bool) or counter < 0:
        _blocked(result, 'Existing SKU counter is invalid; review sku_index.json before registration')
        counter = 0
    for index, name in enumerate(names, counter + 1):
        source = str(PurePosixPath(path.name) / name) if name != path.name else path.name
        # Existing scanner owns allocation. These are estimates, not reservations.
        proposed = prefix + f'{index:04d}'
        result['products'].append({'folder': name, 'sku': proposed, 'allocation': 'Proposed — scanner confirms identity'})
        if Product.query.filter((Product.source_relpath == source) | (Product.sku == proposed)).first():
            _blocked(result, 'An existing catalogue identity/SKU conflicts with ' + source + ' / ' + proposed)
    result['rows'] = [{'before': result['source'] + '/' + r['path'],
                       'after': path.name + '/' + r['path'], 'kind': r['kind']} for r in result['tree']['files']]


def _receipt(operation_id):
    row = db.session.get(CatalogueOperation, operation_id, populate_existing=True)
    if not row: raise ValueError('Intake operation not found')
    return row, json.loads(row.scope or '{}')


def _persist(operation_id, transaction):
    row, scope = _receipt(operation_id)
    scope[JOURNAL] = transaction
    row.scope = json.dumps(scope, ensure_ascii=False)
    db.session.commit()
    from app.utils.operation_live import persist_live_state, persisted_live_state
    previous = persisted_live_state(row) or {}
    phase = transaction['phase']
    logs = previous.get('logs', [])
    if not logs or logs[-1]['line'] != phase.replace('_', ' ').capitalize():
        logs.append({'sequence': (logs[-1]['sequence'] + 1) if logs else 1,
                     'severity': 'info', 'line': phase.replace('_', ' ').capitalize()})
    persist_live_state(operation_id, {'stage': phase, 'latest_message': logs[-1]['line'],
        'status': 'succeeded' if _transaction_complete(transaction) else row.status,
        'summary': _summary(transaction), 'counts': {'changed_files': len(transaction['plan']['moves'])},
        'discord': transaction.get('notification', {}), 'next_sequence': logs[-1]['sequence'] + 1}, logs)


def _sync(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try: os.fsync(fd)
    finally: os.close(fd)


def _safe_parent(base, relative):
    parts = images._portable_parts(relative)
    if not parts: raise ValueError('Empty file path')
    parent, _ = images.resolve_intake_folder(base, str(PurePosixPath(*parts[:-1])))
    return parent / parts[-1]


def _matches(path, expected):
    return path.exists() and not path.is_symlink() and path.stat().st_size == expected['size'] and file_hash(path) == expected['sha256']


def _move(source, target):
    # All renames are non-overwriting, including races with external writers.
    _specialised_promote_noreplace(source, target)
    _sync(source.parent)
    if source.parent != target.parent: _sync(target.parent)


def _mkdirs(base, directories):
    for value in sorted(directories, key=lambda r: (len(PurePosixPath(r).parts), r)):
        target = _safe_parent(base, value)
        if target.exists():
            if target.is_symlink() or not target.is_dir(): raise ValueError('Unsafe destination directory: ' + value)
        else:
            target.mkdir()
            _sync(target.parent)


def _apply_files(operation_id, transaction):
    approved = transaction['plan']
    root = images.configured_intake_root()
    if digest(str(Path(root).resolve(strict=True))) != approved['intake_binding']:
        raise ValueError('Intake mount changed after acceptance')
    # A containing-folder rename is itself journalled and may already have run.
    source = root / approved['source']
    result = root / approved['result']
    private_name = '.tlc-intake-txn-' + operation_id
    temporary_root = root / transaction['root_temporary'] if transaction.get('root_temporary') else None
    if (transaction['phase'] == 'rename_root' and temporary_root and temporary_root.exists()
            and not source.exists() and not result.exists()):
        # Explicit recovery of the one case-only directory-rename boundary.
        images.resolve_intake_folder(root, transaction['root_temporary'])
        _verify_prepared_files(temporary_root, approved, private=private_name)
        _move(temporary_root, _safe_parent(root, approved['result']))
    renamed = transaction.get('root_renamed', False)
    if not source.exists() and result.exists() and transaction.get('phase') == 'rename_root':
        renamed = True
    selected = approved['result'] if renamed else approved['source']
    base, _ = folder(root, selected)
    private = base / private_name
    moves = approved['moves']
    if transaction['phase'] == 'accepted':
        if snapshot(base) != approved['tree']:
            raise ValueError('Source changed after acceptance; no file changes started')
        transaction['phase'] = 'parking'
        _persist(operation_id, transaction)
    if transaction['phase'] == 'parking':
        if private.exists() and (private.is_symlink() or not private.is_dir()):
            raise ValueError('Unsafe transaction directory')
        private.mkdir(mode=0o700, exist_ok=True)
        for index, row in enumerate(moves):
            original = _safe_parent(base, row['path'])
            parked = private / str(index)
            if parked.exists():
                if not _matches(parked, row): raise ValueError('Parked original changed; inspect operation ' + operation_id)
            else:
                if not _matches(original, row): raise ValueError('Original changed: ' + row['path'])
                _move(original, parked)
        transaction['phase'] = 'directories'
        _persist(operation_id, transaction)
    if transaction['phase'] == 'directories':
        # Files not in the plan stay in place; never remove a non-empty folder.
        retained = set(approved['directories'])
        for row in sorted(approved['tree']['directories'], key=lambda r: len(PurePosixPath(r['path']).parts), reverse=True):
            if not (base / row['path']).exists(): continue
            candidate = _safe_parent(base, row['path'])
            if row['path'] not in retained and candidate.exists(): candidate.rmdir()
        _mkdirs(base, approved['directories'])
        renames = approved['options'].get('renames', {}) if approved['stage'] == 'folders' else {}
        for original in approved['tree']['directories']:
            target = renames.get(original['path'], original['path'])
            if target in approved['directories']: os.chmod(_safe_parent(base, target), original['mode'])
        transaction['phase'] = 'placing'
        _persist(operation_id, transaction)
    if transaction['phase'] == 'placing':
        for index, row in enumerate(moves):
            parked = private / str(index)
            target = _safe_parent(base, row['target'])
            if not parked.exists():
                if not _matches(target, row): raise ValueError('Missing original/destination: ' + row['path'])
            else:
                if not _matches(parked, row): raise ValueError('Parked original changed')
                _move(parked, target)
        transaction['phase'] = 'json'
        _persist(operation_id, transaction)
    if transaction['phase'] == 'json':
        for row in approved['writes']:
            target = _safe_parent(base, row['path'])
            before = next((r for r in approved['tree']['files'] if r['path'] == row['path']), None)
            intended = row['text'].encode()
            if target.exists() and file_hash(target) == hashlib.sha256(intended).hexdigest():
                continue
            if (before and not _matches(target, before)) or (not before and target.exists()):
                raise ValueError('JSON changed since acceptance; existing authored data was preserved')
            temporary = private / 'product_info.new'
            if temporary.exists() and file_hash(temporary) != hashlib.sha256(intended).hexdigest():
                raise ValueError('Unexpected metadata transaction file')
            if not temporary.exists():
                with temporary.open('xb') as handle:
                    handle.write(intended); handle.flush(); os.fsync(handle.fileno())
                os.chmod(temporary, before['mode'] if before else 0o644)
            os.replace(temporary, target)
            _sync(target.parent)
        transaction['phase'] = 'rename_root'
        _persist(operation_id, transaction)
    if transaction['phase'] == 'rename_root':
        if approved['source'] != approved['result'] and not renamed:
            # The operation-owned directory travels with the original folder.
            target = _safe_parent(root, approved['result'])
            if source.name.casefold() == target.name.casefold() and source.name != target.name:
                intermediate = source.parent / ('.tlc-intake-root-' + operation_id)
                transaction['root_temporary'] = intermediate.relative_to(root).as_posix()
                _persist(operation_id, transaction)
                _move(source, intermediate); _move(intermediate, target)
            else: _move(source, target)
            base, private = target, target / private_name
        transaction['root_renamed'] = True
        transaction['phase'] = 'verify'
        _persist(operation_id, transaction)
    if transaction['phase'] == 'verify':
        _verify_prepared_files(base, approved, private=private_name)
        if private.exists(): private.rmdir()
        _sync(base)
        transaction['phase'] = 'complete'
        _persist(operation_id, transaction)


def _verify_prepared_files(base, approved, *, private=None):
    inventory = snapshot(base, private=private)
    expected_files = {r.get('target', r['path']) for r in approved['moves']}
    expected_files.update(r['path'] for r in approved['tree']['files']
                          if not any(m['path'] == r['path'] for m in approved['moves']))
    expected_files.update(r['path'] for r in approved['writes'])
    if (set(r['path'] for r in inventory['files']) != expected_files
            or set(r['path'] for r in inventory['directories']) != set(approved['directories'])):
        raise ValueError('Folder contents changed during preparation; inspect the retained originals before continuing')
    for original in approved['tree']['files']:
        move = next((r for r in approved['moves'] if r['path'] == original['path']), None)
        write = next((r for r in approved['writes'] if r['path'] == original['path']), None)
        target = _safe_parent(base, move['target'] if move else original['path'])
        if write:
            if file_hash(target) != hashlib.sha256(write['text'].encode()).hexdigest(): raise ValueError('JSON verification failed')
        elif not _matches(target, original): raise ValueError('Byte verification failed: ' + original['path'])
    for write in approved['writes']:
        if file_hash(_safe_parent(base, write['path'])) != hashlib.sha256(write['text'].encode()).hexdigest():
            raise ValueError('JSON verification failed')


def _summary(transaction):
    approved = transaction['plan']
    return {'source_relpath': approved['source'], 'prepared_relpath': approved['result'],
        'in_place': True, 'source_images': sum(r['kind'] == 'image' for r in approved['tree']['files']),
        'copied_images': 0, 'changed_files': len(approved['moves']) + len(approved['writes']),
        'completed_images': sum(r['kind'] == 'image' for r in approved['tree']['files']) if transaction['phase'] == 'complete' else 0,
        'workflow_status': 'complete' if transaction['phase'] == 'complete' else 'recovery_required',
        'transaction_phase': transaction['phase'], 'registration_operation_id': transaction.get('registration_operation_id'),
        'catalogue_relpath': approved.get('destination'), 'installed': transaction.get('installed', False)}


def _execute(app, lease, transaction):
    with app.app_context():
        failure = None
        try:
            _persist(lease.id, transaction)
            _apply_files(lease.id, transaction)
        except Exception as exc:
            failure = sanitize_operation_error(exc)
            db.session.rollback()
        finally:
            finish_intake_operation(lease, status='failed' if failure else 'succeeded',
                                    error=failure, summary=_summary(transaction))
        _notify(lease.id, transaction, failure)


def _notify(operation_id, transaction, error=None):
    """Existing Discord transport and persistent receipt, one terminal event."""
    key = 'failed' if error else 'completed'
    delivered = transaction.setdefault('notifications', {})
    if key in delivered: return  # interruption after send is never auto-replayed
    delivered[key] = {'state': 'attempting'}
    _persist(operation_id, transaction)
    from app.utils.discord import send_discord_message, build_embed, COLORS
    summary = _summary(transaction)
    fields = [{'name': 'Intake folder', 'value': transaction['plan']['source'], 'inline': False},
              {'name': 'Original files changed', 'value': str(summary['changed_files']), 'inline': True},
              {'name': 'Stage', 'value': transaction['phase'].replace('_', ' '), 'inline': True}]
    message = ('Stopped safely. Inspect the retained file operation; no automatic replay. ' + str(error)) if error else (
        'Original files prepared in place. No Shopify publishing.' if not transaction.get('installed') else
        'Files moved into the catalogue. Registration is a separate scoped scanner operation; no Shopify publishing.')
    try:
        ok, outcome = send_discord_message(embeds=[build_embed(
            'Catalogue Intake — ' + STAGES[transaction['plan']['stage']][0] + ' — ' + key,
            ('Operation: `' + operation_id + '`\n' + message)[:1800],
            COLORS['error'] if error else COLORS['success'], fields)],
            channels=['scans_errors' if error else 'scans_info'])
    except Exception:
        ok, outcome = False, 'delivery failed'
    delivered[key] = {'state': 'sent' if ok else outcome if outcome in {'disabled', 'not configured'} else 'uncertain_or_failed'}
    transaction['notification'] = delivered[key]
    _persist(operation_id, transaction)


def accept(stage, relative, options, submitted_digest):
    preview = plan(stage, relative, options)
    if not hmac.compare_digest(preview['digest'], str(submitted_digest or '')):
        raise ValueError('Files or settings changed after preview. Review the refreshed plan before accepting.')
    if not preview['ready']:
        raise ValueError('Resolve the displayed blocking issues before accepting changes')
    if stage == 'handoff': return start_handoff(preview)
    lease = acquire_intake_operation({'source_relpath': preview['source'], 'proposal_digest': preview['digest']},
                                     operation_type=STAGES[stage][1])
    try:
        # Recheck under the cross-worker filesystem lock.
        if plan(stage, relative, options)['digest'] != preview['digest']:
            raise ValueError('Source changed while acquiring Intake ownership')
        for operation in CatalogueOperation.query.filter(CatalogueOperation.id != lease.id).filter(
                CatalogueOperation.operation_type.in_([v[1] for v in STAGES.values()])).all():
            pending = json.loads(operation.scope or '{}').get(JOURNAL)
            if pending and not _transaction_complete(pending):
                raise ValueError('Finish or inspect interrupted Intake operation ' + operation.id + ' before new changes')
        transaction = {'version': 1, 'phase': 'accepted', 'plan': preview}
        _persist(lease.id, transaction)
    except Exception:
        finish_intake_operation(lease, status='failed', error='Acceptance invalidated before file changes')
        raise
    app = current_app._get_current_object()
    if app.config.get('INTAKE_RUN_INLINE'):
        _execute(app, lease, transaction)
    else:
        try:
            threading.Thread(target=_execute, args=(app, lease, transaction), daemon=True,
                             name='intake-' + lease.id[:8]).start()
        except Exception as exc:
            finish_intake_operation(lease, status='failed', error=sanitize_operation_error(exc), summary=_summary(transaction))
    return lease.id


def _verify_installed(destination, approved, *, scanner_owned=False):
    if destination.is_symlink() or not destination.is_dir(): raise ValueError('Installed collection is unavailable or unsafe')
    for row in approved['tree']['files']:
        target = _safe_parent(destination, row['path'])
        if scanner_owned and PurePosixPath(row['path']).name == 'sku_index.json':
            # A linked existing scan owns its SKU indexes, not authored JSON or
            # images. Never compare its updated counter to the pre-scan bytes.
            file_hash(target)  # retain no-symlink/hardlink protection
            if not isinstance(json.loads(target.read_text()), dict):
                raise ValueError('Scanner SKU index is invalid: ' + row['path'])
            continue
        if not _matches(target, row):
            raise ValueError('Installed original changed: ' + row['path'])


def _apply_handoff(operation_id, transaction):
    approved = transaction['plan']
    catalogue = _catalogue_readiness()
    if not catalogue['writable'] or digest(str(catalogue['root'])) != approved['catalogue_binding']:
        raise ValueError('Configured catalogue destination changed or is unavailable')
    root = images.configured_intake_root()
    if digest(str(Path(root).resolve(strict=True))) != approved['intake_binding']:
        raise ValueError('Intake mount changed after acceptance')
    source = root / approved['source']
    destination = catalogue['root'] / approved['destination']
    if transaction['phase'] == 'accepted':
        if snapshot(folder(root, approved['source'])[0]) != approved['tree']:
            raise ValueError('Source changed after acceptance')
        transaction['phase'] = 'transfer'
        _persist(operation_id, transaction)
    if transaction['phase'] == 'transfer':
        if destination.exists():
            # Only an interrupted commit owned by this exact intent can arrive
            # here. Revalidate every retained original; never merge a new import.
            _verify_installed(destination, approved)
            if source.exists() and not transaction.get('cross_filesystem'):
                raise ValueError('Destination collision; source remains preserved')
        else:
            folder(root, approved['source'])
            try:
                _move(source, destination)
            except OSError as exc:
                if exc.errno != errno.EXDEV: raise
                transaction['cross_filesystem'] = True
                _persist(operation_id, transaction)
                private = catalogue['root'] / '.woocommerce-dashboard-staging'
                if private.exists() and (private.is_symlink() or not private.is_dir()): raise ValueError('Unsafe transfer workspace')
                private.mkdir(mode=0o700, exist_ok=True)
                transfer_id = transaction.get('transfer_owner', operation_id)
                stage = private / ('intake-' + transfer_id)
                if stage.exists() and (stage.is_symlink() or not stage.is_dir()): raise ValueError('Unsafe transfer workspace')
                stage.mkdir(mode=0o700, exist_ok=True)
                _mkdirs(stage, [r['path'] for r in approved['tree']['directories']])
                for index, row in enumerate(approved['tree']['files']):
                    original = _safe_parent(source, row['path'])
                    target = _safe_parent(stage, row['path'])
                    if target.exists():
                        if not _matches(target, row): raise ValueError('Incomplete transfer file requires inspection: ' + row['path'])
                        continue
                    if not _matches(original, row): raise ValueError('Source changed during transfer')
                    temporary = stage / ('.transfer-' + str(index))
                    if temporary.exists():
                        if temporary.is_symlink() or not temporary.is_file() or temporary.stat().st_nlink != 1:
                            raise ValueError('Unsafe operation-owned partial transfer')
                        temporary.unlink()
                    with os.fdopen(os.open(original, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as reader, temporary.open('xb') as writer:
                        shutil.copyfileobj(reader, writer, 1024 * 1024)
                        writer.flush(); os.fsync(writer.fileno())
                    os.chmod(temporary, row['mode'])
                    if not _matches(temporary, row): raise ValueError('Transfer bytes differ from accepted original')
                    _move(temporary, target)
                for row in approved['tree']['directories']: os.chmod(_safe_parent(stage, row['path']), row['mode'])
                _verify_installed(stage, approved)
                if snapshot(source) != approved['tree']: raise ValueError('Source changed during transfer; originals retained')
                os.chmod(stage, approved['tree']['root_mode'])
                _move(stage, destination)
        _verify_installed(destination, approved)
        transaction['installed'] = True
        transaction['phase'] = 'source_cleanup'
        _persist(operation_id, transaction)
    if transaction['phase'] == 'source_cleanup':
        _verify_installed(destination, approved)
        if source.exists():
            # Destination is complete and durably acknowledged before removing
            # only the proven originals. Unexpected additions stop cleanup.
            remaining = snapshot(folder(root, approved['source'])[0])
            expected = {r['path']: r for r in approved['tree']['files']}
            for row in remaining['files']:
                if row != expected.get(row['path']): raise ValueError('Intake source changed; review remaining originals')
            for row in remaining['files']:
                original = _safe_parent(source, row['path'])
                if not _matches(original, row): raise ValueError('Source changed before cleanup')
                original.unlink()
            for row in sorted(remaining['directories'], key=lambda r: len(PurePosixPath(r['path']).parts), reverse=True):
                _safe_parent(source, row['path']).rmdir()
            source.rmdir(); _sync(source.parent)
        transaction['phase'] = 'registration_pending'
        _persist(operation_id, transaction)


def _register(operation_id, transaction):
    from app.utils.scan_runner import start_scan
    approved = transaction['plan']
    catalogue = _catalogue_readiness()
    if not catalogue['writable'] or digest(str(catalogue['root'])) != approved['catalogue_binding']:
        raise ValueError('Configured catalogue destination changed or is unavailable')
    # Keep a durable link before dispatch. Existing scan ownership and completed
    # ingestion make a retry idempotent; no independent importer is introduced.
    existing = transaction.get('registration_operation_id')
    if not existing:
        # Dispatch can commit just before its parent receipt link is persisted.
        previous = CatalogueOperation.query.filter(CatalogueOperation.operation_type == 'append',
            db.func.json_extract(CatalogueOperation.scope, '$.intake_operation_id') == operation_id
            ).order_by(CatalogueOperation.started_at.desc()).first()
        if previous: existing = previous.id
    if existing:
        row = db.session.get(CatalogueOperation, existing, populate_existing=True)
        bound = json.loads(row.scope or '{}') if row else {}
        if not row or row.operation_type != 'append' or bound.get('intake_operation_id') != operation_id or bound.get('collection_relpath') != approved['destination']:
            raise ValueError('Registration receipt does not match the installed collection')
        _verify_installed(catalogue['root'] / approved['destination'], approved, scanner_owned=True)
        if row and row.status in {'running', 'pending', 'succeeded'}:
            transaction['registration_operation_id'] = existing
            _persist(operation_id, transaction)
            return existing
    else:
        _verify_installed(catalogue['root'] / approved['destination'], approved)
    run_id = uuid.uuid4().hex
    scan_id = start_scan(current_app._get_current_object(), run_id, scan_mode='append',
        collection_relpath=approved['destination'], scope={'initiating_source': 'intake_registration',
        'intake_operation_id': operation_id, 'scope_kind': 'collection',
        'collection_relpath': approved['destination']})
    transaction['registration_operation_id'] = scan_id
    _persist(operation_id, transaction)
    return scan_id


def start_handoff(preview):
    lease = acquire_catalogue_operation('intake_catalogue_handoff', {'source_relpath': preview['source'],
        'proposal_digest': preview['digest'], 'destination': preview['destination']})
    transaction = {'version': 1, 'phase': 'accepted', 'plan': preview}
    _persist(lease.id, transaction)
    app = current_app._get_current_object()
    if app.config.get('INTAKE_RUN_INLINE'):
        _execute_handoff(app, lease, transaction)
    else:
        try:
            threading.Thread(target=_execute_handoff, args=(app, lease, transaction), daemon=True,
                             name='intake-move-' + lease.id[:8]).start()
        except Exception as exc:
            finish_catalogue_operation(lease.id, status='failed', error=sanitize_operation_error(exc),
                                       operation_summary=_summary(transaction))
    return lease.id


def _execute_handoff(app, lease, transaction, *, continuation=False):
    with app.app_context():
        _handoff_worker(lease, transaction, continuation=continuation)


def _handoff_worker(lease, transaction, *, continuation=False):
    preview = transaction['plan']
    guard = None
    failure = None
    try:
        guard = acquire_intake_mutation_guard()
        if not continuation and plan('handoff', preview['source'])['digest'] != preview['digest']:
            raise ValueError('Import changed while acquiring catalogue ownership')
        _persist(lease.id, transaction)
        _apply_handoff(lease.id, transaction)
    except Exception as exc:
        failure = sanitize_operation_error(exc)
        db.session.rollback()
    finally:
        if guard: release_intake_mutation_guard(guard)
        finish_catalogue_operation(lease.id, status='partial' if failure else 'succeeded', error=failure,
            recovery_state='review_required' if failure else 'none', operation_summary=_summary(transaction))
    if not failure:
        try: _register(lease.id, transaction)
        except Exception as exc:
            transaction['registration_error'] = sanitize_operation_error(exc)
            _persist(lease.id, transaction)
    _notify(lease.id, transaction, failure)
    return lease.id


def operation_result(operation_id):
    row, scope = _receipt(operation_id)
    transaction = scope.get(JOURNAL)
    if not transaction: raise ValueError('This historical operation has no direct-file continuation; originals remain preserved')
    result = {'id': row.id, 'status': row.status, 'error': row.error, **_summary(transaction),
        'source': transaction['plan']['source'], 'result': transaction['plan']['result'],
        'stage': transaction['plan']['stage'], 'registration': None}
    scan_id = transaction.get('registration_operation_id')
    if scan_id:
        scan = db.session.get(CatalogueOperation, scan_id, populate_existing=True)
        if scan: result['registration'] = {'id': scan.id, 'status': scan.status, 'error': scan.error}
        if scan and scan.status == 'succeeded': result['transaction_phase'] = 'registered'
    result['registration_error'] = transaction.get('registration_error')
    return result


def recover(operation_id):
    row, scope = _receipt(operation_id)
    transaction = scope.get(JOURNAL)
    if not transaction: raise ValueError('No in-place transaction receipt exists; inspect historical work manually')
    if transaction['plan']['stage'] == 'handoff':
        if transaction['phase'] == 'registration_pending':
            return _register(operation_id, transaction)
        lease = acquire_catalogue_operation('intake_catalogue_handoff', {'source_relpath': transaction['plan']['source'],
            'recovery_of': operation_id, 'proposal_digest': transaction['plan']['digest']})
        resumed = json.loads(json.dumps(transaction))
        resumed['transfer_owner'] = transaction.get('transfer_owner', operation_id)
        _persist(lease.id, resumed)
        _execute_handoff(current_app._get_current_object(), lease, resumed, continuation=True)
        return lease.id
    guard = acquire_intake_mutation_guard(recovery_operation_id=operation_id)
    try:
        _apply_files(operation_id, transaction)
        row, scope = _receipt(operation_id)
        scope.setdefault('intake_recovery_history', []).append({'status': row.status, 'error': row.error,
                                                               'finished_at': str(row.finished_at)})
        row.status, row.error = 'succeeded', None
        scope['operation_summary'] = _summary(transaction)
        row.scope = json.dumps(scope, ensure_ascii=False)
        db.session.commit()
    finally:
        release_intake_mutation_guard(guard)
    _notify(operation_id, transaction)
    return operation_id


def _transaction_complete(transaction):
    if transaction['phase'] in {'complete', 'registered'}: return True
    if transaction['phase'] == 'registration_pending' and transaction.get('registration_operation_id'):
        scan = db.session.get(CatalogueOperation, transaction['registration_operation_id'], populate_existing=True)
        return bool(scan and scan.status == 'succeeded')
    return False
