"""Exact merchant informational workflow in a disposable, network-denied editor."""
from organisation_fixture import app, catalogue, directory, ids, taxonomy
import json
from flask import redirect
from test_informational_attributes import BASE, information_registry
from test_product_shipping_editor import rescan
from app.taxonomy_workspace import validate

(taxonomy/'registry.json').write_bytes(validate(information_registry()))
source=catalogue/'Simple Collection/product_info.json'
shared=json.loads(source.read_text());shared['attributes']=BASE
source.write_text(json.dumps(shared))

@app.get('/fixture/rescan')
def rescan_fixture():
    # Only this synthetic fixture, with the existing loopback-only before_request.
    core=(app,None,catalogue,directory/'output',ids)
    rescan(core,'Simple Collection/Blue Token')
    return redirect(f'/edit_products/{ids["Simple Collection/Blue Token"]}/edit/override')

if __name__=='__main__':
    print(json.dumps({'products':ids,'fixture':str(directory)}),flush=True)
    app.run(host='127.0.0.1',port=7504,use_reloader=False)
