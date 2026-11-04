"""Existing offline 2/4-worker upgrade/restart harness plus local customs CSV.

Run: python3 tests/docker_royal_mail_smoke.py IMAGE [2|4]
Disposable mounts; network none; automatic publication disabled.
"""
import subprocess
import sys
import docker_hs_code_smoke as previous

smoke=previous.smoke
# The reused package-history harness has an archival identity-only product with
# no real source folder. It is not a sellable catalogue fixture; do not invent a
# scan checkpoint for it to pass this export. Preserve its identity/receipts.
archival="sku='SMOKE-PACKAGE-1',product_type='simple',source_relpath='Fictional/Smoke'"
assert smoke.IMPORT.count(archival)==1
smoke.IMPORT=smoke.IMPORT.replace(archival,archival+",catalogue_status='missing'",1)
# Inherited PTY smoke can observe EOF just before the child is reapable. Keep
# its original deadline and every password assertion; wait within that deadline
# instead of treating this tiny process-exit window as a recovery timeout.
reap="  waited,status=os.waitpid(pid,os.WNOHANG)\n  if not waited:\n   os.kill(pid,9)"
assert smoke.CHECK.count(reap)==1
smoke.CHECK=smoke.CHECK.replace(reap,
    "  waited,status=os.waitpid(pid,os.WNOHANG)\n"
    "  while not waited and time.monotonic()<deadline:\n"
    "   time.sleep(.02);waited,status=os.waitpid(pid,os.WNOHANG)\n"
    "  if not waited:\n   os.kill(pid,9)",1)
smoke.CHECK=smoke.CHECK.replace("'hs_code_persistence':True", "'royal_mail_export_persistence':True,'hs_code_persistence':True")
smoke.IMPORT+='''
with app.app_context():
 product=Product.query.filter_by(sku=sku).one()
 revision_before=local_revision(product)
 body={'kind':'shared','revision':review_token(product,'shared'),
       'data':{'customs_description':'Greeting card'},'acknowledge_shared':True}
with patch('app.routes.start_scan',scan_runner.start_scan),patch('app.routes.notify_editor_saved'),patch('app.scheduled_tasks.queue_projected',return_value=None):
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
 assert local_revision(product)==revision_before
 assert product_document(product)['customs_description']=='Greeting card'
'''
smoke.CHECK+='''
import csv,io
assert json.loads(Path('/catalogue/Shipping Fixture/product_info.json').read_text())['customs_description']=='Greeting card'
with sqlite3.connect('/app/instance/site.db') as connection:
 before_changes=connection.total_changes
 assert json.loads(connection.execute('select view_json from shopify_publish_job where product_id=?',(hs_product_id,)).fetchone()[0])['authored']['customs_description']=='Greeting card'
 try:response=opener.open('http://127.0.0.1:7485/packaging/royal-mail.csv',timeout=5)
 except urllib.error.HTTPError as error:
  diagnostic=connection.execute('select requested_revision,view_revision,view_pending,scan_operation_id,view_json from shopify_publish_job where product_id=?',(hs_product_id,)).fetchone()
  raise AssertionError(error.read().decode()+' fixture evidence: '+repr(diagnostic)) from None
 rows=list(csv.DictReader(io.StringIO(response.read().decode('utf-8'))))
 assert response.headers['X-Export-Row-Count']==str(len(rows))
 assert len(rows)==1 and rows[0]['Customs Description']=='Greeting card'
 assert rows[0]['HS Code']=='0012345678901' and rows[0]['Weight (KG)']=='0.05' and rows[0]['Price (GBP)']=='3.50'
 assert rows[0]['Non-standardised Manufacturer ID']==rows[0]['SKU']
 assert connection.total_changes==before_changes
'''

if __name__=='__main__':
    try:smoke.main(sys.argv[1])
    except subprocess.CalledProcessError as error:
        print(error.stdout,error.stderr,file=sys.stderr)
        raise SystemExit(error.returncode)
