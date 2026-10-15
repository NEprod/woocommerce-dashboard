"""Fictional sources/isolated DBs only. Local authoring never calls Shopify."""
import copy
import json
import re
import sqlite3
import time

import pytest
from alembic import command
from sqlalchemy import create_engine, text

from app import db
from app.customisations import validate, merge, ordered_fields, answer_error
from app import customisation_templates as templates
from app.models import CustomisationTemplate, Product, User, CatalogueOperation
from app.metadata_workspace import resolved_metadata
from app.database import _alembic_config, ensure_database
from app.shopify_products import source_fingerprint
from test_metadata_editor_core import core, payload, save


DEFINITION = {'schema_version': 2, 'proof_policy': 'optional', 'fields': {
    'dog_name': {'type': 'text', 'label': "Dog's Name", 'required': True, 'max_length': 30, 'order': 0}}}


def create(name='Pet Ornament', definition=None):
    return templates.save({'name': name, 'description': 'Internal library note', 'definition': definition or DEFINITION})


@pytest.mark.parametrize('field', [
    {'type':'text','min_length':1,'max_length':30,'default':'Pip'},
    {'type':'multiline','help_text':'Please check spelling','placeholder':'Message'},
    {'type':'textarea','max_length':500}, {'type':'number','minimum':0,'maximum':120,'whole_number':True,'default':4},
    {'type':'choice','choices':['A','B'],'default':'B'},
    {'type':'dropdown','choices':[{'id':'a','label':'Design A'},{'id':'b','label':'Design B'}],'default':'a'},
    {'type':'radio','choices':[{'id':'script','label':'Script'}],'default':'script'},
    {'type':'checkbox','default':False},
    {'type':'checkbox_group','choices':[{'id':'star','label':'Star'},{'id':'heart','label':'Heart'}], 'min_selections':1,'max_selections':2,'default':['star']},
    {'type':'date','min_date':'2026-01-01','max_date':'2027-01-01','default':'2026-10-16'},
    {'type':'colour','choices':[{'id':'red','label':'Red','value':'#ff0000'}],'default':'#ff0000'},
    {'type':'photo','allowed_extensions':['.png','.jpg'],'allowed_media_categories':['image'],'max_file_size_bytes':10000000,'max_files':2},
    {'type':'file_upload','allowed_media_categories':['document'],'allowed_extensions':['.pdf']},
])
def test_all_types_and_legacy_aliases(field):
    definition={'fields':{'input':{'label':'Customer input',**field}}}
    before=copy.deepcopy(definition);validate(definition);assert definition==before


@pytest.mark.parametrize('patch', [
    {'type':'text','min_length':40,'max_length':30}, {'type':'text','max_length':0},
    {'type':'text','max_length':'30'}, {'type':'number','minimum':10,'maximum':1},
    {'type':'number','default':'2'}, {'type':'number','whole_number':True,'default':1.2},
    {'type':'choice','choices':[]}, {'type':'radio','choices':[{'id':'a','label':'A'},{'id':'a','label':'Other'}]},
    {'type':'choice','choices':['A'],'default':'B'}, {'type':'checkbox_group','choices':['A'],'min_selections':2},
    {'type':'checkbox_group','choices':['A'],'default':['B']}, {'type':'checkbox','max_length':5},
    {'type':'checkbox','default':0}, {'type':'date','min_date':'2026-02-30'},
    {'type':'date','min_date':'2027-01-01','max_date':'2026-01-01'}, {'type':'date','default':'2026-02-30'},
    {'type':'colour','default':'red'}, {'type':'colour','choices':[{'id':'bad','label':'Bad','value':'red'}]},
    {'type':'photo','default':None}, {'type':'photo','max_files':0},
    {'type':'photo','allowed_extensions':['../../exe']}, {'type':'photo','max_file_size_bytes':-1},
    {'type':'text','required':'yes'}, {'type':'text','order':-1}, {'type':'variant'},
    {'type':'text','default':float('nan')},
])
def test_rejects_malformed_or_unsupported_constraints(patch):
    with pytest.raises(ValueError):validate({'fields':{'input':{'label':'Test',**patch}}})


