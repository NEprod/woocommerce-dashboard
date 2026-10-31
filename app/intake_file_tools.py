"""Synchronous file preparation: no operation queue, journal or staging tree."""
import fcntl
import os
import uuid
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from app.intake_grouping import _lock_path


@contextmanager
def changing_files():
    """Short-lived existing OS mutex only; historical DB rows are irrelevant."""
    path = _lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another Intake file change is executing now. Refresh and try again.')
        yield
    finally:
        os.close(fd)


def rename(source, target, *, root=None):
    """Original script operation, guarded against existing destinations.

    All app writers hold the Intake flock. Check again immediately before the
    ordinary rename; Unraid shfs does not support renameat2(RENAME_NOREPLACE).
    """
    if root is not None:
        from app.image_preparation import resolve_intake_folder
        for path in (source, target):
            try: relative = path.parent.relative_to(root).as_posix()
            except ValueError: raise ValueError('Rename escapes the selected folder')
            resolve_intake_folder(root, relative)
    if source.is_symlink() or not source.exists():
        raise ValueError('Source is missing or unsafe: ' + str(source))
    if os.path.lexists(target):
        raise FileExistsError('Destination already exists: ' + str(target))
    # Do not allow a differently cased destination on case-sensitive hosts.
    if any(p.name.casefold() == target.name.casefold() for p in target.parent.iterdir()):
        raise FileExistsError('Case-insensitive destination conflict: ' + str(target))
    os.rename(source, target)


def _rename_files(base, moves, changed, temporary):
    pending = {base / row['path']: base / row['target'] for row in moves}
    while pending:
        progress = False
        for source, target in list(pending.items()):
            if target in pending or (source.parent == target.parent and source.name.casefold() == target.name.casefold()):
                continue
            rename(source, target, root=base)
            changed.append({'before': str(source.relative_to(base)), 'after': str(target.relative_to(base)), 'kind': 'image'})
            pending.pop(source)
            temporary.pop(source, None)
            progress = True
        if progress:
            continue
        # Only cycles/case-only renames require a temporary filename. No copies.
        source, target = next(iter(pending.items()))
        temp = source.with_name('.tlc-rename-' + uuid.uuid4().hex + source.suffix)
        rename(source, temp, root=base)
        temporary[temp] = source
        pending.pop(source)
        pending[temp] = target


def _rename_folders(base, approved, changed, temporary):
    spec = approved['options']
    actual = {row['path']: base / row['path'] for row in approved['tree']['directories']}
    for key in spec['remove_empty']:
        actual[key].rmdir()
        actual.pop(key)
        changed.append({'before': key, 'after': '(empty folder removed)', 'kind': 'folder'})
    desired = dict(spec['renames'])
    for name in spec['created']:
        desired['new:' + name] = name
    inverse = {value: key for key, value in desired.items()}
    pending = set(desired)
    def relocate(source, target):
        for key, current in list(actual.items()):
            if current == source or source in current.parents:
                actual[key] = target / current.relative_to(source)
    while pending:
        progress = False
        for key in sorted(pending):
            final = PurePosixPath(desired[key])
            parent_key = inverse.get(str(final.parent)) if str(final.parent) != '.' else None
            if parent_key and parent_key not in actual:
                continue
            parent = actual[parent_key] if parent_key else base
            target = parent / final.name
            source = actual.get(key)
            if source == target:
                pending.remove(key); progress = True; continue
            if source and (source == parent or source in parent.parents):
                continue
            if os.path.lexists(target) or any(p.name.casefold() == target.name.casefold() for p in parent.iterdir()):
                continue
            if source:
                rename(source, target, root=base)
                changed.append({'before': str(source.relative_to(base)), 'after': str(target.relative_to(base)), 'kind': 'folder'})
                relocate(source, target)
                temporary.pop(source, None)
            else:
                target.mkdir()
                actual[key] = target
                changed.append({'before': '(new folder)', 'after': str(target.relative_to(base)), 'kind': 'folder'})
            pending.remove(key); progress = True
        if progress:
            continue
        # Break a directory rename cycle by renaming one original, not copying it.
        key = next((key for key in sorted(pending) if key in actual and actual[key] not in temporary), None)
        if key is None:
            raise ValueError('Folder destinations are occupied; no existing data was overwritten')
        source = actual[key]
        temp = source.with_name('.tlc-rename-' + uuid.uuid4().hex)
        rename(source, temp, root=base)
        temporary[temp] = source
        relocate(source, temp)


def apply(approved, options, submitted_digest):
    from app import intake_direct as plans
    result = {'stage': approved['stage'], 'source': approved['source'], 'result': approved['source'],
              'changed': [], 'error': None, 'temporary': [], 'files': []}
    temporary = {}
    with changing_files():
        current = plans.plan(approved['stage'], approved['source'], options)
        if current['digest'] != submitted_digest or not current['ready']:
            raise ValueError('Files or settings changed after preview. Review the current files before accepting.')
        root = plans.images.configured_intake_root().resolve(strict=True)
        base, _ = plans.folder(root, approved['source'])
        try:
            if approved['stage'] == 'folders':
                _rename_folders(base, approved, result['changed'], temporary)
            elif approved['stage'] in {'group', 'rename'}:
                if approved['stage'] == 'group':
                    plans._mkdirs(base, approved['directories'])
                _rename_files(base, approved['moves'], result['changed'], temporary)
            for row in approved['writes']:
                from app.utils.atomic_files import atomic_write_text
                target = plans._safe_parent(base, row['path'])
                if target.exists() and target.read_text(encoding='utf-8') == row['text']:
                    continue
                before = next((r for r in approved['tree']['files'] if r['path'] == row['path']), None)
                def recheck():
                    if (before and not plans._matches(target, before)) or (not before and os.path.lexists(target)):
                        raise ValueError('Authored JSON changed after preview; nothing was overwritten')
                atomic_write_text(target, row['text'], before_replace=recheck)
                if before: os.chmod(target, before['mode'])
                result['changed'].append({'before': row['path'], 'after': row['path'], 'kind': 'JSON'})
            if approved['result'] != approved['source']:
                target = plans._safe_parent(root, approved['result'])
                if base.name.casefold() == target.name.casefold():
                    temp = base.with_name('.tlc-rename-' + uuid.uuid4().hex)
                    rename(base, temp, root=root); temporary[temp] = base
                    base = temp
                rename(base, target, root=root)
                temporary.pop(base, None)
                result['changed'].append({'before': approved['source'], 'after': approved['result'], 'kind': 'folder'})
                base = target
            result['result'] = str(base.relative_to(root))
        except (OSError, ValueError) as exc:
            result['error'] = str(exc)
            # Restore a cycle's temporary name only if its original slot is free.
            for temp, original in list(temporary.items()):
                if temp.exists() and not os.path.lexists(original):
                    try: rename(temp, original, root=root); temporary.pop(temp)
                    except (OSError, ValueError): pass
            if not base.exists() and (root / approved['source']).exists():
                base = root / approved['source']
        result['result'] = str(base.relative_to(root))
        result['temporary'] = [str(p) for p in temporary if p.exists()]
        if base.exists():
            try:
                result['files'] = [row['path'] for row in plans.snapshot(base)['files']]
            except (OSError, ValueError) as exc:
                result['error'] = (result['error'] + '; ' if result['error'] else '') + 'Could not list current files: ' + str(exc)
        result['status'] = 'partial' if result['error'] and result['changed'] else 'failed' if result['error'] else 'succeeded'
    return result
