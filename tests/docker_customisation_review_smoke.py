"""Current offline two-worker upgrade/restart plus readable review from saved JSON."""
import subprocess
import sys
import docker_override_scan_smoke as previous

smoke = previous.smoke
smoke.IMPORT += '''
with app.app_context():
 product=Product.query.filter_by(sku=sku).one()
 authored=json.loads((collection/'Fictional Card/product_info.json').read_text())
 body={'kind':'override','revision':review_token(product,'override'),'data':{
  'customisation':{**authored['customisation'],'proof_policy':'optional'}}}
with patch('app.routes.start_scan',completed),patch('app.routes.notify_editor_saved'):
 response=client.post('/edit_products/'+sku+'/save',json=body)
 assert response.status_code==200,response.json
with app.app_context():
 project();db.session.expire_all()
 from app.order_personalisation import parse
 for line,choice,display in zip(ShopifyOrderLine.query.order_by(ShopifyOrderLine.id),
   ['yes','no'],['Yes, send me a proof for approval.','No, proceed without a proof.']):
  properties=json.loads(line.properties_json)
  snapshot_property=next(p for p in properties if p['key']=='_tlc_personalisation')
  snapshot=json.loads(snapshot_property['value'])
  snapshot['proof']={'policy':'optional','choice':choice,'display':display,'property_key':'Digital proof'}
  snapshot_property['value']=json.dumps(snapshot)
  properties.append({'key':'Digital proof','value':display})
  line.properties_json=json.dumps(properties)
  line.personalisation_json=json.dumps(parse(properties,line.product_id))
 db.session.commit()
'''
smoke.CHECK = smoke.CHECK.replace("'local_pages':True", "'customisation_review':True,'local_pages':True")
smoke.CHECK += '''
from copy import deepcopy
from flask import Flask,render_template
from app.storefront_customisations import public_configuration
from app.shopify_simple_fields import review_rows
current=public_configuration(bootstrap['resolved']['customisation'])
assert [f['id'] for f in current['fields']]==['recipient','age','personal_message']
previous=deepcopy(current);previous['fields']=previous['fields'][:-1]
previous['fields'][0]['required']=False;previous['revision']='a'*64
spec={'before':{'value':{'value':json.dumps(previous)}},'desired':current}
plan={'classification':'update','customisations':spec,'fields':[{
 'field':'customisations.configuration','before':{},'after':{},'changed':True}]}
rows=review_rows(plan)
view=Flask('review-smoke',template_folder='/app/app/templates')
with view.test_request_context():
 rendered=render_template('includes/_customisation_review.html',review_rows=rows)
assert 'Required: No' in rendered and 'Personal Message</strong> — Added' in rendered
assert 'Technical details' in rendered and current['revision'] in rendered
assert rows[0]['customisation_details']['matching'][0]['id']=='age'
assert 'revision' not in rows[0]['before'] and 'revision' not in rows[0]['after']
assert bootstrap['authored']['customisation']['proof_policy']=='optional'
assert current['proof_policy']=='optional'
previous['proof_policy']='required'
spec['before']['value']['value']=json.dumps(previous)
assert review_rows(plan)[0]['customisation_details']['proof_change']=={'before':'Required','after':'Optional'}
with sqlite3.connect('/app/instance/site.db') as connection:
 proofs=[json.loads(row[0]) for row in connection.execute('select personalisation_json from shopify_order_line order by id')]
 assert [p['proof']['choice'] for p in proofs]==['yes','no']
 assert all(not p['warnings'] for p in proofs)
detail=opener.open('http://127.0.0.1:7485/orders/1',timeout=5).read().decode()
assert 'Digital proof:</strong> Yes, send me a proof for approval.' in detail
assert 'Digital proof:</strong> No, proceed without a proof.' in detail
'''

if __name__ == '__main__':
    try:
        smoke.main(sys.argv[1])
    except subprocess.CalledProcessError as error:
        print(error.stderr, file=sys.stderr)
        raise SystemExit(error.returncode)
