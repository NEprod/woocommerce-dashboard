"""Real temporary filesystem + normal Intake HTTP/scanner integration."""
import errno
import hashlib
import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from app import create_app, db
from app import intake_direct as intake
from app.models import CatalogueOperation, Product, Settings, User, ShopifyPublishJob
from app.intake_grouping import reset_intake_operation_control_for_tests
from app.utils.operation_control import reset_operation_control_for_tests
from config import Config


def image(path, colour='red'):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (12, 9), colour).save(path)


def document(kind='Simple'):
    result = {'collection_type': kind, 'sku_prefix': 'DOG-', 'live': False, 'price': '3.50',
              'attributes': {}, 'variation_attributes': [], 'title': 'Dogs'}
    if kind == 'Variable Collection':
        result.update(attributes={'Size': ['Small', 'Large']}, variation_attributes=['Size'])
    if kind == 'Single Variable':
        result.update(attributes={'Style': ['Hound', 'Beagle']}, variation_attributes=['Style'], image_attributes=['Style'])
    return result


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    instance, source, catalogue, output = (tmp_path / n for n in ('instance', 'intake', 'catalogue', 'output'))
    for path in (instance, source, catalogue, output): path.mkdir()
    monkeypatch.setattr(Config, 'SQLALCHEMY_DATABASE_URI', 'sqlite:///' + str(instance / 'site.db'))
    app = create_app()
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, INTAKE_ROOT=str(source),
        INTAKE_TEST_MOUNTED=True, INTAKE_RUN_INLINE=True,
        INTAKE_MUTATION_LOCK_PATH=str(instance / 'intake.lock'), DISCORD_ENABLED=False)
    with app.app_context():
        db.session.add(User(username='intake', email='intake@example.test', password='unused'))
        db.session.add(Settings(product_folder=str(catalogue), output_folder=str(output), url_prefix='https://invalid.example/images/'))
        db.session.commit()
    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = '1'; session['_fresh'] = True
    reset_intake_operation_control_for_tests(); reset_operation_control_for_tests()
    yield app, client, source, catalogue, output
    with app.app_context(): db.session.remove()
    reset_intake_operation_control_for_tests(); reset_operation_control_for_tests()


def accept(client, stage, preview):
    response = client.post('/image-preparation/' + stage + '/confirm', data={
        'path': preview['source'], 'options': json.dumps(preview['options']), 'digest': preview['digest']})
    assert '/image-preparation/result/' in response.location, response.location
    return response.location.rsplit('/', 1)[1]


def wait_scan(app, operation_id):
    end = time.monotonic() + 20
    while time.monotonic() < end:
        with app.app_context():
            row = db.session.get(CatalogueOperation, operation_id, populate_existing=True)
            if row and row.status not in {'pending', 'running'}:
                assert row.status == 'succeeded', row.error
                return
        time.sleep(.05)
    pytest.fail('Existing scoped registration did not finish')


