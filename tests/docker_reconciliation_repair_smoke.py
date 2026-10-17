"""Offline upgrade/restart: retain success AND incomplete evidence without promotion.

Reuses two-worker Orders/package/dimensions smoke with disposable synthetic mounts.
"""
import sys
import docker_package_manager_smoke as previous

smoke = previous.smoke
smoke.IMPORT += '''
from app.models import ShopifyProductComparison
with app.app_context():
 for source,complete in [('Fictional/Smoke',False),('Fictional/Readable',True)]:
  db.session.add(ShopifyProductComparison(store_id=payload['store'],source_identity=source,
   summary_json=json.dumps({'read_complete':complete,'full_compared_at':'2026-10-12T00:00:00',
    'package_assignments_unverified':True,**({'package_readback_policy':2} if complete else {})})))
 db.session.commit()
'''
smoke.CHECK += '''
with sqlite3.connect('/app/instance/site.db') as connection:
 comparisons={source:json.loads(raw) for source,raw in connection.execute('select source_identity,summary_json from shopify_product_comparison')}
 assert comparisons['Fictional/Smoke']['read_complete'] is False
 assert 'package_readback_policy' not in comparisons['Fictional/Smoke']
 assert comparisons['Fictional/Readable']['read_complete'] is True
 assert comparisons['Fictional/Readable']['package_readback_policy']==2
 assert all(v['full_compared_at']=='2026-10-12T00:00:00' for v in comparisons.values())
'''

if __name__ == '__main__':
    smoke.main(sys.argv[1])
