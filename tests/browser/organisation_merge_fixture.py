"""Four-section normal editor plus merchant legacy replacement; all data disposable."""
import copy
import json
from flask import redirect, jsonify
from organisation_fixture import app, catalogue, directory, ids, taxonomy
from test_organisation_additive_editor import BASE, merchant_override, registry_document
from test_product_shipping_editor import rescan
from app.taxonomy_workspace import validate

(taxonomy/'registry.json').write_bytes(validate(registry_document()))
parents={}
for name in ('Simple Collection','Variable Collection','Single Variable'):
    source=catalogue/name/'product_info.json';shared=json.loads(source.read_text())
    axes=[] if shared['collection_type']=='Simple' else list(shared.get('attributes',{}))
    shared.update(categories=['Cards'],storefront_collections=['16-Bit'],tags=['Personalised','Pixel Art'],
        shopify_product_type_key='cat-cards',variation_attributes=axes,
        attributes={**(shared.get('attributes',{}) if axes else {}),**BASE},
        taxonomy_additions={'attributes':{'attr-lead-time':['term-3-5-days']}})
    source.write_text(json.dumps(shared))
    if name!='Single Variable':parents[name]=source.read_bytes()
path=catalogue/'Simple Collection/Blue Token/product_info.json'
original=merchant_override();path.write_text(json.dumps(original))


@app.get('/fixture/rescan/<int:product_id>')
def scan_fixture(product_id):
    owner=next(name for name,value in ids.items() if value==product_id)
    rescan((app,None,catalogue,directory/'output',ids),owner)
    for name,before in parents.items():assert (catalogue/name/'product_info.json').read_bytes()==before
    return redirect(f'/edit_products/{product_id}/edit/'+('shared' if owner=='Single Variable' else 'override'))


@app.get('/fixture/check/<int:product_id>')
def check_fixture(product_id):
    owner=next(name for name,value in ids.items() if value==product_id)
    document=json.loads((catalogue/owner/'product_info.json').read_text())
    for name,before in parents.items():assert (catalogue/name/'product_info.json').read_bytes()==before
    if product_id==ids['Simple Collection/Blue Token']:
        for key in ('packaging','sale_price','sale_start_date','sale_end_date'):assert document[key]==original[key]
    return jsonify(authored=document,parents_unchanged=True,
        backups=len(list((catalogue/owner).glob('*.bak.*'))))


if __name__=='__main__':
    print(json.dumps({'fixture':str(directory),'products':ids}),flush=True)
    app.run(host='127.0.0.1',port=7504,use_reloader=False)
