"""Option recipes use actual sellable selections and the existing cost-only publisher."""
import copy
import json

import pytest
from flask import current_app, url_for

from app import db, automatic_publishing as publisher, shopify_drafts as drafts
from app.inventory import product_bom as bom
from app.inventory.models import Movement
from app.models import Product, ScheduledTaskRun
from app.utils.ingest import ingest_rows_to_db
from app.utils.scanner import scan_collection
from test_automatic_publishing import authorise, execute, notices, queued
from test_inventory import client_for, item, manufactured, workshop
from test_metadata_editor_core import core, save
from test_phase3_woo_publish_preview import preview_app
from test_product_bom import CostMock, VariableCost, add, calculate, edit, manual, world
from test_setup_flow import setup_app
from test_shopify_publication_sales import VariableAcceptanceAdapter
from test_shopify_variables import variable_world


def rule(mode, **lines):
    return {'mode':mode, **({'lines':lines} if lines else {})}


def priced(client, code, cost):
    return item(client, 'purchased', code, code.title(), pack_qty='1', pack_unit='piece', pack_price=cost)


def requirements(result):
    return {row['line_id']:(row['item_id'], row['quantity']) for row in result['rows'] if not row['excluded']}


@pytest.fixture
def parts(workshop):
    client = client_for(workshop)
    pla = item(client, 'material', 'PLA', 'Filament', unit='g', pack_qty='100', pack_unit='g', pack_price='3')
    component = manufactured(client, [(pla,'40','g')])
    return {'body':component, **{key:priced(client,key,cost) for key,cost in [
        ('CLICKER','0.15'), ('PACK','0.20'), ('KEY','0.10'), ('LID','0.12'),
        ('A4','0.11'), ('A3','0.23'), ('INSERT','0.07'), ('EXTRA','0.09')]}}


def test_dumpling_combinations_and_variant_modes(workshop, parts):
    base = {**calculate({'body':add(parts['body']), 'clicker':add(parts['CLICKER']), 'pack':add(parts['PACK'])}),
        'options':{'Keyring':{'Yes':rule('add',key=add(parts['KEY']))},
                   'Lid':{'Yes':rule('add',lid=add(parts['LID'])), 'No':rule('inherit')}},
        'variants':{'COMBO':rule('add', insert=add(parts['INSERT'])),
                    'SPECIAL':rule('replace', unique=add(parts['EXTRA']))}}
    untouched = copy.deepcopy(base)
    with workshop.app_context():
        for (key,lid), amount, count in [(('No','No'),'0.65',3), (('Yes','No'),'0.75',4),
                                          (('No','Yes'),'0.77',4), (('Yes','Yes'),'0.87',5)]:
            options = [('Keyring',key),('Lid',lid)]
            result = bom.resolve(base, variant_sku='PLAIN', matches=options)
            assert result['shopify'] == amount and result['count'] == count
            assert requirements(result) == requirements(bom.resolve(base, matches=reversed(options)))
        both = [('Keyring','Yes'),('Lid','Yes')]
        result = bom.resolve(base, variant_sku='COMBO', matches=both)
        assert result['shopify'] == '0.94' and requirements(result)['insert'] == (parts['INSERT'],'1')
        assert {r['origin'] for r in result['rows']} >= {'Shared Keyring = Yes','Shared Lid = Yes','Shared variant'}
        assert bom.resolve(base, variant_sku='SPECIAL', matches=both)['shopify'] == '0.09'
        assert bom.resolve(base, {'variants':{'SPECIAL':rule('inherit')}}, 'SPECIAL', matches=both)['shopify'] == '0.87'
        assert bom.resolve(base, {'variants':{'SPECIAL':rule('replace')}}, 'SPECIAL', matches=both)['shopify'] is None
        assert base == untouched and Movement.query.count() == 0