@pytest.mark.parametrize('kind', ['Simple', 'Variable Collection', 'Single Variable'])
def test_complete_real_file_workflow_and_registration(workspace, kind, monkeypatch):
    app, client, root, catalogue, output = workspace
    source = root / 'Dogs'
    for index, name in enumerate(['hound 1.png', 'hound 2.png', 'beagle 1.png', 'beagle 2.png']):
        image(source / name, ['red', 'blue', 'green', 'yellow'][index])
    originals = sorted(hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir())
    inodes = sorted(p.stat().st_ino for p in source.iterdir())
    unrelated = catalogue / 'Other'; image(unrelated / 'Existing' / 'untouched.png')
    unrelated_json = unrelated / 'product_info.json'; unrelated_json.write_text(json.dumps({'bad': 'must not be scanned'}))
    from app.utils import scan_runner
    calls = []
    original_scan = scan_runner.scan_collection
    def scoped(*args, **kwargs):
        calls.append(str(args[0])); return original_scan(*args, **kwargs)
    monkeypatch.setattr(scan_runner, 'scan_collection', scoped)
    with patch('requests.sessions.Session.request', side_effect=AssertionError('No Shopify/network calls permitted')):
        with app.app_context(): preview = intake.plan('group', 'Dogs')
        operation = accept(client, 'group', preview)
        assert client.get('/image-preparation/result/' + operation).status_code == 200
        assert not list(source.glob('*.png'))
        assert (source / 'hound' / 'hound 1.png').exists()
        with app.app_context():
            preview = intake.plan('folders', 'Dogs', {'renames': {'hound': 'Hound', 'beagle': 'Beagle'}, 'preview_prefix': 'dog'})
        accept(client, 'folders', preview)
        with app.app_context(): preview = intake.plan('rename', 'Dogs', {'prefix': 'DOG'})
        accept(client, 'rename', preview)
        assert (source / 'Hound' / 'dog_hound_01.png').exists()
        with app.app_context():
            repeated = intake.plan('rename', 'Dogs', {'prefix': 'DOG'})
            assert repeated['moves'] == []
        accept(client, 'rename', repeated)
        with app.app_context(): preview = intake.plan('metadata', 'Dogs', {'document': document(kind)})
        accept(client, 'metadata', preview)
        assert json.loads((source / 'product_info.json').read_text()) == document(kind)
        assert sorted(p.stat().st_ino for p in source.rglob('*.png')) == inodes
        assert sorted(hashlib.sha256(p.read_bytes()).hexdigest() for p in source.rglob('*.png')) == originals
        with app.app_context(): preview = intake.plan('handoff', 'Dogs')
        assert preview['ready'], preview['issues']
        assert len(preview['products']) == (1 if kind == 'Single Variable' else 2)
        operation = accept(client, 'handoff', preview)
        assert not source.exists()
        assert (catalogue / 'Dogs' / 'product_info.json').exists()
        with app.app_context():
            result = intake.operation_result(operation)
            scan_id = result['registration_operation_id']
        assert scan_id
        wait_scan(app, scan_id)
        with app.app_context():
            products = Product.query.order_by(Product.sku).all()
            assert len(products) == (1 if kind == 'Single Variable' else 2)
            assert {p.sku for p in products} == ({'DOG-0001'} if kind == 'Single Variable' else {'DOG-0001', 'DOG-0002'})
            assert all(db.session.get(ShopifyPublishJob, p.id).scan_operation_id == scan_id for p in products)
            before_ids = {p.id for p in products}
            assert intake.recover(operation) == scan_id
            assert {p.id for p in Product.query.all()} == before_ids
        # Products intentionally renders its existing fast shell, then this API.
        page = client.get('/api/edit_products').get_data(as_text=True)
        assert 'DOG-0001' in page
    assert calls == [str(catalogue / 'Dogs')]
    assert not (root / 'Prepared').exists()
    assert not (root / '.catalogue-intake-staging').exists()
    assert unrelated_json.read_text() == json.dumps({'bad': 'must not be scanned'})


def test_handoff_preview_reuses_existing_sku_counter_without_writing(workspace):
    app, client, root, catalogue, *_ = workspace
    image(root / 'Dogs' / 'Card' / 'one.png')
    (root / 'Dogs' / 'product_info.json').write_text(json.dumps(document()))
    index = root / 'Dogs' / 'sku_index.json'
    index.write_text('{"counter": 41, "retained": "untouched"}')
    original = index.read_bytes()
    with app.app_context():
        preview = intake.plan('handoff', 'Dogs')
        assert preview['ready'], preview['issues']
        assert preview['products'][0]['sku'] == 'DOG-0042'
    assert index.read_bytes() == original
    operation = accept(client, 'handoff', preview)
    with app.app_context(): scan_id = intake.operation_result(operation)['registration_operation_id']
    wait_scan(app, scan_id)
    with app.app_context():
        assert Product.query.one().sku == 'DOG-0042'
        assert intake.recover(operation) == scan_id
    # Its scanner-owned counter changed legitimately; authored data did not.
    assert json.loads((catalogue / 'Dogs' / 'sku_index.json').read_text())['counter'] == 42


def test_external_addition_during_file_transaction_is_reported_and_preserved(workspace):
    app, client, root, *_ = workspace
    image(root / 'Dogs' / 'hound1.png')
    with app.app_context(): preview = intake.plan('group', 'Dogs')
    persist = intake._persist
    def external(operation, transaction):
        persist(operation, transaction)
        if transaction['phase'] == 'placing':
            (root / 'Dogs' / 'new-author-file.txt').write_text('preserve me')
    with patch.object(intake, '_persist', external): operation = accept(client, 'group', preview)
    with app.app_context():
        result = intake.operation_result(operation)
        assert result['status'] == 'failed' and 'contents changed' in result['error']
    assert (root / 'Dogs' / 'new-author-file.txt').read_text() == 'preserve me'
    assert (root / 'Dogs' / 'hound' / 'hound1.png').exists()


