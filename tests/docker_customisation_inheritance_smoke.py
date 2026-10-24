"""Offline two-worker upgrade/restart plus template and sparse field inheritance."""
import sys
import docker_production_time_smoke as previous

smoke = previous.smoke
smoke.IMPORT += '''
from app.customisation_templates import save as save_template
with app.app_context():
 template=save_template({'name':'Inherited required fields','definition':{
  'storefront_enabled':True,'fields':{
   'recipient':{'type':'text','label':'Recipient Name','required':True,'max_length':30,'order':10},
   'age':{'type':'number','label':'Age','required':True,'whole_number':True,'order':20}}}})
 shared=json.loads((collection/'product_info.json').read_text())
 shared['customisation']={'template':{'id':template['id'],'revision':1}}
 (collection/'product_info.json').write_text(json.dumps(shared))
 before=(collection/'product_info.json').read_bytes()
 project();db.session.expire_all();product=Product.query.filter_by(sku=sku).one()
 body={'kind':'override','revision':review_token(product,'override'),'data':{
  'customisation':{'fields':{'personal_message':{'type':'text','label':'Personal Message','required':False,'order':0}}}}}
with patch('app.routes.start_scan',completed),patch('app.routes.notify_editor_saved'):
 response=client.post('/edit_products/'+sku+'/save',json=body)
 assert response.status_code==200 and response.json['json_saved'],response.json
with app.app_context():
 project();db.session.expire_all()
 assert (collection/'product_info.json').read_bytes()==before
 from app.shopify_customisations import desired
 product=Product.query.filter_by(sku=sku).one()
 assert [f['id'] for f in desired(product)['fields']]==['recipient','age','personal_message']
'''
smoke.CHECK += '''
assert list(bootstrap['authored']['customisation']['fields'])==['personal_message']
assert bootstrap['authored']['customisation']['fields']['personal_message']['order']==0
assert set(bootstrap['resolved']['customisation']['fields'])=={'age','personal_message','recipient'}
from app.customisations import ordered_fields
assert [key for key,_ in ordered_fields(bootstrap['resolved']['customisation'])]==['recipient','age','personal_message']
assert bootstrap['resolved']['customisation']['fields']['recipient']['required'] is True
assert bootstrap['resolved']['customisation']['fields']['recipient']['max_length']==30
assert 'template' in parent['customisation'] and 'fields' not in parent['customisation']
assert 'data-customisation-designer' in page
'''

if __name__ == '__main__':
    smoke.main(sys.argv[1])
