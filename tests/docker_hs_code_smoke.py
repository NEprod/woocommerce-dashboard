"""Reuse existing offline upgrade/publisher persistence checks with one HS field.

Run: python3 tests/docker_hs_code_smoke.py IMAGE [2|4]
Disposable appdata/catalogue only, --network none, no Shopify authority.
"""
import sys
import subprocess
import textwrap
import docker_automatic_publishing_smoke as previous

smoke = previous.smoke
# Reuse its old-appdata seed while checking the actual existing migration head;
# HS Code itself introduces no migration.
smoke.CHECK = smoke.CHECK.replace('0019_automatic_publishing','0021_manual_review_worker').replace('*0018*0019*','*0018*0021*')
smoke.CHECK = smoke.CHECK.replace("'customisation_review':True", "'hs_code_persistence':True,'hs_source_checkpoint':True,'local_draft_protected':True,'customisation_review':True")
smoke.CHECK = smoke.CHECK.replace("assert any(r[4]=='offline-restart-fixture' for r in jobs)",
    "assert any(r[4]=='scan' and r[3]=='paused' for r in jobs)")
# That earlier helper inserts a synthetic release with no authored release date.
# A subsequent real scan must retain its record but cancel that obsolete intent.
smoke.CHECK = smoke.CHECK.replace("schedule_revision='fixture'\").fetchone()==('pending',)",
    "schedule_revision='fixture'\").fetchone()==('cancelled',)")
# The older shipping fixture intentionally omitted publication intent. This
# milestone's checkpoint assertion needs a valid local Draft, not an Attention
# revision correctly generated for an unresolved Draft/Live configuration.
seed = "parent={'collection_type':'Simple','sku_prefix':'SMOKE-SHIP-','title':'Fictional shipping collection',"
assert smoke.IMPORT.count(seed) == 1
smoke.IMPORT = smoke.IMPORT.replace(seed, seed + "'live':False,", 1)
smoke.IMPORT += '''
with app.app_context():
 product=Product.query.filter_by(sku=sku).one()
 body={'kind':'override','revision':review_token(product,'override'),
       'data':{'harmonized_system_code':'0012345678901'}}
before=(collection/'product_info.json').read_bytes()
with patch('app.routes.start_scan',scan_runner.start_scan),patch('app.routes.notify_editor_saved'):
 response=client.post('/edit_products/'+sku+'/save',json=body)
 assert response.status_code==200,response.json
 deadline=time.monotonic()+20
 while time.monotonic()<deadline:
  with app.app_context():progress=scan_runner.get_progress(response.json['run_id'])
  if progress['status'] in {'done','error'}:break
  time.sleep(.05)
 assert progress['status']=='done',progress
with app.app_context():
 db.session.expire_all();product=Product.query.filter_by(sku=sku).one()
 from app.commerce import catalogue_snapshot
 from app.automatic_publishing import local_revision
 job=db.session.get(ShopifyPublishJob,product.id)
 assert job.error is None and job.state=='paused',job.error
 assert job.scan_operation_id==response.json['operation_id']
 current_revision=local_revision(product)
 assert job.requested_revision==current_revision
 assert catalogue_snapshot(product).data['shipping']['harmonized_system_code']=='0012345678901'
 assert not db.session.get(AutomaticPublishing,'shopify').enabled
assert (collection/'product_info.json').read_bytes()==before
'''
# This persistence probe has no Shopify API double inside its container. Keep
# the original scan/ingestion real, but do not dispatch an unmocked read worker
# that could race the next fixture save. The Python lifecycle test exercises
# that real dispatch and scheduler with the controlled API double.
smoke.IMPORT = "from unittest.mock import patch\nwith patch('app.scheduled_tasks.queue_projected',return_value=None):\n" + textwrap.indent(smoke.IMPORT, ' ')
smoke.CHECK += '''
with sqlite3.connect('/app/instance/site.db') as connection:
 hs_product_id=connection.execute("select id from product where source_relpath='Shipping Fixture/Fictional Card'").fetchone()[0]
 job=connection.execute('select requested_revision,scan_operation_id from shopify_publish_job where product_id=?',(hs_product_id,)).fetchone()
 assert len(job[0])==64 and job[1]
 assert connection.execute('select status from catalogue_operation where id=?',(job[1],)).fetchone()==('succeeded',)
 assert json.loads(connection.execute('select view_json from shopify_publish_job where product_id=?',(hs_product_id,)).fetchone()[0])['authored']['harmonized_system_code']=='0012345678901'
hs_page=opener.open('http://127.0.0.1:7485/edit_products/'+str(hs_product_id)+'/edit/override',timeout=5).read().decode()
hs_bootstrap=json.loads(re.search(r'<script id="metadata-editor-bootstrap" type="application/json">(.*?)</script>',hs_page,re.S)[1])
assert hs_bootstrap['authored']['harmonized_system_code']=='0012345678901'
assert hs_bootstrap['resolved']['harmonized_system_code']=='0012345678901'
assert 'HS Code' in hs_page and 'inputmode="numeric"' in hs_page
'''

if __name__=='__main__':
    try:smoke.main(sys.argv[1])
    except subprocess.CalledProcessError as error:
        print(error.stdout,error.stderr,file=sys.stderr)
        raise SystemExit(error.returncode)
