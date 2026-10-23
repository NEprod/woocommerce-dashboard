"""Organisation effective membership display/persistence contract; synthetic sources only."""
import copy
import json

import pytest

from app import db, organisation, shopify_drafts as drafts, shopify_taxonomy as taxonomy
from app.models import Product, ShopifyCollectionIdentity
from app.taxonomy_assignments import product_document
from app.taxonomy_workspace import empty_registry, validate
from test_metadata_editor_core import core, save
from test_product_shipping_editor import bootstrap, rescan
from test_organisation_inheritance import choices
from test_shopify_drafts import world
from test_shopify_taxonomy_products import registry, created
from test_shopify_simple_updates import change
from test_phase3_woo_publish_preview import preview_app


def install_registry(app, path):
    document = empty_registry()
    for kind in ('categories', 'storefront_collections', 'tags', 'attributes'):
        for row in choices()[kind]:
            item = {key: row[key] for key in ('key', 'name', 'state', 'aliases')}
            item.update(slug=row['key'], name=item['name'].split(' > ')[-1])
            if kind == 'categories':
                item['parent'] = row['parent']
            if kind == 'attributes':
                item.update(navigation=False, visible_default=True, terms=[
                    {key: term[key] for key in ('key', 'name', 'state', 'aliases')} | {'slug': term['key']}
                    for term in row['terms']])
            document[kind].append(item)
    path.mkdir()
    (path/'registry.json').write_bytes(validate(document))
    app.config['TAXONOMY_ROOT'] = str(path)


@pytest.mark.parametrize('owner,kind', [
    ('Simple Collection/Blue Token', 'override'),
    ('Variable Collection/Badge One', 'override'),
    ('Single Variable', 'shared'),
])
def test_memberships_save_reopen_rescan_and_reload_from_authoritative_sparse_source(core, tmp_path, owner, kind):
    app, client, root, _, ids, *_ = core
    install_registry(app, tmp_path/'taxonomy')
    source = root/owner.split('/')[0]/'product_info.json'
    shared = json.loads(source.read_text())
    shared.update(categories=['Cards'], storefront_collections=['Pixel'], shopify_product_type_key='cat-cards')
    source.write_text(json.dumps(shared))
    before = source.read_bytes()
    authored_path = root/owner/'product_info.json'
    original = json.loads(authored_path.read_text())
    data = {'categories': ['Cards > Birthday'], 'storefront_collections': ['Spring']}
    if kind=='shared':
        data={'categories':['Cards',*data['categories']],'storefront_collections':['Pixel',*data['storefront_collections']]}
    with app.app_context():
        product = db.session.get(Product, ids[owner])
        identities = (product.id, product.sku, product.regular_price, product.weight,
                      [(v.id, v.sku, v.regular_price, v.weight) for v in product.variations])
    assert save(core, owner, kind=kind, data=data).status_code == 200
    assert json.loads(authored_path.read_text()) == {**original, **data}
    if kind == 'override':
        assert source.read_bytes() == before
        assert json.loads(authored_path.read_text())['categories']==['Cards > Birthday']
        assert json.loads(authored_path.read_text())['storefront_collections']==['Spring']
    reopened = bootstrap(client.get(f'/edit_products/{ids[owner]}/edit/{kind}'))
    assert all(reopened['authored'][key]==value for key,value in data.items())
    for effective in (reopened['resolved'], rescan(core, owner)):
        assert effective['categories'] == ['Cards', 'Cards > Birthday']
        assert effective['storefront_collections'] == ['Pixel', 'Spring']
        assert effective['shopify_product_type_key'] == 'cat-cards'
    with app.app_context():
        db.session.remove()  # reload persisted projection, not a cached ORM instance
        product = db.session.get(Product, ids[owner])
        assert identities == (product.id, product.sku, product.regular_price, product.weight,
                              [(v.id, v.sku, v.regular_price, v.weight) for v in product.variations])
        assert product_document(product)['storefront_collections'] == ['Pixel', 'Spring']
        assert organisation.product_type(product_document(product)) == 'Cards'
        # All three layouts reach the same reviewed parent-membership planner.
        remote_ids = {}
        for index, key in enumerate(('categories:cat-cards', 'categories:cat-birthday',
                                     'storefront_collections:range-pixel', 'storefront_collections:range-spring')):
            remote_ids[key] = f'gid://shopify/Collection/{1000+index}'
            db.session.add(ShopifyCollectionIdentity(store_id='gid://shopify/Shop/42', source_key=key,
                remote_id=remote_ids[key], state='verified', evidence_json='{"version":1}'))
        db.session.commit()

        class ReadOnlyCollections:
            def collection_state(self, config, remote_id, product_id):
                assert remote_id in remote_ids.values() and product_id == 'gid://shopify/Product/100'
                return {'id': remote_id, 'title': 'Retained collection', 'ruleSet': None,
                        'hasProduct': True, 'sources': []}

        planned, blockers, _ = taxonomy.membership_plan(product, 'gid://shopify/Shop/42',
            'gid://shopify/Product/100', None, ReadOnlyCollections())
        assert not blockers
        assert {row['key']: row['remote_id'] for row in planned} == remote_ids
        assert all(row['action'] == 'Already assigned' for row in planned)
    assert save(core, owner, kind=kind, data=data).json['unchanged']
    if kind == 'override':
        assert source.read_bytes() == before
    # Remove only the local memberships; collection values remain authoritative.
    restore={key:original[key] for key in data if key in original}
    assert save(core, owner, kind=kind, data=restore, removals=[[key] for key in data if key not in original]).status_code == 200
    effective = rescan(core, owner)
    assert effective['categories'] == ['Cards']
    assert effective['storefront_collections'] == ['Pixel']
    if kind == 'override':
        assert source.read_bytes() == before


