"""UI corrections retain the existing signed save/schema/projection contract."""
import json
import pytest
from app import db
from app.models import Product, CustomisationTemplate
from app.metadata_workspace import resolved_metadata
from app.customisations import validate
from app.shopify_products import source_fingerprint
from app import customisation_templates
from test_metadata_editor_core import core, save

NAME={'type':'text','label':'Name','required':True,'enabled':True,'max_length':30,'help_text':'Check spelling','future':{'keep':[]}}


@pytest.mark.parametrize('enabled,required',[(True,True),(True,False),(False,False),(False,True)])
def test_boolean_controls_preserve_each_authored_combination(core,enabled,required):
    field={**NAME,'enabled':enabled,'required':required}
    response=save(core,data={'customisation':{'fields':{'name_on_card':field}}})
    assert response.status_code==200,response.json
    path=core[2]/'Simple Collection/Blue Token/product_info.json'
    assert json.loads(path.read_text())['customisation']['fields']['name_on_card']==field
    validate({'fields':{'name_on_card':field}})


@pytest.mark.parametrize('default,field',[(False,{'type':'checkbox'}),([],{'type':'checkbox_group','choices':[{'id':'a','label':'A'}]}),('',{'type':'text'}),(4,{'type':'number','whole_number':True}),('2026-10-16',{'type':'date'}),('#ff0000',{'type':'colour'}),('a',{'type':'radio','choices':[{'id':'a','label':'A'}]})])
def test_typed_ui_defaults_persist_without_coercion_or_empty_pruning(core,default,field):
    definition={'fields':{'input':{'label':'Input',**field,'default':default}}}
    response=save(core,data={'customisation':definition})
    assert response.status_code==200,response.json
    assert response.json['authored']['customisation']==definition
    assert save(core,data={'customisation':definition}).json['unchanged']


@pytest.mark.parametrize('source,kind',[('Simple Collection/Blue Token','override'),('Variable Collection/Badge One','override'),('Single Variable','shared')])
def test_sparse_edit_with_pinned_template_changes_only_intended_property(core,source,kind):
    app,client,root,_,ids,*_=core
    owner=root/(source if kind=='shared' else source.rsplit('/',1)[0])/'product_info.json'
    content=json.loads(owner.read_text())
    with app.app_context():
        template=customisation_templates.save({'name':'Fictional Card','definition':{'fields':{'name_on_card':NAME}}})
    content['customisation']={'template':{'id':template['id'],'revision':1}}
    owner.write_text(json.dumps(content));before=owner.read_bytes()
    with app.app_context():fingerprint=source_fingerprint(db.session.get(Product,ids[source]),[])
    result=save(core,source,kind=kind,data={'customisation':{'fields':{'name_on_card':{'max_length':20}}}})
    assert result.status_code==400,result.json
    assert 'type' in json.dumps(result.json) and 'required' in json.dumps(result.json)
    assert owner.read_bytes()==before
    with app.app_context():
        product=db.session.get(Product,ids[source])
        assert source_fingerprint(product,[])==fingerprint
        assert CustomisationTemplate.query.one().definition_json==json.dumps({'fields':{'name_on_card':NAME}},ensure_ascii=False,sort_keys=True)


def test_label_edit_keeps_field_option_ids_opaque_metadata_and_template_revision(core):
    field={'type':'choice','label':'Style','choices':[{'id':'script','label':'Script','future':{'keep':True}},{'id':'block','label':'Block'}],'default':'script'}
    assert save(core,data={'customisation':{'fields':{'name_on_card':NAME,'style':field}}}).status_code==200
    result=save(core,data={'customisation':{'fields':{'name_on_card':{'label':'Name to appear on your card'},'style':{'choices':[field['choices'][1],{**field['choices'][0],'label':'Decorative script'}]}}}})
    assert result.status_code==200,result.json
    expected={**NAME,'label':'Name to appear on your card'}
    assert result.json['authored']['customisation']['fields']['name_on_card']==expected
    assert result.json['authored']['customisation']['fields']['style']['default']=='script'


@pytest.mark.parametrize('patch',[{'max_length':0},{'min_length':40},{'required':'yes'},{'enabled':'no'}])
def test_server_still_rejects_malformed_control_values_before_source_write(core,patch):
    assert save(core,data={'customisation':{'fields':{'name_on_card':NAME}}}).status_code==200
    target=core[2]/'Simple Collection/Blue Token/product_info.json';before=target.read_bytes()
    result=save(core,data={'customisation':{'fields':{'name_on_card':patch}}})
    assert result.status_code==400 and target.read_bytes()==before
