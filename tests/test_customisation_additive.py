"""Canonical customisation fields only; no live catalogue or Shopify writes."""
import copy
import json

import pytest

from app.customisations import merge, ordered_fields
from app.storefront_customisations import public_configuration
from app.utils.json_utils import merge_product_json
from test_metadata_editor_core import core, save

SHARED = {'storefront_enabled': True, 'proof_policy': 'required', 'fields': {
    'recipient': {'type': 'text', 'label': 'Recipient Name', 'required': True,
                  'max_length': 30, 'placeholder': 'Name', 'help_text': 'Check spelling', 'order': 10},
    'age': {'type': 'number', 'label': 'Age', 'required': True,
            'minimum': 1, 'maximum': 120, 'whole_number': True, 'order': 20}}}
LOCAL = {'fields': {'personal_message': {'type': 'text', 'label': 'Personal Message',
                                        'required': False, 'max_length': 200, 'order': 0}}}


def test_inherited_first_then_local_preserves_each_group_without_source_rewrite():
    shared, local = copy.deepcopy(SHARED), copy.deepcopy(LOCAL)
    result = merge(shared, local)
    assert list(result['fields']) == ['recipient', 'age', 'personal_message']
    assert [key for key, _ in ordered_fields(result)] == ['recipient', 'age', 'personal_message']
    assert result['fields']['recipient'] == SHARED['fields']['recipient']
    assert result['fields']['age'] == SHARED['fields']['age']
    assert result['proof_policy'] == 'required'
    assert [f['id'] for f in public_configuration(result)['fields']] == ['recipient', 'age', 'personal_message']
    assert shared == SHARED and local == LOCAL


@pytest.mark.parametrize('patch,reason', [({'required': False}, 'recipient.*type'),
    ({'remove': True}, 'recipient.remove is obsolete'), (SHARED['fields']['recipient'], 'Duplicate.*recipient')])
def test_matching_inherited_id_is_rejected_not_applied(patch, reason):
    with pytest.raises(ValueError, match=reason):
        merge(SHARED, {'fields': {'recipient': patch}})


def test_absent_empty_disabled_and_unrelated_scalars_keep_existing_meaning():
    assert merge(SHARED, {}) == merge(SHARED, {'fields': {}})
    assert not merge(SHARED, {'storefront_enabled': False})['storefront_enabled']
    data = merge_product_json({'customisation': SHARED, 'price': '3.50', 'weight': 50},
                              {'customisation': LOCAL, 'price': '4.50'})
    assert data['price'] == '4.50' and data['weight'] == 50
    assert len(data['customisation']['fields']) == 3


@pytest.mark.parametrize('kind', ['collection', 'override'])
@pytest.mark.parametrize('remove', [True, False])
def test_obsolete_remove_key_is_named_and_never_accepted(kind, remove):
    from app.product_info import validate_product_info
    document = {'customisation': {'fields': {'message': {
        'type': 'text', 'label': 'Message', 'remove': remove}}}}
    result = validate_product_info(document, kind)
    assert not result.valid
    assert 'customisation.fields.message.remove is obsolete' in result.errors[0].message


def test_child_template_is_invalid_even_without_inherited_fields_and_full_duplicate_is_rejected(core):
    from app.customisations import SCHEMA, validate
    from app.product_info import load_schema, validate_product_info
    from jsonschema import Draft202012Validator
    from app.customisation_templates import save as template_save
    assert 'remove' not in SCHEMA['properties']['fields']['additionalProperties']['properties']
    assert SCHEMA['properties']['template']['type'] == 'object'
    with core[0].app_context():
        template = template_save({'name': 'Protected definitions', 'definition': SHARED})
        reference = {'id': template['id'], 'revision': 1}
        for value in (None, reference):
            document = {'customisation': {'template': value}}
            before = copy.deepcopy(document)
            result = validate_product_info(document, 'override')
            assert not result.valid and 'customisation.template is obsolete in Product Overrides' in result.errors[0].message
            assert list(Draft202012Validator(load_schema('override')).iter_errors(document))
            with pytest.raises(ValueError, match='obsolete'): merge({}, document['customisation'])
            assert document == before
        with pytest.raises(ValueError, match='template:null is obsolete'): validate({'template': None})
        full_duplicate = {'fields': {'recipient': {**SHARED['fields']['recipient'], 'required': False}}}
        before = copy.deepcopy(full_duplicate)
        with pytest.raises(ValueError, match='Duplicate.*recipient'): merge(SHARED, full_duplicate)
        assert full_duplicate == before and SHARED['fields']['recipient']['required'] is True