def test_unknown_keys_preserved_and_no_artificial_thirty_field_cap():
    definition={'future':{'opaque':[]},'fields':{f'f_{i}':{'type':'text','label':str(i),'legacy':{'keep':True}} for i in range(40)}}
    merged=merge(definition,{'fields':{'f_1':{'label':'Renamed','order':1}}})
    assert merged['future']==definition['future'] and len(merged['fields'])==40
    assert merged['fields']['f_1']['legacy']=={'keep':True}
    assert dict(ordered_fields(merged))['f_1']['label']=='Renamed'


def test_sparse_merge_disable_exclusion_order_and_reset_do_not_mutate_shared():
    shared=copy.deepcopy(DEFINITION)
    extra={'fields':{'dog_name':{'max_length':20},'memorial_date':{'type':'date','label':'Memorial Date','order':1}}}
    result=merge(shared,extra)
    assert result['fields']['dog_name']=={**shared['fields']['dog_name'],'max_length':20}
    assert [id for id,_ in ordered_fields(result)]==['dog_name','memorial_date']
    assert merge(shared,{'fields':{'dog_name':{'enabled':False}}})['fields']['dog_name']['enabled'] is False
    assert merge(shared,{'fields':{'dog_name':{'remove':True}}})['fields']=={}
    assert merge(shared,{})==shared and shared==DEFINITION


@pytest.mark.parametrize('field,answer,valid', [
    ({'type':'text','required':True},'',False),({'type':'text','max_length':3},'long',False),
    ({'type':'text'},'',True),({'type':'number','minimum':2},1,False),
    ({'type':'checkbox','required':True},False,False),({'type':'checkbox'},False,True),
    ({'type':'date','max_date':'2026-10-01'},'2026-10-16',False),
    ({'type':'choice','choices':['A']},'A',True),({'type':'radio','choices':['A']},'B',False),
    ({'type':'checkbox_group','choices':['a','b'],'max_selections':1},['a','b'],False),
    ({'type':'colour','choices':['#ff0000']},'#ffffff',False)])
def test_preview_validation_contract(field,answer,valid):
    assert (answer_error(field,answer) is None)==valid


def test_library_pins_revisions_renames_duplicates_and_archives(core):
    with core[0].app_context():
        first=create();ref={'id':first['id'],'revision':1}
        second=templates.save({**first,'expected_revision':1,'name':'Pet keepsake','definition':{'fields':{'dog_name':{'type':'text','label':'Pet name','max_length':20}}}},first['id'])
        assert second['id']==first['id'] and second['revision']==2
        assert templates.definition(ref)==DEFINITION
        assert templates.save({**second,'expected_revision':2},first['id'])==second  # no-op
        duplicate=create('Independent copy',second['definition']);assert duplicate['id']!=first['id']
        archived=templates.save({**second,'expected_revision':2,'archived':True},first['id'])
        assert archived['revision']==3 and templates.definition(ref)==DEFINITION
        with pytest.raises(ValueError,match='archived'):templates.check_assignments({}, {'customisation':{'template':ref}})
        templates.check_assignments({'customisation':{'template':ref}},{'customisation':{'template':ref}})
        db.session.remove()
        assert templates.definition(ref)==DEFINITION and len(templates.library())==2


def test_template_collision_concurrent_revision_fence(core):
    with core[0].app_context():
        first=create()
        templates.save({**first,'name':'Rename','expected_revision':1},first['id'])
        with pytest.raises(templates.TemplateConflict):templates.save({**first,'name':'Stale','expected_revision':1},first['id'])
        assert CustomisationTemplate.query.count()==2


