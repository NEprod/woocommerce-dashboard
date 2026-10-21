"""Reuse the offline two-worker persistence chain, adding a taxonomy mount."""
from pathlib import Path
import sys
import docker_password_recovery_smoke as previous

smoke=previous.smoke
original_docker=smoke.docker


def docker(*args, **kwargs):
    if args and args[0]=='run':
        mounts=[a for a in args if a.startswith('type=bind,src=') and a.endswith(',dst=/catalogue')]
        if mounts:
            directory=Path(mounts[0].split('src=',1)[1].split(',dst=',1)[0]).parent/'taxonomy'
            directory.mkdir(exist_ok=True)
            directory.chmod(0o777)  # disposable synthetic fixture; never a real mount
            position=args.index(sys.argv[1])
            args=(*args[:position],'--mount',f'type=bind,src={directory},dst=/taxonomy',*args[position:])
    return original_docker(*args,**kwargs)


smoke.docker=docker
smoke.IMPORT+='''
from app.taxonomy_workspace import empty_registry,validate
registry=empty_registry()
def entry(key,name,**extra):return dict(key=key,name=name,slug=key,state='active',**extra)
registry['categories']=[entry('cat-cards','Cards',parent=None),entry('cat-gifts','Gifts',parent=None)]
registry['storefront_collections']=[entry('range-pixel','Pixel'),entry('range-spring','Spring')]
registry['attributes']=[entry('attr-recipient','Recipient',navigation=False,visible_default=True,
 terms=[entry('term-him','Him'),entry('term-her','Her'),entry('term-mum','Mum')])]
Path('/taxonomy/registry.json').write_bytes(validate(registry))
shared=json.loads((collection/'product_info.json').read_text())
shared.update(attributes={'Recipient':['Him','Her']},variation_attributes=[],
 storefront_collections=['Pixel'],shopify_product_type_key='cat-cards')
(collection/'product_info.json').write_text(json.dumps(shared))
before=(collection/'product_info.json').read_bytes()
with app.app_context():
 project();db.session.expire_all();product=Product.query.filter_by(sku=sku).one()
 body={'kind':'override','revision':review_token(product,'override'),'data':{
  'shopify_product_type_key':'cat-gifts','taxonomy_additions':{
   'attributes':{'attr-recipient':['term-mum']},'storefront_collections':['range-spring']}}}
with patch('app.routes.start_scan',completed),patch('app.routes.notify_editor_saved'):
 response=client.post('/edit_products/'+sku+'/save',json=body)
 assert response.status_code==200 and response.json['json_saved'],response.json
with app.app_context():
 project();db.session.expire_all();product=Product.query.filter_by(sku=sku).one()
 resolved=product_document(product)
 assert resolved['attributes']['Recipient']==['Him','Her','Mum']
 assert resolved['storefront_collections']==['Pixel','Spring']
 assert (collection/'product_info.json').read_bytes()==before
'''
smoke.CHECK+='''
assert bootstrap['authored']['shopify_product_type_key']=='cat-gifts'
assert bootstrap['resolved']['attributes']['Recipient']==['Him','Her','Mum']
assert bootstrap['resolved']['storefront_collections']==['Pixel','Spring']
assert bootstrap['collection']['shopify_product_type_key']=='cat-cards'
assert 'taxonomy_additions' not in bootstrap['collection']
assert bootstrap['authored']['taxonomy_additions']=={'attributes':{'attr-recipient':['term-mum']},'storefront_collections':['range-spring']}
assert 'data-parent-product-type' in page
'''

if __name__=='__main__':smoke.main(sys.argv[1])
