"""Extend the existing isolated two-worker upgrade/restart with purchase metadata."""
import sys
import docker_organisation_smoke as previous

smoke = previous.smoke
smoke.IMPORT += '''
from app.order_personalisation import parse
with app.app_context():
 for line,production in zip(ShopifyOrderLine.query.order_by(ShopifyOrderLine.id),['3-5 Days','5-10 Days']):
  captured=json.loads(line.properties_json)
  captured.insert(0,{'key':'Production time','value':production})
  line.properties_json=json.dumps(captured)
  line.personalisation_json=json.dumps(parse(captured,line.product_id))
 db.session.commit()
'''
smoke.CHECK += '''
with sqlite3.connect('/app/instance/site.db') as connection:
 rows=connection.execute('select properties_json,personalisation_json from shopify_order_line order by id').fetchall()
 assert [json.loads(row[1])['production_times'] for row in rows]==[['3-5 Days'],['5-10 Days']]
 assert all(not json.loads(row[1])['warnings'] for row in rows)
 assert [json.loads(row[0])[0]['value'] for row in rows]==['3-5 Days','5-10 Days']
detail=opener.open('http://127.0.0.1:7485/orders/1',timeout=5).read().decode()
assert detail.index('Production Time:</strong> 3-5 Days')<detail.index('<dt>Recipients Name</dt>')
assert 'Review personalisation before production' not in detail
print(json.dumps({'production_snapshots':True,'same_sku_answers_preserved':True,'no_false_warnings':True}))
'''

if __name__ == '__main__':
    smoke.main(sys.argv[1])