def test_template_precedence_explicit_null_and_no_recursive_references(core):
    with core[0].app_context():
        first=create();ref={'id':first['id'],'revision':1}
        shared={'template':ref,'fields':{'dog_name':{'max_length':25}}}
        resolved=merge(shared,{'fields':{'dog_name':{'max_length':20},'message':{'type':'multiline','label':'Message'}}})
        assert resolved['fields']['dog_name']['required'] is True and resolved['fields']['dog_name']['max_length']==20
        assert 'template' not in resolved
        assert merge({'template':ref},{'template':None})['fields']=={}
        removed=merge({'template':ref},{'fields':{'dog_name':{'remove':True}}});validate(removed);assert removed['fields']=={}
        validate({'template':ref,'fields':{'dog_name':{'remove':True}}})
        with pytest.raises(ValueError,match='standalone'):create(definition={'template':ref})


def test_template_api_admin_csrf_stale_and_size_bounds(core):
    app,client,*_=core
    app.config['WTF_CSRF_ENABLED']=True
    body={'name':'Birthday card','definition':DEFINITION}
    assert client.post('/customisations/api/templates',json=body).status_code==400
    page=client.get('/customisations/templates');assert page.status_code==200
    token=re.search(r'<meta name="csrf-token" content="([^"]+)"',page.text).group(1)
    headers={'X-CSRFToken':token}
    response=client.post('/customisations/api/templates',json=body,headers=headers);assert response.status_code==200
    first=response.json
    assert client.post('/customisations/api/templates/'+first['id'],json={**body,'expected_revision':99},headers=headers).status_code==409
    assert client.delete('/customisations/api/templates/'+first['id'],headers=headers).status_code==405
    assert client.post('/customisations/api/templates',data='x'*(4*1024*1024+1),content_type='application/json',headers=headers).status_code==413
    with app.app_context():User.query.one().is_admin=False;db.session.commit()
    assert client.get('/customisations/api/templates').status_code==403
    assert client.post('/customisations/api/templates',json=body,headers=headers).status_code==403


@pytest.mark.parametrize('name,kind', [('Simple Collection/Blue Token','override'),('Simple Collection/Blue Token','shared'),('Variable Collection/Badge One','override'),('Variable Collection/Badge One','shared'),('Single Variable','shared')])
def test_template_assignment_safe_local_save_real_projection_and_fingerprint_isolation(core,monkeypatch,name,kind):
    from app.utils.scan_runner import start_scan,get_progress
    from app.commerce import catalogue_snapshot
    monkeypatch.setattr('app.routes.start_scan',start_scan)
    monkeypatch.setattr('app.shopify_connection.ShopifyAdapter._request',lambda *a,**kw:pytest.fail('Local customisation edit called Shopify'))
    app,client,root,_,ids,*_=core
    with app.app_context():
        template=create();product=db.session.get(Product,ids[name]);before=(product.sku,[(v.id,v.sku,v.source_identity) for v in product.variations]);stamp=source_fingerprint(product,[])
    images={str(f.relative_to(root)):f.read_bytes() for f in root.rglob('*') if f.suffix in {'.png','.jpg'}}
    indexes={str(f.relative_to(root)):f.read_bytes() for f in root.rglob('sku_index.json')}
    result=save(core,name,kind=kind,data={'customisation':{'template':{'id':template['id'],'revision':1}}})
    assert result.status_code==200,result.json
    for _ in range(600):
        with app.app_context(): progress=get_progress(result.json['run_id'])
        if progress.get('status') in {'done','error'}: break
        time.sleep(.025)
    assert progress['status']=='done',progress
    with app.app_context():
        product=db.session.get(Product,ids[name]);assert (product.sku,[(v.id,v.sku,v.source_identity) for v in product.variations])==before
        assert source_fingerprint(product,[])==stamp
        assert catalogue_snapshot(product).data['customisation']['fields']['dog_name']['max_length']==30
        operation=db.session.get(CatalogueOperation,result.json['operation_id'])
        # The established Single Variable scanner logs image-path diagnostics at
        # WARN even on its normal path. Preserve that existing partial receipt.
        assert operation.status in ({'succeeded','partial'} if name=='Single Variable' else {'succeeded'})
        assert not operation.error
        assert all(i.database_state=='committed' and i.marker_state=='finalized' and not i.error for i in operation.items)
    assert images=={str(f.relative_to(root)):f.read_bytes() for f in root.rglob('*') if f.suffix in {'.png','.jpg'}}
    assert indexes=={str(f.relative_to(root)):f.read_bytes() for f in root.rglob('sku_index.json')}
    assert (root/name/'.scanned').exists() and not (root/name/'.update').exists()
    page=client.get(f'/edit_products/{ids[name]}/edit/{kind}');assert page.status_code==200
    assert 'data-customisation-designer' in page.text and 'data-custom-preview-dialog' in page.text


