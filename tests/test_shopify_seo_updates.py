"""Regression for Shopify's replacement-style SEO input and default title readback."""
import copy
import pytest
from app import shopify_drafts as drafts, shopify_simple_fields as fields
from app.shopify_connection import ShopifyError
from test_shopify_drafts import world
from test_phase3_woo_publish_preview import preview_app
from test_shopify_simple_updates import UpdateAdapter, change


class ReplacingSEOAdapter(UpdateAdapter):
    def update_simple_product(self, config, product):
        result = super().update_simple_product(config, product)
        if 'seo' in product:
            self.remote['seo'] = {k: product['seo'].get(k) for k in ('title', 'description')}
            if self.remote['seo']['title'] == self.remote['title']:
                self.remote['seo']['title'] = None
        return result


@pytest.mark.parametrize('description', [None, 'Remote-only SEO description'])
def test_title_only_update_preserves_unmanaged_description(world, description):
    product = world[0]
    adapter = ReplacingSEOAdapter()
    _, token = drafts.preview(product, adapter)
    row = drafts.confirm(token, True, adapter)
    adapter.remote['seo'] = {'title': 'Old generic title', 'description': description}
    plan, token = drafts.preview(product, adapter)
    assert drafts.update_inputs(plan)[0]['seo']['description'] == description
    drafts.confirm(token, True, adapter)
    assert row.state == 'verified'
    assert adapter.remote['seo'] == {'title': None, 'description': description}
    assert drafts.preview(product, adapter)[0]['classification'] == 'no_change'


def test_changed_title_resends_managed_description_and_unique_override(world):
    product = world[0]
    change(world, meta_title='Original SEO', meta_description='Keep this SEO copy')
    adapter = ReplacingSEOAdapter()
    _, token = drafts.preview(product, adapter)
    row = drafts.confirm(token, True, adapter)
    change(world, meta_title='Unique product SEO')
    plan, token = drafts.preview(product, adapter)
    assert drafts.update_inputs(plan)[0]['seo'] == {'title':'Unique product SEO','description':'Keep this SEO copy'}
    drafts.confirm(token, True, adapter)
    assert row.state == 'verified' and adapter.remote['seo']['description'] == 'Keep this SEO copy'
    # A genuine different title is still blocked, not accepted as a default.
    remote = copy.deepcopy(adapter.remote)
    remote['seo']['title'] = None
    assert any(r['field']=='seo.title' and r['changed'] for r in fields.field_rows(plan['managed'], remote))


def test_readonly_recovery_accepts_only_matching_default_title(world):
    product = world[0]
    adapter = ReplacingSEOAdapter()
    _, token = drafts.preview(product, adapter)
    row = drafts.confirm(token, True, adapter)
    adapter.remote['seo']['title'] = None
    data = drafts.evidence(row)
    drafts.save(row, data, 'uncertain')
    before = list(adapter.calls)
    drafts.reconcile(row, adapter=adapter)
    assert row.state == 'verified'
    assert not any('write' in c or c=='product_update' for c in adapter.calls[len(before):])


@pytest.mark.parametrize('conflict', [None, 'price', 'source', 'lost_response'])
def test_explicit_seo_repair_is_scoped_audited_and_not_repeated(world, conflict):
    product = world[0]
    change(world, meta_title='Original SEO', meta_description='Saved SEO description')
    adapter = ReplacingSEOAdapter()
    _, token = drafts.preview(product, adapter)
    row = drafts.confirm(token, True, adapter)
    change(world, meta_title='Unique SEO')
    _, token = drafts.preview(product, adapter)
    drafts.confirm(token, True, adapter)
    data = drafts.evidence(row)
    data['error'] = 'Managed Simple fields differ from the reviewed plan. Reconcile; do not repeat the update blindly.'
    adapter.remote['seo']['description'] = None
    drafts.save(row,data,'uncertain')
    if conflict == 'price':
        adapter.remote['simpleVariants']['nodes'][0]['price'] = '999.00'
    if conflict == 'source':
        change(world, description='New unreviewed copy')
    if conflict == 'lost_response':
        adapter.update_failure = 'after'
    count = adapter.calls.count('product_update')
    if conflict:
        with pytest.raises(ShopifyError):
            drafts.repair_reviewed_seo(row,adapter)
        if conflict == 'lost_response':
            with pytest.raises(ShopifyError):
                drafts.repair_reviewed_seo(row,adapter)
        assert adapter.calls.count('product_update') == count + (conflict == 'lost_response')
    else:
        from app import db
        from app.models import ShopifyPublishJob
        from app.automatic_publishing import local_revision
        job = ShopifyPublishJob(product_id=product.id,source_identity=product.source_relpath,
            requested_revision=local_revision(product),state='recovery_required',generation=1,
            trigger='inventory_cost',next_attempt=drafts.now(),updated_at=drafts.now())
        db.session.add(job); db.session.commit()
        drafts.repair_reviewed_seo(row,adapter)
        assert row.state == 'verified' and adapter.remote['seo']['description']=='Saved SEO description'
        db.session.refresh(job)
        assert job.state=='verified' and job.verified_revision==job.requested_revision
        assert drafts.evidence(row)['seo_recovery_attempt']['original_operation']
        drafts.repair_reviewed_seo(row,adapter)
        assert adapter.calls.count('product_update') == count + 1
