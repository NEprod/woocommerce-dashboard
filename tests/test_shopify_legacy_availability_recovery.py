"""Angel's retained Simple Draft scenario with fictional Shopify and SQLite."""
import copy
import json

import pytest
from flask import current_app, g

from app import db, shopify_continuation as checkpoint, shopify_drafts as drafts, shopify_publication as publication
from app.models import CatalogueOperation, ShopifyProductIdentity
from app.shopify_capacity import BackgroundYield, RequestNotSent
from app.shopify_connection import ShopifyError
from test_automatic_publishing import queued
from test_manual_publishing_worker import accepted, offline, products_badge, step
from test_phase3_woo_publish_preview import preview_app, _client
from test_shopify_attribute_capabilities import CapabilitiesAdapter
from test_shopify_attribute_policy import legacy
from test_shopify_drafts import world
from test_shopify_markets import MarketAdapter, market, publication as market_publication
from test_shopify_publication_sales import AcceptanceAdapter
from test_shopify_simple_updates import change
from test_shopify_taxonomy_products import registry

OLDER = "**Cozy Crochet Christmas Collection**\n\nA wonderfully knitted appearance."
CURRENT = "<p><strong>Cozy Crochet Christmas Collection</strong> — the charming appearance of a handmade crochet creation.</p>"
CHANNELS = {f'gid://shopify/Publication/{i}' for i in (1, 2, 3)}


class AngelAdapter(MarketAdapter, CapabilitiesAdapter):
    pause_at_shop = False

    def set_publication(self, config, product_id, pid, publish):
        if pid in CHANNELS:
            if self.pause_at_shop and pid.endswith('/2'):
                self.pause_at_shop = False
                raise RequestNotSent('Paused before dispatching Shop publication.', code='request_budget')
            return AcceptanceAdapter.set_publication(self, config, product_id, pid, publish)
        return super().set_publication(config, product_id, pid, publish)


@pytest.fixture
def angel(world, registry, monkeypatch):
    product = world[0]
    product.sku = 'CCCC-0001'
    product.images.pop()
    (world[1] / 'second.jpg').unlink()  # Remove the extra original from this pytest-only catalogue.
    db.session.commit()
    legacy(registry)
    change(world, description=OLDER, live=True, categories=['Cards'], attributes={'Occasion':['Birthday']},
           shopify_channels={'online-store':True, 'shop':True, 'pos':True}, shopify_market_ids=[market(2)])
    adapter = AngelAdapter()
    adapter.memberships.clear()
    original, _ = accepted(world, adapter)
    with monkeypatch.context() as old:
        old.setattr(checkpoint, 'begin', lambda *a, **k: False)

        def stop(*_):
            raise BackgroundYield('Historical pause after base readback; no availability request sent.')

        old.setattr(publication, 'comparison_after_write', stop)
        step(adapter)
    row = ShopifyProductIdentity.query.one()
    assert db.session.get(CatalogueOperation, original).recovery_state == 'review_required'
    assert row.shopify_product_id == adapter.remote['id']
    assert len(adapter.remote['media']['nodes']) == 1 and adapter.resources and adapter.collections
    assert not adapter.memberships and not adapter.market_writes
    change(world, description=CURRENT)
    drafts.reconcile(row, adapter=adapter)
    assert (row.state, row.verification_state) == ('creation_review_required', 'base_verified')
    adapter.calls.clear()
    adapter.inputs.clear()
    return row, adapter, original


