"""The local planner uses the existing product cost and never publishes discounts."""

import pytest

from app import db
from app.inventory import costing
from app.promotions import planner, publish, shopify
from app.promotions.models import Promotion
from test_inventory import client_for, workshop
from test_phase3_woo_publish_preview import preview_app
from test_setup_flow import setup_app
from test_shopify_drafts import world


def _row(sku, price):
    return {'local_id':1, 'sku':sku, 'price':price, 'name':sku, 'options':{}}


def _cost(amount, *, available=True):
    return {'cost':amount, 'bom_complete':available, 'mode':'calculated',
            'material_component':amount, 'packaging':None, 'lines':[],
            'missing':[] if available else ['Product cost is missing.']}


def test_fixed_total_uses_repeatable_variant_cost_extremes(monkeypatch):
    monkeypatch.setattr(planner, 'variant_costs', lambda rows: {'A':_cost(250), 'B':_cost(400)})
    result = planner.review({'kind':'exact','tiers':[{'qty':3,'total':1600}]},
                            [_row('A','6.00'), _row('B','7.00')])
    tier = result['tiers'][0]
    assert (tier['best_cost'], tier['worst_cost']) == (750,1200)
    assert (tier['best_contribution'], tier['worst_contribution']) == (850,400)
    assert (tier['best_margin'], tier['worst_margin']) == ('53.1','25.0')
    assert (tier['best_markup'], tier['worst_markup']) == ('113.3','33.3')
    assert (tier['normal_low'], tier['normal_high']) == (1800,2100)
    assert result['repeat_variants_allowed'] and not tier['loss_risk']


def test_missing_cost_never_counts_as_zero_and_loss_is_visible(monkeypatch):
    monkeypatch.setattr(planner, 'variant_costs', lambda rows: {'A':_cost(900), 'B':_cost(None, available=False)})
    result = planner.review({'kind':'exact','tiers':[{'qty':3,'total':1600}]},
                            [_row('A','5.00'), _row('B','6.00')])
    tier = result['tiers'][0]
    assert not result['bom_complete'] and tier['worst_cost'] is None
    assert tier['worst_margin'] is None and tier['discount_low'] == 0 and tier['bound_only']
    assert result['missing']
    monkeypatch.setattr(planner, 'variant_costs', lambda rows: {'A':_cost(600)})
    loss = planner.review({'kind':'exact','tiers':[{'qty':3,'total':1600}]}, [_row('A','6.00')])['tiers'][0]
    assert loss['loss_risk'] and loss['worst_contribution'] == -200
    assert loss['worst_margin'] == '-12.5'


def test_offer_never_uses_a_surcharged_price_for_profit(monkeypatch):
    monkeypatch.setattr(planner, 'variant_costs', lambda rows: {'A':_cost(200)})
    result = planner.review({'kind':'exact','tiers':[{'qty':3,'total':1995}]}, [_row('A','6.00')])
    tier = result['tiers'][0]
    assert tier['normal_low'] == 1800 and tier['bound_only']
    assert tier['best_cost'] == 600 and tier['best_contribution'] is None
    assert tier['best_margin'] is None and tier['best_markup'] is None


def test_effective_local_cost_matches_shopify_contract_projection(world, monkeypatch):
    product, *_ = world
    calls = []
    def product_costs(local_product):
        calls.append(local_product.id)
        return {product.sku:{'rows':[], 'state':'complete','mode':'calculated',
                             'shopify':'2.47','warnings':[]}}
    monkeypatch.setattr(costing, 'product_costs', product_costs)
    from app.inventory import profitability
    monkeypatch.setattr(profitability, 'finished_product_estimate',
                        lambda *args, **kwargs: dict(complete=True, cost='3.47', missing=[], labour='1.00'))
    found = costing.variant_costs([{'local_id':product.id,'sku':product.sku}])
    assert calls == [product.id]
    assert found[product.sku]['cost'] == 347
    assert found[product.sku]['bom_complete']
    assert found[product.sku]['packaging'] is None


def test_manual_product_cost_is_used_but_not_misrepresented_as_itemised(world, monkeypatch):
    product, *_ = world
    monkeypatch.setattr(costing, 'product_costs', lambda _: {product.sku:{'rows':[], 'state':'empty',
        'mode':'manual','shopify':'1.20','warnings':[]}})
    found = costing.variant_costs([{'local_id':product.id,'sku':product.sku}])[product.sku]
    assert found['cost'] == 120 and found['bom_complete']
    assert found['material_component'] is None and found['packaging'] is None
    assert any('component breakdown' in message for message in found['missing'])


def test_retired_promotion_write_entrypoints_refuse_before_any_shopify_call(workshop, monkeypatch):
    client = client_for(workshop)
    response = client.post('/promotions/new', data=dict(name='Local only', kind='exact', method='automatic',
        scope='product', intent='draft', tier_qty=['2'], tier_total=['9.00']))
    assert response.status_code == 303
    with workshop.app_context():
        row = Promotion.query.one()
        ident = row.id
        before = (row.document, row.receipt, row.state)
        class Forbidden:
            def __getattribute__(self, name):
                raise AssertionError('The retired path contacted Shopify')
        with pytest.raises(ValueError, match='retired'):
            publish.confirm(row, 'old-signed-approval', 'operator', Forbidden())
        with pytest.raises(ValueError, match='retired'):
            publish.reconcile(row, Forbidden())
        db.session.refresh(row)
        assert (row.document, row.receipt, row.state) == before
    assert client.get(f'/promotions/{ident}/review').status_code == 200
    retired = client.post(f'/promotions/{ident}/review', data={'action':'confirm','ack':'yes'})
    assert retired.status_code == 410 and b'retired' in retired.data
    assert b'name="approval"' not in client.get(f'/promotions/{ident}/review').data
    admin = shopify.Admin(shopify.Configuration(domain='fictional-dev.myshopify.com',
        store='gid://shopify/Shop/42', app='gid://shopify/App/321', client='1'*32,
        kid='sample', private_key='/not/needed.pem', uk_market='gid://shopify/Market/22'))
    with pytest.raises(ValueError, match='retired'):
        admin._graphql('mutation Any { discountDelete(id:"1") { userErrors { message } } }')
    with pytest.raises(ValueError, match='retired'):
        admin.apply('create', {}, None)


def test_existing_definition_without_description_remains_unchanged():
    from app.promotions import domain
    definition = domain.definition({'name':'Original', 'kind':'exact','method':'automatic',
        'scope':'product','intent':'draft','starts':None,'ends':None,'targets':[],
        'exclusions':[],'combines':{},'tiers':[{'qty':2,'total':900}],'repeat':True})
    assert 'description' not in definition
