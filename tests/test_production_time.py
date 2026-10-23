"""Captured purchase metadata only: no Shopify calls, today's catalogue or writes."""
import copy
import json

from app import db
from app.models import ShopifyOrderLine
from app.order_personalisation import parse
from app.orders_workspace import line_view
from test_shopify_orders import orders_app, properties, order, save, client, PRODUCT
from test_setup_flow import setup_app


def production(value='3-5 Days'):
    return {'key': 'Production time', 'value': value}


def test_production_only_is_not_customer_personalisation():
    result = parse([production()], PRODUCT)
    assert result['production_times'] == ['3-5 Days']
    assert result['fields'] == result['public'] == result['warnings'] == []
    assert not result['has_personalisation']
    assert not parse([], PRODUCT)['production_times']
    assert parse([production('')], PRODUCT)['production_times'] == []


def test_production_precedes_answers_without_changing_snapshot_or_warnings():
    original = properties('Ellie', 6)
    before = copy.deepcopy(original)
    result = parse([production(), *original], PRODUCT)
    assert result['production_times'] == ['3-5 Days'] and not result['warnings']
    assert [f['display'] for f in result['fields']] == ['Ellie', '6']
    assert [p['label'] for p in result['public']] == ['Recipients Name', 'Anniversary Years']
    assert original == before and result['has_personalisation']
    assert parse([production(), *original, {'key': 'Unexpected answer', 'value': 'retain me'}], PRODUCT)['warnings']


def test_legacy_customer_label_is_not_reinterpreted_as_production():
    original = properties('Ellie', 6)
    snapshot = json.loads(original[-1]['value'])
    snapshot['fields'][0].update(label='Production time', property_key='Production time')
    original[0]['key'] = 'Production time'
    original[-1]['value'] = json.dumps(snapshot)
    result = parse(original, PRODUCT)
    assert result['production_times'] == [] and not result['warnings']
    assert result['fields'][0]['value'] == 'Ellie'


def test_duplicate_or_malformed_evidence_remains_visible():
    result = parse([production('3-5 Days'), production('7-10 Days')], PRODUCT)
    assert result['production_times'] == ['3-5 Days', '7-10 Days'] and result['warnings']
    result = parse([production(), {'key': '_tlc_personalisation', 'value': 'invalid'}], PRODUCT)
    assert result['production_times'] == ['3-5 Days'] and result['warnings']


def test_order_snapshot_restart_and_same_sku_lines(orders_app):
    app, _ = orders_app
    raw = order()
    raw['lineItems']['nodes'][0]['customAttributes'].insert(0, production())
    raw['lineItems']['nodes'][1]['customAttributes'].insert(0, production('5-10 Days'))
    originals = copy.deepcopy(raw['lineItems']['nodes'])
    save(raw)
    rows = ShopifyOrderLine.query.order_by(ShopifyOrderLine.id).all()
    ids = [r.id for r in rows]
    assert [line_view(r)['personalisation']['production_times'] for r in rows] == [['3-5 Days'], ['5-10 Days']]
    assert all(not line_view(r)['personalisation']['warnings'] for r in rows)
    page = client(app).get('/orders/1').get_data(as_text=True)
    assert page.index('Production Time:</strong> 3-5 Days') < page.index('<dt>Recipients Name</dt>')
    assert 'Review personalisation before production' not in page
    db.session.remove()
    rows = ShopifyOrderLine.query.order_by(ShopifyOrderLine.id).all()
    assert [r.id for r in rows] == ids
    assert json.loads(rows[0].properties_json) == originals[0]['customAttributes']
    assert json.loads(rows[1].properties_json) == originals[1]['customAttributes']
    # A subsequent read of the same purchase remains independent of product metadata.
    save(raw)
    assert [r.id for r in ShopifyOrderLine.query.order_by(ShopifyOrderLine.id)] == ids
    assert line_view(rows[0])['personalisation']['production_times'] == ['3-5 Days']


def test_existing_import_is_reparsed_without_rewriting_history(orders_app):
    app, _ = orders_app
    raw = order()
    raw['lineItems']['nodes'][0]['customAttributes'] = [production('&lt;img src=x&gt;')]
    save(raw)
    row = ShopifyOrderLine.query.first()
    row.personalisation_json = json.dumps({'fields': [], 'public': [], 'warnings': ['old derived parser']})
    db.session.commit()
    before = row.personalisation_json, row.properties_json
    assert not line_view(row)['personalisation']['has_personalisation']
    page = client(app).get('/orders/1').get_data(as_text=True)
    assert 'Production Time:</strong> &lt;img src=x&gt;' in page and '<img src=x>' not in page
    assert (row.personalisation_json, row.properties_json) == before


def test_historical_redaction_notice_is_unchanged(orders_app):
    save()
    row = ShopifyOrderLine.query.first()
    row.properties_json = '[]'
    retained = {'fields': [], 'public': [], 'warnings': ['Local personal data was redacted.']}
    row.personalisation_json = json.dumps(retained)
    assert line_view(row)['personalisation'] == retained