def test_print_substitution_and_sparse_shared_option_overrides(workshop, parts):
    main = {**calculate({'paper':add(parts['A4']), 'pack':add(parts['PACK'])}),
        'options':{'Size':{'A3':rule('add', paper={'op':'modify','item_id':parts['A3']})},
                   'Style':{'B':rule('add', insert=add(parts['INSERT']))}}}
    local = {'lines':{'extra':add(parts['EXTRA'])},
             'options':{'Style':{'B':rule('add',insert={'op':'modify','quantity':'2'}, extra_insert=add(parts['INSERT']))}}}
    with workshop.app_context():
        assert bom.resolve(main, matches=[('Size','A4')])['shopify'] == '0.31'
        assert bom.resolve(main, matches=[('Size','A3')])['shopify'] == '0.43'
        result = bom.resolve(main, local, matches=[('Style','B'),('Size','A3')])
        assert result['shopify'] == '0.73'
        assert requirements(result) == {'paper':(parts['A3'],'1'), 'pack':(parts['PACK'],'1'),
                                       'extra':(parts['EXTRA'],'1'), 'insert':(parts['INSERT'],'2'),
                                       'extra_insert':(parts['INSERT'],'1')}
        main['options']['Style']['B']['lines']['thank'] = add(parts['KEY'])
        assert bom.resolve(main, local, matches=[('Style','B'),('Size','A3')])['shopify'] == '0.83'
        del local['options']['Style']['B']['lines']['insert']
        assert bom.resolve(main, local, matches=[('Style','B'),('Size','A3')])['shopify'] == '0.76'
        isolated = {'options':{'Style':{'B':rule('inherit')}}}
        assert bom.resolve(main, isolated, matches=[('Style','B'),('Size','A3')])['shopify'] == '0.43'
        assert bom.resolve(main, matches=[('Style','B'),('Size','A3')])['shopify'] == '0.60'
        exclusion = {'options':{'Size':{'A3':rule('add', pack={'op':'remove'})}}}
        assert requirements(bom.resolve(main, exclusion, matches=[('Size','A3')])) == {'paper':(parts['A3'],'1')}


def test_replacement_base_combines_independent_add_and_reports_invalid_targets(workshop, parts):
    main = {**calculate({'pack':add(parts['PACK'])}),
        'options':{'Body':{'Special':rule('replace', shell=add(parts['body']))},
                   'Key':{'Yes':rule('add', attach=add(parts['KEY']))},
                   'Lid':{'Yes':rule('replace', lid=add(parts['LID']))},
                   'Pack':{'Double':rule('add', pack={'op':'modify','quantity':'2'})}}}
    with workshop.app_context():
        base = [('Body','Special'),('Key','Yes')]
        assert requirements(bom.resolve(main, matches=base)) == {'shell':(parts['body'],'1'),'attach':(parts['KEY'],'1')}
        assert bom.resolve(main, matches=base)['shopify'] == '0.40'
        missing = [*base, ('Pack','Double')]
        result = bom.resolve(main, matches=missing)
        assert result['shopify'] is None and result['conflicts'][0]['line_id'] == 'pack'
        assert 'Pack = Double' in result['conflicts'][0]['message']
        exception = {'variants':{'V':rule('add',pack={'op':'remove'})}}
        assert bom.resolve(main, exception, 'V', matches=missing)['shopify'] == '0.40'
        clash = [*base, ('Lid','Yes')]
        result = bom.resolve(main, exception, 'V', matches=clash)
        assert result['shopify'] is None and 'Body = Special' in result['warnings'][0] and 'Lid = Yes' in result['warnings'][0]
        assert bom.resolve(main, {'variants':{'V':rule('replace', special=add(parts['EXTRA']))}}, 'V', matches=clash)['shopify'] == '0.09'
        fixed = bom.resolve(main, {'variants':{'V':manual('0.00')}}, 'V', matches=clash)
        assert fixed['shopify'] == '0.00' and not fixed['picking_ready'] and fixed['estimated'] is None
        main['options']['Pack']['Double']['lines'] = {'shell':{'op':'modify','quantity':'2'}}
        assert bom.resolve(main, matches=missing)['shopify'] == '0.70'