def test_shopify_review_combines_inherited_and_added_memberships_without_touching_mappings(world, registry):
    receipt, adapter = created(world)
    adapter.remote['productType'] = ''
    change(world, categories=['Cards'], storefront_collections=[], shopify_product_type_key='cards')
    child = world[1]/'product_info.json'
    original = json.loads(child.read_text()) if child.exists() else {}
    child.write_text(json.dumps({**original,'categories':['Cards > Birthday Cards'],'storefront_collections':['Personalised']}))
    shared_before = world[2].read_bytes()
    for key, remote_id, name in [('categories:birthday', 'gid://shopify/Collection/903', 'Birthday Cards'),
                                 ('storefront_collections:personalised', 'gid://shopify/Collection/904', 'Personalised')]:
        db.session.add(ShopifyCollectionIdentity(store_id=receipt.store_id, source_key=key,
            remote_id=remote_id, state='verified', evidence_json='{"version":1}'))
        adapter.collections[remote_id] = {'title': name, 'handle': name.lower(), 'marker': None, 'members': set()}
    db.session.commit()
    identities = [(r.id, r.source_key, r.remote_id, r.state, r.evidence_json) for r in ShopifyCollectionIdentity.query.all()]
    remote_before = copy.deepcopy(adapter.collections)
    plan, token = drafts.preview(world[0], adapter)
    assert token and not plan['blockers'], plan['blockers']
    assert {row['key']: row['action'] for row in plan['collections']} == {
        'categories:cards': 'Already assigned', 'categories:birthday': 'Join',
        'storefront_collections:personalised': 'Join'}
    assert plan['managed']['product']['productType'] == 'Cards'
    assert all(row['remote_id'] for row in plan['collections'])
    assert identities == [(r.id, r.source_key, r.remote_id, r.state, r.evidence_json) for r in ShopifyCollectionIdentity.query.all()]
    assert adapter.collections == remote_before
    assert not any(call in adapter.calls for call in ('collection_join', 'collection_create', 'product_update'))
    assert world[2].read_bytes() == shared_before
