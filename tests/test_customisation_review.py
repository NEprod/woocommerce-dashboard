"""Public definition comparisons only; no live Shopify or customer answers."""
import copy
import json

import pytest

from app.customisation_review import compare
from app.shopify_simple_fields import review_rows
from app.storefront_customisations import public_configuration
from test_shopify_drafts import world
from test_phase3_woo_publish_preview import preview_app, _client


def configuration(**changes):
    field = {'type': 'text', 'label': 'Recipient Name', 'required': False, **changes}
    return public_configuration({'storefront_enabled': True, 'fields': {'recipient': field}})


def spec(previous, desired):
    return {'before': {'value': {'value': json.dumps(previous)} if previous is not None else None},
            'desired': desired}


def test_identical_definitions_are_matching_and_input_is_not_mutated():
    value = spec(configuration(), configuration())
    original = copy.deepcopy(value)
    result = compare(value)
    assert result['available'] and not result['changes']
    assert [f['id'] for f in result['matching']] == ['recipient']
    assert value == original


@pytest.mark.parametrize('setting,before,after,kind', [
    ('required', False, True, 'text'), ('label', 'Name', 'Recipient Name', 'text'),
    ('type', 'text', 'number', 'text'), ('placeholder', 'Name', 'Your name', 'text'),
    ('help_text', 'Check', 'Check spelling', 'text'), ('default', 'A', 'B', 'text'),
    ('min_length', 1, 2, 'text'), ('max_length', 10, 20, 'text'),
    ('minimum', 1, 2, 'number'), ('maximum', 10, 20, 'number'),
    ('whole_number', False, True, 'number'), ('min_date', '2026-01-01', '2026-02-01', 'date'),
    ('max_date', '2026-10-01', '2026-11-01', 'date'),
    ('min_selections', 0, 1, 'checkbox_group'), ('max_selections', 1, 2, 'checkbox_group'),
])
def test_customer_facing_setting_differences(setting, before, after, kind):
    extra = {'choices': ['a', 'b']} if kind == 'checkbox_group' else {}
    old = configuration(type=kind, **extra)
    new = copy.deepcopy(old)
    old['fields'][0][setting], new['fields'][0][setting] = before, after
    result = compare(spec(old, new))
    assert result['available']
    assert len(result['changes']) == 1 and result['changes'][0]['kind'] == 'Changed'
    assert len(result['changes'][0]['changes']) == 1


def test_choices_and_multiple_changes_to_one_field_are_readable():
    old = configuration(type='dropdown', choices=[{'id': 'red', 'label': 'Red'}])
    new = copy.deepcopy(old)
    new['fields'][0].update(label='Favourite colour', required=True, choices=[{'id': 'blue', 'label': 'Blue'}])
    changes = compare(spec(old, new))['changes']
    assert len(changes) == 1
    assert [c['setting'] for c in changes[0]['changes']] == ['Label', 'Required', 'Allowed choices']
    assert changes[0]['changes'][-1]['before'] == 'Red [red]'
    assert changes[0]['changes'][-1]['after'] == 'Blue [blue]'


def test_add_remove_and_unpublished_are_not_confused_with_unavailable():
    old = configuration()
    new = public_configuration({'storefront_enabled': True, 'fields': {
        'personal_message': {'type': 'text', 'label': 'Personal Message'}}})
    result = compare(spec(old, new))
    assert [(f['id'], f['kind']) for f in result['changes']] == [('personal_message', 'Added'), ('recipient', 'Removed')]
    assert result['changes'][0]['description'] == 'Optional text field'
    result = compare(spec(None, new))
    assert result['available'] and result['before'] == 'Not published'
    assert [f['kind'] for f in result['changes']] == ['Added']