def test_incompatible_targeted_instructions_need_explicit_affected_line(workshop, parts):
    main = {**calculate({'paper':add(parts['A4']), 'pack':add(parts['PACK'])}),
        'options':{'One':{'Yes':rule('add', paper={'op':'modify', 'item_id':parts['A3'], 'quantity':'2'})},
                   'Two':{'Yes':rule('add', paper={'op':'modify','quantity':'3'})}}}
    both = [('One','Yes'),('Two','Yes')]
    with workshop.app_context():
        conflict = bom.resolve(main, matches=both)
        assert conflict['conflicts'][0]['fields'] == ['quantity']
        assert 'One = Yes' in conflict['warnings'][0] and 'Two = Yes' in conflict['warnings'][0]
        unrelated = {'variants':{'SKU':rule('add',pack={'op':'modify','quantity':'2'})}}
        assert not bom.resolve(main, unrelated, 'SKU', matches=both)['picking_ready']
        addressed = {'variants':{'SKU':rule('add',paper={'op':'modify','quantity':'1'})}}
        assert bom.resolve(main, addressed, 'SKU', matches=both)['shopify'] == '0.43'
        main['options']['Two']['Yes']['lines']['paper']['quantity'] = '2.0'
        assert bom.resolve(main, matches=both)['shopify'] == '0.66'
        main['options']['Two']['Yes']['lines']['paper'] = {'op':'remove'}
        assert not bom.resolve(main, matches=both)['picking_ready']
        addressed['variants']['SKU']['lines']['paper'] = {'op':'remove'}
        assert bom.resolve(main, addressed, 'SKU', matches=both)['shopify'] == '0.20'


@pytest.mark.parametrize('layer',[
    {'options':{'Size':{'A3':rule('inherit',pack=add(1))}}},
    {'options':{'Size':{'A3':rule('replace',pack={'op':'remove'})}}},
    {'options':{'Size':{'A3':{**rule('add'),**manual('1')}}}},
])
def test_invalid_rule_modes_do_not_get_silently_reinterpreted(layer):
    with pytest.raises(ValueError):
        bom.validate(layer)


def test_real_editor_persists_exact_option_rules_through_rescan_and_new_combinations(core):
    app, client, root, out, ids, *_ = core
    path = 'Variable Collection/Badge One'
    pkg, large, extra = (priced(client,'OPTIONPACK','0.20'), priced(client,'BIGPRINT','0.30'), priced(client,'NOTE','0.10'))
    source, override = root/'Variable Collection/product_info.json', root/path/'product_info.json'
    main = {**calculate({'pack':add(pkg)}),'options':{'Size':{'Large':rule('add', paper=add(large))}}}
    child = {'options':{'Size':{'Large':rule('add',insert=add(extra))}}}
    shared_save = save(core, path, kind='shared', data={'production':main})
    assert shared_save.status_code == 200, shared_save.json
    initial = source.read_bytes()
    for _ in range(2):
        saved = save(core, path, data={'production':child})
        assert saved.status_code == 200, saved.json
    assert json.loads(override.read_text())['production'] == child and source.read_bytes() == initial
    with app.test_request_context():
        editor = url_for('main.product_edit', product_id=ids[path], label='override')
    markup = client.get(editor).data
    assert b'option_groups' in markup and b'Large' in markup and b'Amber' in markup
    preview = client.post(f'/api/products/{ids[path]}/production-costing', json={'kind':'override','production':child})
    assert preview.status_code == 200
    assert preview.json['options'][0]['result']['shopify'] == '0.60'
    assert preview.json['options'][0]['before']['shopify'] == '0.50'
    edit(source, attributes={'Size':['Large','Small'], 'Colour':['Teal','Amber','Blue']})
    with app.app_context():
        product = db.session.get(Product, ids[path])
        old = {v.source_identity:v.sku for v in product.variations}
        scan = scan_collection(root/'Variable Collection','https://invalid.example/',out,log=lambda *a, **k:None)
        ingest_rows_to_db(scan,log=lambda *a, **k:None)
        db.session.expire_all()
        assert len(product.variations) == 6
        result = bom.product_costs(product)
        for v in product.variations:
            attrs = {a.name:a.value for a in v.attributes}
            assert result[v.sku]['shopify'] == ('0.60' if attrs['Size'] == 'Large' else '0.20')
            if v.source_identity in old:
                assert v.sku == old[v.source_identity]
    edit(source, attributes={'Size':['Renamed Large','Small'], 'Colour':['Teal','Amber','Blue']})
    preview = client.post(f'/api/products/{ids[path]}/production-costing', json={'kind':'override','production':child})
    assert preview.json['orphans'] == [{'name':'Size','value':'Large','local':True}]
    assert all(v['shopify'] == '0.20' for v in preview.json['variants'].values())
    assert json.loads(override.read_text())['production'] == child