def test_discord_delivery_failure_does_not_undo_original_file_success(workspace):
    app, client, root, *_ = workspace
    image(root / 'Dogs' / 'hound1.png')
    with app.app_context(): preview = intake.plan('group', 'Dogs')
    with patch('app.utils.discord.send_discord_message', side_effect=RuntimeError('Isolated delivery outage')):
        operation = accept(client, 'group', preview)
    with app.app_context():
        assert intake.operation_result(operation)['status'] == 'succeeded'
        receipt = json.loads(db.session.get(CatalogueOperation, operation).scope)
        assert receipt[intake.JOURNAL]['notification']['state'] == 'uncertain_or_failed'
    assert (root / 'Dogs' / 'hound' / 'hound1.png').exists()


def test_accept_requires_authentication_and_csrf_and_binds_settings(workspace):
    app, client, root, *_ = workspace
    image(root / 'Dogs' / 'hound1.png')
    with app.app_context(): preview = intake.plan('group', 'Dogs')
    data = {'path': 'Dogs', 'digest': preview['digest'], 'options': '{}'}
    assert app.test_client().post('/image-preparation/group/confirm', data=data).status_code == 401
    app.config['WTF_CSRF_ENABLED'] = True
    assert client.post('/image-preparation/group/confirm', data=data).status_code == 400
    app.config['WTF_CSRF_ENABLED'] = False
    with app.app_context(): preview = intake.plan('rename', 'Dogs', {'prefix': 'DOG'})
    response = client.post('/image-preparation/rename/confirm', data={
        'path': 'Dogs', 'digest': preview['digest'], 'options': '{"prefix":"OTHER"}'})
    assert '/result/' not in response.location
    assert (root / 'Dogs' / 'hound1.png').exists()


@pytest.mark.parametrize('stage,url', [('group','group'), ('folders','folders/edit'), ('rename','rename'), ('metadata','metadata/edit'), ('handoff','handoff/review')])
def test_independent_stage_entry_and_live_preview(workspace, stage, url):
    app, client, root, *_ = workspace
    image(root / 'Ready' / 'Card' / 'original.png')
    (root / 'Ready' / 'product_info.json').write_text(json.dumps(document()))
    response = client.get('/image-preparation/' + url + '?path=Ready')
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert 'Accept Changes' in text and 'data-intake-direct' in text
    assert 'modal fade' not in text if stage != 'metadata' else 'data-prepared-metadata-editor' in text
    settings = {'prefix': 'dog'} if stage == 'rename' else {}
    response = client.post('/image-preparation/' + stage + '/preview?live=1', data={
        'path': 'Ready', 'options': json.dumps(settings)})
    assert response.status_code == 200
    assert response.json['ready'], response.json['html']
    assert (root / 'Ready' / 'Card' / 'original.png').exists()


def test_stale_preview_symlinks_collisions_and_bytes(workspace):
    app, client, root, *_ = workspace
    image(root / 'Dogs' / 'hound1.png')
    with app.app_context(): preview = intake.plan('group', 'Dogs')
    image(root / 'Dogs' / 'hound1.png', 'blue')
    response = client.post('/image-preparation/group/confirm', data={
        'path': 'Dogs', 'digest': preview['digest'], 'options': '{}'})
    assert '/result/' not in response.location
    assert (root / 'Dogs' / 'hound1.png').exists()
    (root / 'Dogs' / 'hound').mkdir()
    image(root / 'Dogs' / 'hound' / 'hound1.png')
    with app.app_context():
        preview = intake.plan('group', 'Dogs')
        assert not preview['ready']
    (root / 'Dogs' / 'escape').symlink_to(root.parent)
    with app.app_context(), pytest.raises(ValueError, match='link'): intake.plan('group', 'Dogs')
    for path in ['../', '/etc', 'Dogs/escape']:
        with app.app_context(), pytest.raises(ValueError): intake.plan('group', path)


def test_rename_cycle_case_only_and_interruption_resume(workspace, monkeypatch):
    app, client, root, *_ = workspace
    image(root / 'Dogs' / 'Hound' / 'one.png', 'red')
    image(root / 'Dogs' / 'Beagle' / 'two.png', 'blue')
    before = {p.name: p.read_bytes() for p in (root / 'Dogs').rglob('*.png')}
    with app.app_context():
        preview = intake.plan('folders', 'Dogs', {'root_name': 'dogs', 'renames': {'Hound': 'Beagle', 'Beagle': 'Hound'}})
    move = intake._move
    calls = 0
    def interrupt(source, destination):
        nonlocal calls
        calls += 1
        move(source, destination)
        if calls == 1: raise OSError('Injected termination after atomic move')
    with patch.object(intake, '_move', interrupt): operation = accept(client, 'folders', preview)
    with app.app_context():
        assert intake.operation_result(operation)['status'] == 'failed'
        db.session.remove(); db.engine.dispose()
        intake.recover(operation)
        assert intake.operation_result(operation)['transaction_phase'] == 'complete'
    assert 'dogs' in os.listdir(root) and 'Dogs' not in os.listdir(root)
    assert (root / 'dogs' / 'Beagle' / 'one.png').read_bytes() == before['one.png']
    assert (root / 'dogs' / 'Hound' / 'two.png').read_bytes() == before['two.png']
    assert not list((root / 'dogs').glob('.tlc-*'))