def test_pending_description_survives_availability_restart_and_then_syncs_separately(world, angel, monkeypatch):
    row, adapter, original = angel
    product = world[0]
    product_id, gid = product.id, row.shopify_product_id
    history = db.session.get(CatalogueOperation, original).scope
    remote, collections, resources, values = map(copy.deepcopy,
        (adapter.remote, adapter.collections, adapter.resources, adapter.attr_values))
    job = queued(product)
    job.operation_id, job.state, job.error = original, 'recovery_required', 'Historical draft hold.'
    db.session.commit()
    plan, token = drafts.preview(product, adapter)
    assert token and plan['deferred_description'] and plan['scope'] == 'availability_only'
    assert all(f['publication_only'] for f in plan['fields'])
    assert 'market_read' in adapter.calls and 'publication_read' in adapter.calls
    assert plan['managed']['product']['descriptionHtml'] == OLDER
    assert plan['publication']['markets']['actions'][0]['after'] is True
    assert not adapter.market_writes and adapter.remote == remote
    monkeypatch.setattr(drafts, 'ShopifyAdapter', lambda: adapter)
    ui = _client(current_app).post(f'/products/{product.id}/shopify/review', data={'action':'preview'})
    assert ui.status_code == 200 and b'Main description: deferred' in ui.data
    assert b'Confirm availability only' in ui.data and b'eligible Market changes' in ui.data
    assert CURRENT.encode() not in ui.data
    with pytest.raises(ShopifyError, match='acknowledgement'):
        drafts.confirm(token, False, adapter)
    with pytest.raises(ShopifyError, match='evidence changed'):
        drafts.require_simple_creation_review(row, product.id, {**drafts.serializer().loads(token), 'scope':'full'})

    recovery, token = accepted(world, adapter)
    adapter.pause_at_shop = True
    step(adapter, owner='first-worker')
    op = db.session.get(CatalogueOperation, recovery)
    assert op.status == 'pending' and op.recovery_state == 'none'
    assert adapter.calls.count('status_write') == adapter.calls.count('publication_write') == 1
    assert adapter.market_writes == [(gid, market_publication(2), True)]
    assert adapter.remote['descriptionHtml'] == OLDER
    db.session.remove()
    db.engine.dispose()
    g.pop('_login_user', None)  # Pytest retains this app context across the replacement worker.
    step(adapter, owner='replacement-worker')
    op = db.session.get(CatalogueOperation, recovery)
    assert op.status == 'succeeded' and op.recovery_state == 'none', op.error
    assert json.loads(op.scope)['operation_summary']['scope'] == 'availability_only'
    assert db.session.get(CatalogueOperation, original).scope == history
    assert ShopifyProductIdentity.query.one().shopify_product_id == gid
    assert adapter.memberships == CHANNELS and adapter.market_writes == [(gid, market_publication(2), True)]
    assert adapter.calls.count('status_write') == 1 and adapter.calls.count('publication_write') == 3
    assert adapter.remote == {**remote, 'status':'ACTIVE'}
    assert (adapter.collections, adapter.resources, adapter.attr_values) == (collections, resources, values)
    assert not adapter.inputs
    assert set(adapter.calls).isdisjoint({'product_create','variant_write','binary_upload','collection_join','collection_create','attribute_assignment_write'})
    assert products_badge(db.session.get(type(product), product_id))['label'] == 'Update Available'
    with pytest.raises(ShopifyError, match='recovery state changed'):
        drafts.confirm(token, True, adapter)

    product = db.session.get(type(product), product_id)
    plan, token = drafts.preview(product, adapter)
    assert token and plan['scope'] == 'full'
    assert [field['field'] for field in plan['fields'] if field['changed']] == ['descriptionHtml']
    normal, _ = accepted((product,), adapter)
    step(adapter)
    assert db.session.get(CatalogueOperation, normal).status == 'succeeded'
    assert ShopifyProductIdentity.query.one().shopify_product_id == gid
    assert adapter.inputs == [{'id':gid,'descriptionHtml':CURRENT}]
    assert adapter.remote == {**remote, 'status':'ACTIVE', 'descriptionHtml':CURRENT}
    assert products_badge(product)['label'] == 'Up to Date'


@pytest.mark.parametrize('problem', ['market_scope', 'default_uk_scope', 'partial_market', 'partial_channels'])
def test_incomplete_destination_evidence_cannot_issue_approval(world, angel, monkeypatch, problem):
    row, adapter, _ = angel
    if problem in {'market_scope', 'default_uk_scope'}:
        adapter.market_scope = False
        if problem == 'default_uk_scope':
            source = world[2]
            authored = json.loads(source.read_text())
            authored.pop('shopify_market_ids')
            source.write_text(json.dumps(authored))
    elif problem == 'partial_market':
        read = adapter.market_publication_state

        def incomplete(*args):
            result = read(*args)
            result['resourcePublicationsV2']['pageInfo']['hasNextPage'] = True
            return result

        monkeypatch.setattr(adapter, 'market_publication_state', incomplete)
    else:
        adapter.incomplete_channels = True
    plan, token = drafts.preview(world[0], adapter)
    assert token is None and plan['blockers']
    assert any(text in plan['blockers'][0] for text in ('evidence', 'readback is incomplete', 'read_markets'))
    assert row.state == 'creation_review_required' and adapter.remote['status'] == 'DRAFT'
    assert not adapter.inputs and not adapter.market_writes and not adapter.memberships


def test_uncertain_availability_write_is_not_replayed(world, angel):
    row, adapter, _ = angel
    recovery, token = accepted(world, adapter)
    adapter.channel_timeout = True
    step(adapter)
    assert row.state == 'uncertain'
    assert db.session.get(CatalogueOperation, recovery).recovery_state == 'review_required'
    assert drafts.evidence(row)['dispatch'].startswith('publication.')
    before = list(adapter.calls)
    step(adapter, owner='replacement-after-uncertain')
    assert adapter.calls == before and adapter.remote['descriptionHtml'] == OLDER
    with pytest.raises(ShopifyError, match='not verified'):
        drafts.confirm(token, True, adapter)


def test_membership_changed_after_approval_requires_a_fresh_review(world, angel):
    row, adapter, original = angel
    recovery, _ = accepted(world, adapter)
    adapter.memberships.add('gid://shopify/Publication/2')  # Changed independently before the signed operation ran.
    step(adapter)
    op = db.session.get(CatalogueOperation, recovery)
    assert op.status == 'failed' and 'changed since review' in op.error
    assert row.operation_id == original and row.state == 'creation_review_required'
    assert adapter.remote['status'] == 'DRAFT' and adapter.remote['descriptionHtml'] == OLDER
    assert 'status_write' not in adapter.calls and 'publication_write' not in adapter.calls and not adapter.market_writes