@pytest.mark.parametrize('variable_world',['Single Variable'], indirect=True)
def test_signed_review_updates_only_matching_variant_inventory_costs(variable_world):
    product, source, *_ = variable_world
    client = client_for(current_app._get_current_object())
    base, attachment = priced(client,'BASEPACK','0.20'), priced(client,'ATTACH','0.15')
    definition = calculate({'pack':add(base)})
    edit(source, production=definition)
    adapter = VariableCost()
    _, token = drafts.preview(product, adapter)
    linked = drafts.confirm(token, True, adapter)
    assert linked.state == 'verified'
    adapter.cost_writes.clear()
    before = {r['sku']:(r['price'], r['inventoryItem']['tracked']) for r in adapter.remote['variants']['nodes']}
    edit(source, production={**definition,'options':{'Size':{'Maxi':rule('add', attachment=add(attachment))}}})
    plan, token = drafts.preview(product, adapter)
    assert token and not plan['blockers']
    costs = [r for r in plan['fields'] if r['field'].startswith('cost_per_item.') and r['changed']]
    assert len(costs) == 2 and {r['after'] for r in costs} == {'£0.35 (Calculated BOM)'}
    linked = drafts.confirm(token, True, adapter)
    assert linked.state == 'verified' and drafts.evidence(linked)['verification']['item_cost'] == 'verified'
    maxi = [r['inventoryItem']['id'] for r in adapter.remote['variants']['nodes']
            if {'name':'Size','value':'Maxi'} in r['selectedOptions']]
    assert sorted(adapter.cost_writes) == sorted((iid,'0.35') for iid in maxi)
    assert before == {r['sku']:(r['price'], r['inventoryItem']['tracked']) for r in adapter.remote['variants']['nodes']}
    assert drafts.preview(product, adapter)[1] is None
    edit(source, production={**definition,'options':{
        'Size':{'Maxi':rule('replace',attachment=add(attachment))},
        'Theme':{'Moon':rule('replace',box=add(base))}}})
    plan, token = drafts.preview(product, adapter)
    assert token is None and any('Competing replacement BOMs' in blocker for blocker in plan['blockers'])


class AutomaticCost(CostMock, VariableAcceptanceAdapter):
    pass


@pytest.mark.parametrize('variable_world',['Variable Collection'], indirect=True)
def test_hourly_cost_refresh_uses_resolved_option_requirements(variable_world, notices):
    from app import scheduled_tasks as tasks
    from app.inventory.models import Item
    product, source, *_ = variable_world
    client = client_for(current_app._get_current_object())
    base, addon = priced(client,'HOURLYBASE','0.20'), priced(client,'HOURLYADD','0.10')
    edit(source, live=True, production={**calculate({'pack':add(base)}),
        'options':{'Size':{'Large':rule('add', attachment=add(addon))}}})
    adapter = AutomaticCost()
    authorise(adapter)
    assert execute(product, adapter) == 'verified'
    job = queued(product)
    adapter.cost_writes.clear()
    db.session.get(Item,addon).pack_price = 140000
    db.session.commit()
    run_id = tasks.enqueue(publisher.KEY)
    assert run_id and tasks.claim('option-worker') == (publisher.KEY,run_id)
    tasks.run_slice(publisher.KEY,run_id,'option-worker',adapter=adapter)
    db.session.refresh(job)
    assert db.session.get(ScheduledTaskRun,run_id).state == 'completed' and job.state == 'verified'
    impacted = [v['inventoryItem']['id'] for v in adapter.remote['variants']['nodes']
                if {'name':'Size','value':'Large'} in v['selectedOptions']]
    assert sorted(adapter.cost_writes) == sorted((iid,'0.34') for iid in impacted)
    assert Movement.query.count() == 0