def test_existing_json_is_reopened_and_overrides_preserved(workspace):
    app, client, root, *_ = workspace
    source = root / 'Prepared' / 'Already Done'
    image(source / 'Card' / 'old.png')
    shared = document(); shared['customisation'] = {'storefront_enabled': True, 'fields': {'dogs_name': {'label': 'Dogs Name', 'type': 'text', 'required': True}}}
    (source / 'product_info.json').write_text(json.dumps(shared))
    child = source / 'Card' / 'product_info.json'; child.write_text('{"price":"4.50", "tags":["local"]}')
    before = child.read_bytes()
    with app.app_context():
        preview = intake.plan('metadata', 'Prepared/Already Done')
        assert preview['metadata']['document'] == shared
    accept(client, 'metadata', preview)
    assert child.read_bytes() == before
    assert json.loads((source / 'product_info.json').read_text()) == shared


def test_catalogue_collision_never_overwrites_or_merges(workspace):
    app, client, root, catalogue, *_ = workspace
    image(root / 'Dogs' / 'Card' / 'one.png')
    (root / 'Dogs' / 'product_info.json').write_text(json.dumps(document()))
    (catalogue / 'dogs').mkdir()
    with app.app_context():
        preview = intake.plan('handoff', 'Dogs')
        assert not preview['ready']
        with pytest.raises(ValueError, match='blocking'): intake.accept('handoff', 'Dogs', {}, preview['digest'])
    assert (root / 'Dogs').exists()


def test_registration_failure_and_retry_do_not_recopies(workspace, monkeypatch):
    app, client, root, catalogue, *_ = workspace
    image(root / 'Dogs' / 'Card' / 'one.png')
    (root / 'Dogs' / 'product_info.json').write_text(json.dumps(document()))
    with app.app_context(): preview = intake.plan('handoff', 'Dogs')
    with patch('app.utils.scan_runner.start_scan', side_effect=RuntimeError('Busy existing scanner')):
        operation = accept(client, 'handoff', preview)
    assert not (root / 'Dogs').exists()
    inode = (catalogue / 'Dogs' / 'Card' / 'one.png').stat().st_ino
    with app.app_context():
        value = intake.operation_result(operation)
        assert value['installed'] and value['registration_error']
        scan_id = intake.recover(operation)
    wait_scan(app, scan_id)
    assert (catalogue / 'Dogs' / 'Card' / 'one.png').stat().st_ino == inode


def test_cross_filesystem_transfer_and_no_duplicate_source(workspace, monkeypatch):
    app, client, root, catalogue, *_ = workspace
    image(root / 'Dogs' / 'Card' / 'one.png')
    (root / 'Dogs' / 'product_info.json').write_text(json.dumps(document()))
    original = (root / 'Dogs' / 'Card' / 'one.png').read_bytes()
    (root / 'Dogs').chmod(0o750)
    (root / 'Dogs' / 'Card').chmod(0o751)
    (root / 'Dogs' / 'Card' / 'one.png').chmod(0o640)
    move = intake._move
    def cross(source, target):
        if source == root / 'Dogs' and target == catalogue / 'Dogs': raise OSError(errno.EXDEV, 'Different devices')
        return move(source, target)
    monkeypatch.setattr(intake, '_move', cross)
    with app.app_context(): preview = intake.plan('handoff', 'Dogs')
    operation = accept(client, 'handoff', preview)
    with app.app_context(): scan_id = intake.operation_result(operation)['registration_operation_id']
    assert scan_id
    wait_scan(app, scan_id)
    assert (catalogue / 'Dogs' / 'Card' / 'one.png').read_bytes() == original
    assert not (root / 'Dogs').exists()
    assert not list((catalogue / '.woocommerce-dashboard-staging').iterdir())
    assert (catalogue / 'Dogs').stat().st_mode & 0o777 == 0o750
    assert (catalogue / 'Dogs' / 'Card').stat().st_mode & 0o777 == 0o751
    assert (catalogue / 'Dogs' / 'Card' / 'one.png').stat().st_mode & 0o777 == 0o640