def test_template_then_collection_then_product_and_legacy_conflicts(core):
    from app.customisation_templates import save as template_save
    with core[0].app_context():
        template = template_save({'name': 'Required recipient', 'definition': SHARED})
        reference = {'id': template['id'], 'revision': template['revision']}
        shared = {'template': reference, 'fields': {
            'style': {'type': 'choice', 'label': 'Style', 'choices': ['Plain', 'Script'],
                      'default': 'Plain', 'order': 0}}}
        before = copy.deepcopy(shared)
        effective = merge(shared, LOCAL)
        assert [key for key, _ in ordered_fields(effective)] == ['recipient', 'age', 'style', 'personal_message']
        assert effective['fields']['recipient'] == SHARED['fields']['recipient']
        assert shared == before
        for instruction in ({'template': None}, {'template': {'id': 'different', 'revision': 1}},
                            {'fields': {'age': {'required': False}}}, {'fields': {'age': {'remove': True}}}):
            original = copy.deepcopy(instruction)
            with pytest.raises(ValueError, match='obsolete|type.*required'): merge(shared, instruction)
            assert instruction == original
        with pytest.raises(ValueError, match='type.*required'):
            merge({'template': reference, 'fields': {'recipient': {'max_length': 10}}}, {})
        assert merge(shared, {'fields': {}}) == merge(shared, {})


def test_conflict_blocks_only_affected_product_without_source_rewrite(core):
    from app import db
    from app.models import Product
    from app.shopify_customisations import desired
    from app.shopify_connection import ShopifyError
    app, client, root, _, ids, *_ = core
    parent = root / 'Simple Collection/product_info.json'
    document = json.loads(parent.read_text()); document['customisation'] = copy.deepcopy(SHARED)
    parent.write_text(json.dumps(document)); parent_bytes = parent.read_bytes()
    child = root / 'Simple Collection/Blue Token/product_info.json'
    document = json.loads(child.read_text()); document['customisation'] = {'fields': {'recipient': {'required': False}}}
    child.write_text(json.dumps(document)); child_bytes = child.read_bytes()
    affected = ids['Simple Collection/Blue Token']; good = ids['Simple Collection/Green Token']
    assert client.get('/products').status_code == 200
    from app.routes import build_products_data, parse_products_filters
    with app.test_request_context(): build_products_data(parse_products_filters({}))
    assert client.get('/api/edit_products').status_code == 200
    response = client.get(f'/edit_products/{affected}/edit/override')
    assert response.status_code == 409 and b'recipient' in response.data and b'type' in response.data
    from app.metadata_workspace import editor_workspace
    with app.test_request_context(): editor_workspace(db.session.get(Product, good), 'override')
    good_response = client.get(f'/edit_products/{good}/edit/override')
    assert good_response.status_code == 200, good_response.data.decode()
    with app.app_context():
        with pytest.raises(ShopifyError): desired(db.session.get(Product, affected))
        assert [f['id'] for f in desired(db.session.get(Product, good))['fields']] == ['recipient', 'age']
    from app.shopify_products import product_state
    from types import SimpleNamespace
    uncertain = SimpleNamespace(state='verified', shopify_product_id='gid://shopify/Product/123', id=1,
        media_evidence_json=json.dumps({'version': 1, 'package_assignments': {'variant': {'state': 'uncertain'}}}))
    with app.test_request_context():
        state = product_state(db.session.get(Product, affected), [uncertain])
        assert state['state'] == 'recovery_required' and 'recipient' in state['message']
        uncertain.media_evidence_json = '{}'
        assert product_state(db.session.get(Product, affected), [uncertain])['state'] == 'recovery_required'
    assert parent.read_bytes() == parent_bytes and child.read_bytes() == child_bytes
    from app.utils.scanner import scan_collection
    diagnostics = []
    rows = scan_collection(parent.parent, 'https://invalid.example/', core[3],
        force_update=True, update_csv=True, log=lambda message, **kw: diagnostics.append(str(message)))
    assert rows and any('recipient' in message and 'type' in message for message in diagnostics)
    assert parent.read_bytes() == parent_bytes and child.read_bytes() == child_bytes