def test_renderer_array_order_not_numeric_metadata_controls_reordering():
    old = configuration()
    old['fields'] += [{'id': 'age', 'label': 'Anniversary Years', 'type': 'number'},
                      {'id': 'message', 'label': 'Personal Message', 'type': 'text'}]
    new = copy.deepcopy(old)
    new['fields'] = [new['fields'][0], new['fields'][2], new['fields'][1]]
    result = compare(spec(old, new))
    message = next(f for f in result['changes'] if f['id'] == 'message')
    assert message['kind'] == 'Reordered'
    assert message['changes'] == [{'setting': 'Position', 'before': '3', 'after': '2'}]
    new = copy.deepcopy(old)
    new['fields'][0]['order'] = 99
    new['revision'] = 'b' * 64
    assert not compare(spec(old, new))['changes']
    new['fields'].insert(0, {'id': 'new', 'type': 'text', 'label': 'New'})
    assert [f['kind'] for f in compare(spec(old, new))['changes']] == ['Added']
    # A retained field at position 2 must not be described as moved 2 → 2.
    new['fields'] = [copy.deepcopy(old['fields'][2]), copy.deepcopy(old['fields'][1])]
    result = compare(spec(old, new))
    assert next(f for f in result['matching'] if f['id'] == 'age')


def test_disabled_state_change_is_separate():
    old, new = configuration(), configuration()
    new['enabled'] = False
    result = compare(spec(old, new))
    assert result['enabled_change'] == {'before': 'Yes', 'after': 'No'}
    assert not result['changes']


def test_multiple_fields_can_change_without_touching_unrelated_settings():
    old = configuration()
    old['fields'].append({'id': 'age', 'label': 'Age', 'type': 'number', 'maximum': 100})
    new = copy.deepcopy(old)
    new['fields'][0]['required'] = True
    new['fields'][1]['maximum'] = 120
    result = compare(spec(old, new))
    assert [f['id'] for f in result['changes']] == ['recipient', 'age']
    assert [f['changes'][0]['setting'] for f in result['changes']] == ['Required', 'Maximum number']


def test_equivalent_json_numbers_are_not_fake_customer_changes():
    old = configuration(type='number', minimum=0, default=0)
    new = copy.deepcopy(old)
    new['fields'][0].update(minimum=0.0, default=0.0)
    assert not compare(spec(old, new))['changes']
    new['fields'][0] = {'id': 'recipient', 'type': 'checkbox', 'label': 'Recipient Name', 'default': False}
    assert 'Default value' in [c['setting'] for c in compare(spec(old, new))['changes'][0]['changes']]


def test_summary_only_retained_plan_keeps_technical_revision_without_inference():
    result = compare(None, {'enabled': True, 'fields': 2, 'revision': 'a' * 64},
                     {'enabled': True, 'fields': 2, 'revision': 'b' * 64})
    assert not result['available'] and not result['changes']
    assert result['before'] == result['after'] == 'Enabled · 2 fields'
    assert result['previous_revision'] == 'a' * 64 and result['reviewed_revision'] == 'b' * 64


@pytest.mark.parametrize('previous', [
    {'revision': 'a' * 64}, {'enabled': True, 'fields': 2, 'revision': 'a' * 64},
    {'schema_version': 2, 'enabled': True, 'fields': []},
    {'schema_version': 1, 'enabled': True, 'fields': [{'id': 'recipient', 'label': 'Name', 'type': []}]},
])
def test_revision_only_or_incomplete_previous_definitions_never_infer_added_fields(previous):
    result = compare(spec(previous, configuration()))
    assert not result['available'] and result['changes'] == [] and result['matching'] == []
    assert 'unavailable' in result['message']


def test_missing_readback_and_invalid_json_never_imply_absent_configuration():
    for before in ({}, {'value': {'value': 'invalid'}}, {'value': {'value': '[]'}}):
        assert not compare({'before': before, 'desired': configuration()})['available']


def test_duplicate_or_unknown_field_definitions_fall_back_without_dropping_fields():
    old = configuration()
    old['fields'].append(copy.deepcopy(old['fields'][0]))
    assert not compare(spec(old, configuration()))['available']
    old = configuration()
    old['fields'][0]['future_customer_rule'] = 'unknown'
    assert not compare(spec(old, configuration()))['available']