def test_large_gallery_repeat_preserves_numbered_order(workspace):
    app, client, root, *_ = workspace
    for index in range(1, 106): image(root / 'Large' / 'Card' / f'dog_card_{index:02d}.png')
    with app.app_context():
        proposal = intake.plan('rename', 'Large', {'prefix': 'dog'})
        assert proposal['ready'] and not proposal['moves']
    accept(client, 'rename', proposal)
    assert len(list((root / 'Large' / 'Card').iterdir())) == 105


def test_cross_filesystem_interruption_can_resume_explicitly(workspace, monkeypatch):
    app, client, root, catalogue, *_ = workspace
    image(root / 'Dogs' / 'Card' / 'one.png')
    image(root / 'Dogs' / 'Card' / 'two.webp', 'blue')
    (root / 'Dogs' / 'product_info.json').write_text(json.dumps(document()))
    move = intake._move
    interrupted = False
    def cross(source, target):
        nonlocal interrupted
        if source == root / 'Dogs' and target == catalogue / 'Dogs': raise OSError(errno.EXDEV, 'Different devices')
        move(source, target)
        if '.woocommerce-dashboard-staging' in str(target) and target.name == 'one.png' and not interrupted:
            interrupted = True
            raise OSError('Injected interruption after first acknowledged file')
    monkeypatch.setattr(intake, '_move', cross)
    with app.app_context(): preview = intake.plan('handoff', 'Dogs')
    operation = accept(client, 'handoff', preview)
    assert (root / 'Dogs').exists() and not (catalogue / 'Dogs').exists()
    with app.app_context():
        old = db.session.get(CatalogueOperation, operation)
        original_scope = old.scope
        resumed = intake.recover(operation)
        assert resumed != operation
        assert db.session.get(CatalogueOperation, operation).scope == original_scope
        result = intake.operation_result(resumed)
        assert result['installed'], result
        scan_id = result['registration_operation_id']
    wait_scan(app, scan_id)
    assert not (root / 'Dogs').exists()
    assert len(list((catalogue / 'Dogs').rglob('*.png'))) == 1
    assert len(list((catalogue / 'Dogs').rglob('*.webp'))) == 1


@pytest.mark.parametrize('boundary', ['directories', 'placing', 'json', 'rename_root', 'verify'])
def test_restart_at_every_file_phase_is_idempotent(workspace, boundary):
    app, client, root, *_ = workspace
    image(root / 'Dogs' / 'dog1.png')
    before = (root / 'Dogs' / 'dog1.png').read_bytes()
    with app.app_context(): preview = intake.plan('group', 'Dogs')
    persist = intake._persist
    injected = False
    def fail_after_commit(operation_id, transaction):
        nonlocal injected
        persist(operation_id, transaction)
        if transaction['phase'] == boundary and not injected:
            injected = True
            raise OSError('Injected persisted phase interruption')
    with patch.object(intake, '_persist', fail_after_commit):
        operation = accept(client, 'group', preview)
    with app.app_context():
        db.session.remove(); db.engine.dispose()
        intake.recover(operation)
        assert intake.operation_result(operation)['status'] == 'succeeded'
    assert (root / 'Dogs' / 'dog' / 'dog1.png').read_bytes() == before
    assert not list((root / 'Dogs').glob('.tlc-*'))


def test_old_copy_preview_and_force_replacement_are_not_accepted(workspace):
    app, client, root, catalogue, *_ = workspace
    image(root / 'Prepared' / 'Cards' / 'Blue' / 'one.png')
    from app.intake_folder_editor import folder_editor_preview
    with app.app_context(): old = folder_editor_preview(root, 'Prepared/Cards')
    response = client.post('/image-preparation/folders/confirm', data={
        'path': 'Prepared/Cards', 'digest': old['digest'], 'proposal_spec': old['spec_json'], 'acknowledge': 'yes'})
    assert '/result/' not in response.location
    assert (root / 'Prepared' / 'Cards' / 'Blue' / 'one.png').exists()
    assert list(catalogue.iterdir()) == []


def test_scanner_scope_cannot_fall_back_to_whole_catalogue(workspace):
    app, client, root, catalogue, *_ = workspace
    from app.utils.scan_runner import build_scan_scope
    with app.app_context():
        for value in ['', '.', '..', '../outside', 'Missing']:
            result = build_scan_scope(str(catalogue), 'append', collection_relpath=value)
            assert not result.complete and not result.collection_paths
        (catalogue / 'Only').mkdir()
        (catalogue / 'Other').mkdir()
        result = build_scan_scope(str(catalogue), 'append', collection_relpath='Only')
        assert result.collection_paths == (str(catalogue / 'Only'),)
        ordinary = build_scan_scope(str(catalogue), 'append')
        assert set(ordinary.collection_paths) == {str(catalogue / 'Only'), str(catalogue / 'Other')}
