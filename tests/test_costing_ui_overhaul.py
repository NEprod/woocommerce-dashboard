import json
import pytest
from app import db
from app.inventory.models import Equipment, CostingSettings, BomScenario, Movement
from app.inventory.profitability import estimate
from test_inventory import workshop, client_for, item  # noqa: F401
from test_setup_flow import setup_app  # noqa: F401


def test_seven_unit_batch_and_partial_failure_allocation(workshop):
    with workshop.app_context():
        equipment=Equipment.query.filter_by(name='Bambu Lab X2D',profile='PLA provisional').one()
        document=dict(lines=[dict(source='manual',name='Test PLA',quantity='1',unit='g',unit_cost='.02')],
                      equipment=[dict(equipment_id=equipment.id,minutes='420',timing_mode='batch',usable_units='7',allocation='identical')],selling_price='5')
        config=dict(tariff='.40',fee_percent='2',fee_fixed='.25')
        result=estimate(document,config,direct_materials=True)
        assert result['electricity']=='0.10' and result['state']=='provisional'
        document['equipment'][0]['usable_units']='5'
        assert estimate(document,config,direct_materials=True)['electricity']=='0.14'
        document['equipment'][0]['allocation']=''
        with pytest.raises(ValueError,match='explicit batch allocation'):
            estimate(document,config,direct_materials=True)
        assert Movement.query.count()==0


def test_fee_edit_keeps_identity_and_scenario_snapshot_survives_defaults_change(workshop):
    client=client_for(workshop)
    assert client.post('/production/costing/settings',data=dict(action='fee_profile',name='Online',percent='2',fixed='.25')).status_code==303
    with workshop.app_context():
        settings=db.session.get(CostingSettings,1);profile=json.loads(settings.document)['fee_profiles'][0];version=settings.version
    assert client.post('/production/costing/settings',data=dict(action='fee_profile',profile_id=profile['id'],settings_version=version,name='Online updated',percent='3',fixed='.30')).status_code==303
    form=dict(name='Hypothetical decoration',manual_name='Filament estimate',manual_quantity='50',manual_unit='g',manual_cost='.02',selling_price='5',fee_profile=profile['id'],action='save')
    reply=client.post('/production/bom',data=form);assert reply.status_code==303
    with workshop.app_context():
        scenario=BomScenario.query.one();snapshot=json.loads(scenario.document)['_snapshot'];sid=scenario.id
        settings=db.session.get(CostingSettings,1);config=json.loads(settings.document);config['overhead']='100';settings.document=json.dumps(config);db.session.commit()
    html=client.get(reply.headers['Location']).get_data(as_text=True)
    assert 'Saved calculation snapshot' in html
    with workshop.app_context():
        assert json.loads(db.session.get(BomScenario,sid).document)['_snapshot']==snapshot
    assert client.post(f'/production/bom/{sid}/archive',data=dict(version='1',confirmed='yes')).status_code==303
    with workshop.app_context():
        assert json.loads(db.session.get(BomScenario,sid).document)['_archived']
        assert Movement.query.count()==0


def test_settings_render_accessible_modal_tables(workshop):
    html=client_for(workshop).get('/production/costing/settings').get_data(as_text=True)
    assert 'data-costing-dialog="equipment-modal"' in html
    assert '<dialog class="packaging-dialog"' in html
    assert 'aria-labelledby="equipment-title"' in html
    assert 'Machine / equipment' in html and 'Operating profile' in html
    assert 'data-costing-dialog="fee-modal"' in html
