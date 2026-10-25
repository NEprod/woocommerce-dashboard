"""Render the real Review template using isolated effective JSON and remote mocks."""
import copy
import json

from flask import abort, render_template
from flask_wtf import FlaskForm
import customisation_inheritance_fixture as fixture
from app import db
from app.models import Product
from app.shopify_customisations import desired, _summary
from app.shopify_simple_fields import review_rows

app = fixture.app
root = fixture.fixture.catalogue
child = root / 'Simple Collection/Blue Token/product_info.json'
document = json.loads(child.read_text()) if child.exists() else {}
document['customisation'] = {'fields': {'personal_message': {
    'type': 'text', 'label': 'Personal Message', 'required': False, 'max_length': 200}}}
child.write_text(json.dumps(document))


@app.get('/fixture/customisation-review/<scenario>')
def review(scenario):
    if scenario not in {'changes', 'matching', 'unavailable', 'hash-only', 'reordered', 'long'}:
        abort(404)
    product = db.session.get(Product, 1)
    local = desired(product)
    remote = copy.deepcopy(local)
    if scenario == 'changes':
        remote['fields'] = remote['fields'][:-1]
        remote['fields'][0]['required'] = False
    elif scenario == 'unavailable':
        remote = {'enabled': True, 'revision': 'a' * 64}
    elif scenario == 'reordered':
        remote['fields'] = [remote['fields'][0], remote['fields'][2], remote['fields'][1]]
    elif scenario == 'long':
        local['fields'][0]['placeholder'] = 'UnbrokenText' * 80
        remote['fields'][0]['help_text'] = '<img src=x onerror=alert(1)>'
    if scenario != 'matching':
        remote['revision'] = 'a' * 64
    spec = {'desired': local, 'before': {'value': {'value': json.dumps(remote)}},
            'changed': local != remote, 'value_changed': local != remote}
    plan = {'product_id': product.id, 'title': 'Anniversary Card', 'sku': '16bit-0001',
        'scope': 'customisations', 'kind': 'customisations', 'classification': 'no_change' if scenario == 'matching' else 'update',
        'customisations': spec, 'fields': [{'field': 'customisations.configuration',
            'before': _summary(remote), 'after': _summary(local), 'changed': spec['changed']}],
        'price': 'Deferred (configuration-only scope)', 'collections': [], 'intent': False,
        'native': {'enabled': False}, 'publication': {}, 'actual_status': 'DRAFT',
        'remote_id': 'gid://shopify/Product/100', 'images': [], 'warnings': [],
        'blockers': [], 'unsupported': [], 'source_identity': product.source_relpath,
        'handle': 'fictional-card', 'managed': {'product': {}, 'variant': {}, 'namespace': 'tlc_dashboard'}}
    return render_template('shopify_drafts.html', form=FlaskForm(), plan=plan,
        review_rows=review_rows(plan), token='fictional-not-authorised' if spec['changed'] else None,
        error=None, return_to='/products', products=[product], identities=[], scoped_product=product,
        selected_id=product.id)


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=7513, use_reloader=False)
