"""Actual reconciliation/planner graph; fixed mock GraphQL responses, zero network."""
import json
from time import perf_counter
import resource
import os
import xml.etree.ElementTree as ET
import pytest
from sqlalchemy import event
from app import db,shopify_status as status
from app.models import ShopifyProductComparison
from test_shopify_drafts import world
from test_phase3_woo_publish_preview import preview_app
from reconciliation_profile_fixture import catalogue
from test_shopify_taxonomy_products import registry


@pytest.mark.parametrize('count',[15,100,500,1000])
def test_reconciliation_request_graph_before_after(world,registry,count,record_property,monkeypatch):
    if count>15 and not (os.environ.get('TLC_RECONCILIATION_BASELINE') or os.environ.get('TLC_PROFILE_SCALES')=='1'):
        pytest.skip('Opt-in scaling benchmark: set TLC_PROFILE_SCALES=1 or provide measured baseline XML.')
    monkeypatch.setattr('app.utils.discord.publishing_message',lambda data:(False,'disabled'))
    from app.taxonomy_registry import registry_inspection
    # Fixture construction is outside the measured reconciliation. Its registry
    # is immutable while cloning, so avoid irrelevant setup parse overhead.
    with registry_inspection(): products,adapter=catalogue(world,count,registry)
    results=[]
    baseline_file=os.environ.get('TLC_RECONCILIATION_BASELINE')
    if baseline_file:
        baselines=[json.loads(p.get('value'))[0] for p in ET.parse(baseline_file).iter('property') if p.get('name')=='reconciliation_benchmark']
        results=[next(b for b in baselines if b['products']==count)]
    for reuse in ((True,) if baseline_file else (False,True)):
        ShopifyProductComparison.query.delete();db.session.commit()
        profile={};sql=[]
        def statement(*args):sql.append(1)
        event.listen(db.engine,'before_cursor_execute',statement)
        started=perf_counter();checked=[];errors=[]
        try:
            # Same full-comparison slice size as the scheduled coordinator.
            from app.scheduled_tasks import FULL_PER_SLICE
            for offset in range(0,count,FULL_PER_SLICE):
                result=status.refresh(products[offset:offset+FULL_PER_SLICE],adapter=adapter,force=True,full_limit=FULL_PER_SLICE,reuse_references=reuse)
                checked.extend(result['checked']);errors.extend(result['errors'])
                from app.shopify_read_scope import accumulate
                accumulate(profile,result['request_profile'])
        finally:event.remove(db.engine,'before_cursor_execute',statement)
        classifications=[json.loads(r.summary_json)['classification'] for r in ShopifyProductComparison.query.order_by(ShopifyProductComparison.source_identity)]
        results.append({'reuse':reuse,'products':count,'seconds':round(perf_counter()-started,3),
            'sql':len(sql),'profile':profile,'checked':len(checked),'errors':errors,'classifications':classifications,
            'peak_rss_mib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024/1024})
        assert len(checked)==count and not errors
    assert results[0]['classifications']==results[1]['classifications']
    assert results[1]['profile']['http_requests']<results[0]['profile']['http_requests']
    assert results[1]['profile']['cache_hits']>0
    assert results[1]['profile']['cost_observations']==0  # mocked counts are not actual Shopify cost
    record_property('reconciliation_benchmark',json.dumps(results))
