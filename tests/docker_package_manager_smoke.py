"""Offline package reviews/links survive0017→0018, two workers and restart.

Reuses the isolated Orders/packed-dimension smoke; no production mounts or network.
"""
import sys
import docker_packed_dimensions_smoke as previous

smoke=previous.smoke
smoke.IMPORT+='''
from app.packaging_models import Package,PackageLink,PackagingEvidence
from app import packaging_catalogue as catalogue
from app.package_reconciliation import native
with app.app_context():
 package=Package.query.filter_by(seed_key='c5-plus').one()
 spec=catalogue.view(package)
 assert spec['max_contents_thickness_mm']=='3' and spec['external_cm']['height'] is None
 assert all(v is None for v in spec['internal_cm'].values())
 db.session.add(PackageLink(package_id=package.id,store_id=payload['store'],
  remote_id='gid://shopify/ShippingPackage/123',checked_at=orders.now(),
  observed=json.dumps({'source':'merchant','api_verified':False,'values':{},'local_baseline':native(catalogue.view(package))})))
 db.session.add(PackagingEvidence(key='package-review:offline-smoke',checked_at=orders.now(),
  value=json.dumps({'state':'completed','remote_writes':0,'source':'synthetic-only'})))
 db.session.commit()
'''
smoke.CHECK+='''
with sqlite3.connect('/app/instance/site.db') as connection:
 packages=connection.execute('select id,seed_key,specification from packaging_package').fetchall()
 assert len(packages)==6 and len({p[0] for p in packages})==6
 c5=next(p for p in packages if p[1]=='c5-plus');spec=json.loads(c5[2])
 assert spec['max_contents_thickness_mm']=='3' and spec['external_cm']['height'] is None
 assert all(v is None for v in spec['internal_cm'].values())
 link=connection.execute('select package_id,remote_id,observed from packaging_link').fetchone()
 assert link[0]==c5[0] and link[1]=='gid://shopify/ShippingPackage/123'
 assert json.loads(link[2])['api_verified'] is False
 receipt=connection.execute("select value from packaging_evidence where key='package-review:offline-smoke'").fetchone()
 assert json.loads(receipt[0])['state']=='completed' and json.loads(receipt[0])['remote_writes']==0
for path in ['/settings','/packaging/','/packaging/reconcile','/packaging/'+c5[0]+'/edit']:
 response=opener.open('http://127.0.0.1:7485'+path,timeout=5)
 assert response.status==200 and 'built-in method' not in response.read().decode()
print(json.dumps({'package_ids_stable':True,'packages':6,'manual_links_not_api_verified':True,'review_receipt_retained':True,'orders_and_dimensions_preserved':True}))
'''
# The reused harness expects one JSON result. Keep its original result as a
# nested assertion source, and emit only the final combined result here.
smoke.CHECK=smoke.CHECK.replace("print(json.dumps({'migration':", "smoke_result=({'migration':",1).replace("'line_ids':[r[0] for r in rows]}))", "'line_ids':[r[0] for r in rows]})",1)
smoke.CHECK=smoke.CHECK.replace("print(json.dumps({'package_ids_stable':True", "print(json.dumps({**smoke_result,'package_ids_stable':True",1)

if __name__=='__main__':smoke.main(sys.argv[1])