def test_fresh_database_reconstructs_effective_fields_from_canonical_json(core, tmp_path):
    from app import create_app, db
    from app.models import Product, Settings
    from app.shopify_customisations import desired
    from app.utils.scanner import scan_collection
    from app.utils.ingest import ingest_rows_to_db
    from config import Config
    root, output = core[2], core[3]
    shared = root/'Simple Collection/product_info.json'
    document = json.loads(shared.read_text()); document['customisation'] = copy.deepcopy(SHARED)
    shared.write_text(json.dumps(document))
    assert save(core, data={'customisation': LOCAL}).status_code == 200
    before = {str(p): p.read_bytes() for p in root.rglob('product_info.json')}
    previous = Config.SQLALCHEMY_DATABASE_URI
    try:
        Config.SQLALCHEMY_DATABASE_URI = 'sqlite:///' + str(tmp_path/'fresh.db')
        rebuilt = create_app(recover=False)
        with rebuilt.app_context():
            db.session.add(Settings(product_folder=str(root), output_folder=str(output), url_prefix='https://invalid.example/'))
            db.session.commit()
            rows = scan_collection(shared.parent, 'https://invalid.example/', output, force_update=True,
                                   update_csv=True, log=lambda *a, **k: None)
            assert ingest_rows_to_db(rows, log=lambda *a, **k: None)['products_failed'] == 0
            product = Product.query.filter_by(source_relpath='Simple Collection/Blue Token').one()
            first = desired(product)
            db.session.remove()
            product = Product.query.filter_by(source_relpath='Simple Collection/Blue Token').one()
            assert desired(product) == first
            assert [f['id'] for f in first['fields']] == ['recipient', 'age', 'personal_message']
            db.session.remove()
    finally:
        Config.SQLALCHEMY_DATABASE_URI = previous
    assert before == {str(p): p.read_bytes() for p in root.rglob('product_info.json')}


@pytest.mark.parametrize('name,kind', [('Simple Collection/Blue Token', 'override'),
    ('Variable Collection/Badge One', 'override'), ('Single Variable', 'shared')])
def test_save_reopen_rescan_and_only_local_fields_persist(core, name, kind):
    from app import db
    from app.models import Product
    from app.metadata_workspace import editor_workspace
    from app.taxonomy_assignments import product_document
    from app.utils.scanner import scan_collection
    from app.utils.ingest import ingest_rows_to_db
    app, client, root, output, ids, *_ = core
    parent = root / (name if kind == 'shared' else name.rsplit('/', 1)[0]) / 'product_info.json'
    definition = json.loads(parent.read_text())
    if kind == 'override': definition['customisation'] = copy.deepcopy(SHARED)
    parent.write_text(json.dumps(definition)); before = parent.read_bytes()
    response = save(core, name, kind=kind, data={'customisation': LOCAL})
    assert response.status_code == 200, response.json
    child = root / name / 'product_info.json'
    assert json.loads(child.read_text())['customisation'] == LOCAL
    if kind == 'override': assert parent.read_bytes() == before
    for _ in range(2):
        with app.test_request_context():
            rows = scan_collection(parent.parent, 'https://invalid.example/', output,
                                   force_update=True, update_csv=True, log=lambda *a, **k: None)
            assert ingest_rows_to_db(rows, log=lambda *a, **k: None)['products_failed'] == 0
            db.session.expire_all()
            product = db.session.get(Product, ids[name])
            expected = ['personal_message'] if kind == 'shared' else ['recipient', 'age', 'personal_message']
            assert [key for key, _ in ordered_fields(product_document(product)['customisation'])] == expected
            workspace = editor_workspace(product, kind)
            assert workspace['authored']['data']['customisation'] == LOCAL
            assert client.get(f'/edit_products/{product.id}/edit/{kind}').status_code == 200
