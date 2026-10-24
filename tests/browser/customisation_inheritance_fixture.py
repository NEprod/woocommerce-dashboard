"""Isolated canonical JSON/template inheritance. Every outbound request is denied."""
import json
import sys
from flask import jsonify
import organisation_fixture as fixture
from app import db
from app.customisation_templates import save
from app.models import Product
from app.taxonomy_assignments import product_document
from app.utils.scanner import scan_collection
from app.utils.ingest import ingest_rows_to_db
from test_customisation_additive import SHARED

app = fixture.app
with app.app_context():
    template = save({'name': 'Inherited customer fields', 'definition': SHARED})
for name in ('Simple Collection', 'Variable Collection', 'Single Variable'):
    path = fixture.catalogue / name / 'product_info.json'
    document = json.loads(path.read_text())
    document['customisation'] = {'template': {'id': template['id'], 'revision': 1}}
    path.write_text(json.dumps(document))
parents = {name: (fixture.catalogue/name/'product_info.json').read_bytes()
           for name in ('Simple Collection', 'Variable Collection', 'Single Variable')}

@app.post('/fixture/rescan/<int:product_id>')
def rescan(product_id):
    with app.app_context():
        product = db.session.get(Product, product_id)
        name = product.source_relpath.split('/')[0]
        path = fixture.catalogue / name
        rows = scan_collection(path, 'https://invalid.example/', fixture.directory/'output',
                               force_update=True, update_csv=True, log=lambda *a, **k: None)
        assert ingest_rows_to_db(rows, log=lambda *a, **k: None)['products_failed'] == 0
        db.session.expire_all()
        product = db.session.get(Product, product_id)
        return jsonify(effective=product_document(product)['customisation'],
            authored=json.loads((fixture.catalogue/product.source_relpath/'product_info.json').read_text()),
            parent_unchanged=parents[name] == (path/'product_info.json').read_bytes())

if __name__ == '__main__':
    print(json.dumps({'products': fixture.ids, 'fixture': str(fixture.directory)}), flush=True)
    app.run(host='127.0.0.1', port=7511, use_reloader=False)
