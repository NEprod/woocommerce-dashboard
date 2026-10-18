"""Type-specific local forms and non-destructive legacy envelope capacity."""
import json
from decimal import Decimal

import pytest
from html.parser import HTMLParser
from app import db, package_links, packaging_catalogue as catalogue, packing
from app.packaging_models import Package, PackagingEvidence
from test_packaging import packaging_app, logged, package, item
from test_package_manager import manager
from test_setup_flow import setup_app


class FormMarkup(HTMLParser):
    def __init__(self,text):
        super().__init__();self.stack=[];self.controls={};self.details=[];self.feed(text)

    def handle_starttag(self,tag,attributes):
        attrs=dict(attributes);hidden='hidden' in attrs or any(h for _,h in self.stack)
        if tag in {'input','select'} and attrs.get('name'): self.controls[attrs['name']]=hidden
        if tag=='details': self.details.append(attrs)
        if tag not in {'input','meta','link','img','br','hr','source','wbr'}: self.stack.append((tag,hidden))

    def handle_endtag(self,tag):
        for i in range(len(self.stack)-1,-1,-1):
            if self.stack[i][0]==tag: self.stack=self.stack[:i];break


@pytest.mark.parametrize('kind,visible,hidden',[
    ('ENVELOPE',{'external_cm.length','external_cm.width','internal_cm.length','internal_cm.width','internal_cm.height'},
        {'external_cm.height','external_diameter_cm','internal_diameter_cm'}),
    ('BOX',{'external_cm.length','external_cm.width','external_cm.height','internal_cm.height'},
        {'external_diameter_cm','internal_diameter_cm'}),
    ('TUBE',{'external_cm.length','external_diameter_cm','internal_cm.length','internal_diameter_cm'},
        {'external_cm.width','external_cm.height','internal_cm.width','internal_cm.height'}),
])
def test_type_specific_server_markup(manager,kind,visible,hidden):
    row=catalogue.save({'name':'Synthetic '+kind,'type':kind})
    page=logged(manager[0]).get(f'/packaging/{row.id}/edit')
    markup=FormMarkup(page.get_data(as_text=True))
    for key in visible:
        assert key in markup.controls and not markup.controls[key]
    for key in hidden:
        assert markup.controls[key]
    assert 'max_contents_thickness_mm' not in markup.controls
    assert all('open' not in attrs for attrs in markup.details if 'data-navigation-group' not in attrs)


@pytest.mark.parametrize('height,unit',[('.3','cm'),('3','mm')])
def test_legacy_transfer_is_explicit_unit_correct_and_not_automatic(manager,height,unit):
    row=Package.query.filter_by(seed_key='c5-plus').one()
    before=(row.id,row.version,row.specification)
    assert catalogue.envelope_height(catalogue.view(row))[0] is None
    logged(manager[0]).get(f'/packaging/{row.id}/edit')
    assert (row.id,row.version,row.specification)==before
    assert json.loads(row.specification)['internal_cm']['height'] is None
    catalogue.save({'name':row.name,'internal_cm':{'height':height},'dimension_unit':unit,
        'confirm_envelope_thickness_change':True},row,version=row.version)
    assert json.loads(row.specification)['internal_cm']['height']=='0.3'
    assert json.loads(row.specification)['max_contents_thickness_mm'] is None
    assert json.loads(PackagingEvidence.query.filter(PackagingEvidence.key.like('package-thickness-history:%')).one().value)['max_contents_thickness_mm']=='3'


@pytest.mark.parametrize('inner',['.2','.5'])
def test_conflicting_values_are_preserved_without_automatic_winner(manager,inner):
    row=Package.query.filter_by(seed_key='c5-plus').one()
    raw=json.loads(row.specification);raw['internal_cm']['height']=inner
    row.specification=json.dumps(raw);db.session.commit()
    before=row.specification
    height,warning=catalogue.envelope_height(catalogue.view(row))
    assert height is None and 'Conflict' in warning
    assert not packing.recommend(item(dims=('15','15','.1')),[catalogue.view(row)])['recommendation']
    assert catalogue.view(row)['readiness']=='Review historical thickness'
    assert row.specification==before