def test_shared_save_sparse_child_exclusion_extra_field_and_missing_template(core):
    app,_,root,_,ids,*_=core
    with app.app_context():template=create()
    # An inherited field can be excluded by one child without copying the template.
    child=root/'Simple Collection/Blue Token/product_info.json';data=json.loads(child.read_text());data['customisation']={'fields':{'dog_name':{'remove':True},'memorial_date':{'type':'date','label':'Memorial Date'}}};child.write_text(json.dumps(data))
    sku,body=payload(core,'Simple Collection/Blue Token',kind='shared',data={'customisation':{'template':{'id':template['id'],'revision':1}}})
    body['acknowledge_shared']=False
    assert core[1].post(f'/edit_products/{sku}/save',json=body).status_code==400
    body['acknowledge_shared']=True;assert core[1].post(f'/edit_products/{sku}/save',json=body).status_code==200
    with app.app_context():
        shared=json.loads((root/'Simple Collection/product_info.json').read_text())
        effective=resolved_metadata(db.session.get(Product,ids['Simple Collection/Blue Token']),shared,json.loads(child.read_text()))
        assert set(effective['customisation']['fields'])=={'memorial_date'}
        assert 'dog_name' in resolved_metadata(db.session.get(Product,ids['Simple Collection/Green Token']),shared,{})['customisation']['fields']
    assert 'template' not in json.loads(child.read_text())['customisation']
    assert save(core,data={'customisation':{'template':{'id':'missing','revision':1}}}).status_code==400


def test_local_customisation_noop_backup_conflict_and_unknown_preservation(core):
    result=save(core,data={'customisation':DEFINITION});assert result.status_code==200
    target=core[2]/'Simple Collection/Blue Token/product_info.json'
    backups=list(target.parent.glob('*.bak.*'))
    assert save(core,data={'customisation':DEFINITION}).json['unchanged']
    assert list(target.parent.glob('*.bak.*'))==backups and backups
    sku,body=payload(core,'Simple Collection/Blue Token',data={'customisation':{'fields':{'dog_name':{'label':'Renamed'}}}})
    value=json.loads(target.read_text());value['private_extension']={'keep':[]};target.write_text(json.dumps(value))
    assert core[1].post(f'/edit_products/{sku}/save',json=body).status_code==409
    assert json.loads(target.read_text())['customisation']==DEFINITION


def test_upgrade_adds_only_template_table_preserves_existing_identity_and_restart(tmp_path):
    path=tmp_path/'upgrade.db';url='sqlite:///'+str(path)
    command.upgrade(_alembic_config(url),'0014_shopify_sales')
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO shopify_product_identity(store_key,store_id,store_domain,source_identity,sku,state,verification_state,reviewed_digest,media_evidence_json,created_at,updated_at) VALUES ('shop','shop','example.myshopify.com','Fixture','TEST-0001','verified','verified','digest','{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
        before=connection.execute('SELECT * FROM shopify_product_identity').fetchall()
        tables={row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    ensure_database(url)
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT * FROM shopify_product_identity').fetchall()==before
        assert {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}-tables=={'customisation_template'}
        assert connection.execute('SELECT version_num FROM alembic_version').fetchone()[0]=='0015_customisation_templates'
        connection.execute("INSERT INTO customisation_template VALUES ('pet',1,'Pet','',?,0,CURRENT_TIMESTAMP)",(json.dumps(DEFINITION),))
    ensure_database(url)
    with sqlite3.connect(path) as connection:
        assert json.loads(connection.execute('SELECT definition_json FROM customisation_template').fetchone()[0])==DEFINITION