def test_hash_only_changes_and_effective_defaults_do_not_invent_field_changes():
    old, new = configuration(), configuration()
    old['fields'][0].pop('required')
    new['fields'][0].update(placeholder='', help_text='', default='', min_length=0)
    new['revision'] = 'b' * 64
    result = compare(spec(old, new))
    assert result['available'] and not result['changes']
    assert result['previous_revision'] != result['reviewed_revision']
    assert 'No customer-facing field differences' in result['message']


def test_review_rows_are_presentation_only_and_keep_existing_change_action():
    value = spec(configuration(), configuration(required=True))
    plan = {'classification': 'update', 'customisations': value, 'fields': [
        {'field': 'customisations.configuration', 'before': {'revision': 'a' * 64},
         'after': {'revision': 'b' * 64}, 'changed': True}]}
    original = copy.deepcopy(plan)
    row, = review_rows(plan)
    assert row['action'] == 'Change' and row['group'] == 'customisations'
    assert 'revision' not in row['before'] and 'revision' not in row['after']
    assert row['customisation_details']['changes'][0]['changes'][0]['setting'] == 'Required'
    assert plan == original


def test_template_collection_product_effective_review_and_signed_safety(world):
    from app import shopify_drafts as drafts
    from app.customisation_templates import save
    from test_storefront_customisations import create
    from test_shopify_simple_updates import change
    template = save({'name': 'Review template', 'definition': {'fields': {
        'recipient': {'type': 'text', 'label': 'Recipient Name', 'required': True, 'order': 10}}}})
    change(world, customisation={'template': {'id': template['id'], 'revision': 1},
        'storefront_enabled': True, 'fields': {'age': {
            'type': 'number', 'label': 'Anniversary Years', 'required': True, 'order': 0}}})
    child = world[1] / 'product_info.json'
    local = {'customisation': {'fields': {'message': {'type': 'text', 'label': 'Personal Message', 'order': 0}}}}
    child.write_text(json.dumps(local))
    row, adapter = create(world, enabled=False)
    local['customisation']['fields']['message']['placeholder'] = 'Optional greeting'
    child.write_text(json.dumps(local))
    parent_bytes, child_bytes = world[2].read_bytes(), child.read_bytes()
    adapter.calls.clear()
    plan, token = drafts.preview(world[0], adapter, scope='customisations')
    original = copy.deepcopy(plan)
    review, = [r for r in review_rows(plan) if r['group'] == 'customisations']
    assert [f['id'] for f in plan['customisations']['desired']['fields']] == ['recipient', 'age', 'message']
    assert [f['id'] for f in review['customisation_details']['matching']] == ['recipient', 'age']
    assert [f['id'] for f in review['customisation_details']['changes']] == ['message']
    assert plan == original and token
    assert not any('write' in call for call in adapter.calls)
    with pytest.raises(drafts.ShopifyError, match='acknowledgement'):
        drafts.confirm(token, False, adapter)
    assert (parent_bytes, child_bytes) == (world[2].read_bytes(), child.read_bytes())
    # The explanation never changes the compare-digest/readback safety path.
    result = drafts.confirm(token, True, adapter)
    assert result.state == 'verified' and result.id == row.id
    matched, matching_token = drafts.preview(world[0], adapter, scope='customisations')
    assert matched['classification'] == 'no_change' and matching_token is None


def test_production_time_and_answers_are_not_compared():
    old, new = configuration(), configuration()
    old['production_time'], new['production_time'] = '3 days', '5 days'
    old['_tlc_personalisation'] = {'private': 'not a field'}
    result = compare(spec(old, new))
    assert result['available'] and not result['changes']
    assert 'production_time' not in json.dumps(result) and '_tlc_personalisation' not in json.dumps(result)