@pytest.mark.parametrize('height',['.4',None])
def test_replacing_or_clearing_legacy_limit_needs_confirmation_and_retains_history(manager,height):
    app,_=manager;row=Package.query.filter_by(seed_key='c5-plus').one()
    raw=json.loads(row.specification);raw['internal_cm']['height']='.2'
    row.specification=json.dumps(raw);db.session.commit()
    ident=row.id;gid='gid://shopify/ShippingPackageV2/116237992319'
    package_links.set_id(row,gid,[])
    client=logged(app);before=row.specification
    value={'name':row.name,'version':row.version,'internal_cm':{'height':height}}
    assert client.post(f'/packaging/{ident}/edit',json=value).status_code==400
    assert row.specification==before
    value['confirm_envelope_thickness_change']=True
    assert client.post(f'/packaging/{ident}/edit',json=value).status_code==200
    saved=json.loads(row.specification)
    assert saved['max_contents_thickness_mm'] is None and saved['external_cm']['height'] is None
    assert saved['internal_cm']['height']==('0.4' if height else None)
    history=PackagingEvidence.query.filter(PackagingEvidence.key.like('package-thickness-history:%')).one()
    assert json.loads(history.value)['max_contents_thickness_mm']=='3'
    assert package_links.current(ident)['id']==gid


def test_type_changes_preserve_hidden_measurements_and_shopify_id(manager):
    row=catalogue.save({'name':'Synthetic type changes','type':'BOX',
        'external_cm':{'length':'24','width':'18','height':'3'},
        'internal_cm':{'length':'23','width':'17','height':'2'},
        'external_diameter_cm':'5','internal_diameter_cm':'4'})
    original=json.loads(row.specification);ident=row.id
    gid='gid://shopify/ShippingPackageV2/116237992319';package_links.set_id(row,gid,[])
    for kind in ('TUBE','ENVELOPE','BOX'):
        catalogue.save({'name':row.name,'type':kind},row,version=row.version)
        saved=json.loads(row.specification)
        assert all(saved[k]==v for k,v in original.items() if k!='type')
        assert row.id==ident and package_links.current(ident)['id']==gid


def test_no_automatic_envelope_defaults_from_name_or_seed():
    for name in ('Envelope','TLC C5+ Board Back Envelope','5 mm envelope'):
        value=catalogue.clean({'name':name,'type':'ENVELOPE'})
        assert value['internal_cm']['height'] is None and value['max_contents_thickness_mm'] is None
    seeded=next(json.loads(row['specification']) for row in catalogue.initial_rows() if row['seed_key']=='c5-plus')
    assert seeded['internal_cm']['height'] is None and seeded['max_contents_thickness_mm'] is None
    assert packing.recommend(item(dims=('15','15','1')),[package(max_contents_thickness_mm='3')])['recommendation']


def test_historical_rule_alone_is_not_active_and_ordinary_save_preserves_it(manager):
    row=Package.query.filter_by(seed_key='c5-plus').one();spec=catalogue.view(row)
    assert not packing.recommend(item(dims=('15','15','.1')),[spec])['recommendation']
    catalogue.save({'name':row.name,'internal_cm':{'height':None}},row,version=row.version)
    saved=json.loads(row.specification)
    assert saved['internal_cm']['height'] is None and saved['max_contents_thickness_mm']=='3'
    catalogue.save({'name':row.name,'max_contents_thickness_mm':'5'},row,version=row.version)
    assert json.loads(row.specification)['max_contents_thickness_mm']=='3'  # no silent rewrite of historical evidence


@pytest.mark.parametrize('height,eligible',[('.2',True),('.3',True),('.31',False),(None,False)])
def test_envelope_internal_height_is_contents_capacity_without_clearance(height,eligible):
    p=package(type='ENVELOPE',internal_cm={'height':'.3'},external_cm={'length':'24','width':'16.5'},clearance_cm='99')
    result=packing.recommend(item(dims=('24','16.5',height),orientation='fixed',clearance_cm='99'),[p])
    assert bool(result['recommendation']) is eligible
    if eligible: assert result['state']=='estimated'
