"""Exact merchant informational workflow in a disposable, network-denied editor."""
from organisation_fixture import app, catalogue, directory, ids, taxonomy
import json
from flask import redirect, jsonify
from test_informational_attributes import BASE, information_registry
from test_product_shipping_editor import rescan
from app.taxonomy_workspace import validate
from app import db
from app.models import Product
from app.taxonomy_assignments import product_document

(taxonomy/'registry.json').write_bytes(validate(information_registry()))
source=catalogue/'Simple Collection/product_info.json'
shared=json.loads(source.read_text());shared['attributes']=BASE
source.write_text(json.dumps(shared))
parent_before=source.read_bytes()
legacy=catalogue/'Simple Collection/Green Token/product_info.json'
legacy_document=json.loads(legacy.read_text()) if legacy.exists() else {}
legacy_document.update(attributes={'Recipient':['For Wife','For Husband'],'Occasion':['Anniversary']})
legacy.write_text(json.dumps(legacy_document))

@app.get('/fixture/rescan')
def rescan_fixture():
    # Only this synthetic fixture, with the existing loopback-only before_request.
    core=(app,None,catalogue,directory/'output',ids)
    rescan(core,'Simple Collection/Blue Token')
    return redirect(f'/edit_products/{ids["Simple Collection/Blue Token"]}/edit/override')

@app.get('/fixture/assert-additions')
def assert_additions():
    own=json.loads((catalogue/'Simple Collection/Blue Token/product_info.json').read_text())
    assert source.read_bytes()==parent_before
    assert own['attributes']=={'Lead Time':['3–5 Days'],'Occasion':['Anniversary']}
    effective=product_document(db.session.get(Product,ids['Simple Collection/Blue Token']))
    assert effective['attributes']=={**BASE,'Lead Time':['3–5 Days'],'Occasion':['Anniversary']}
    return jsonify(parent_unchanged=True,sparse_json=True,effective_after_scan=effective['attributes'])

if __name__=='__main__':
    print(json.dumps({'products':ids,'fixture':str(directory)}),flush=True)
    app.run(host='127.0.0.1',port=7504,use_reloader=False)
