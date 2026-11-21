import json
from decimal import Decimal

import pytest

from app import db
from app.inventory.models import BomScenario, CostingSettings, Equipment, Movement
from app.inventory.profitability import process_cost
from test_inventory import workshop, client_for  # noqa: F401
from test_setup_flow import setup_app  # noqa: F401


@pytest.mark.parametrize('extra,expected', [
    ({}, '250'),
    (dict(timing_mode='item', usable_units='0', allocation='share', share_percent='999'), '250'),
    (dict(timing_mode='batch', usable_units='5', allocation='identical'), '50'),
    (dict(timing_mode='batch', usable_units='5', allocation='explicit_equal'), '50'),
    (dict(timing_mode='batch', usable_units='2', allocation='share', share_percent='40'), '50'),
])
def test_shared_process_allocation(workshop, extra, expected):
    with workshop.app_context():
        equipment = Equipment.query.filter_by(name='Bambu Lab X2D', profile='PLA provisional').one()
        result = process_cost(dict(equipment_id=equipment.id, minutes='250', **extra), dict(tariff='.24'))
        assert result['total_minutes'] == 250
        assert result['attributable_minutes'] == Decimal(expected)
        assert result['electricity'].quantize(Decimal('.000001')) == Decimal(expected) / 1000
        assert result['provisional']


def test_preview_validation_unknowns_and_no_mutations(workshop, monkeypatch):
    import requests
    monkeypatch.setattr(requests.sessions.Session, 'request', lambda *a, **k: pytest.fail('No external calls'))
    client = client_for(workshop)
    with workshop.app_context():
        equipment = Equipment.query.filter_by(name='Bambu Lab X2D', profile='PLA provisional').one()
        eid = equipment.id
        db.session.add(CostingSettings(id=1, document=json.dumps(dict(tariff='.24')), version=1))
        db.session.commit()
    fields = dict(equipment_id=eid, runtime_hours='4', runtime_minutes='10', timing_mode='item')
    preview = lambda **changes: client.post('/production/bom/process-preview', json=fields | changes)
    result = preview()
    assert result.status_code == 200
    assert result.json['minutes'] == '250'
    assert result.json['electricity'] == '0.25'
    for invalid in [dict(runtime_hours='-1'), dict(runtime_hours='1.5'), dict(runtime_minutes='60'),
                    dict(runtime_minutes='-1'), dict(runtime_minutes=''), dict(equipment_id=''),
                    dict(timing_mode='batch', usable_units='0', allocation='identical'),
                    dict(timing_mode='batch', usable_units='1.5', allocation='identical'),
                    dict(timing_mode='batch', usable_units='2', allocation=''),
                    dict(timing_mode='batch', usable_units='2', allocation='share', share_percent='101')]:
        assert preview(**invalid).status_code == 400
    assert client.post('/production/bom/process-preview', json=[]).status_code == 400
    with workshop.app_context():
        equipment = db.session.get(Equipment, eid)
        equipment.watts = None
        db.session.commit()
    assert preview().json['electricity'] is None
    assert 'unknown' in preview().json['missing'][0]
    with workshop.app_context():
        equipment = db.session.get(Equipment, eid)
        equipment.watts, equipment.source = '250', 'measured'
        settings = db.session.get(CostingSettings, 1)
        settings.document = json.dumps(dict(tariff=None))
        db.session.commit()
    assert preview().json['electricity'] is None
    assert 'tariff' in preview().json['missing'][0]
    with workshop.app_context():
        db.session.get(CostingSettings, 1).document = json.dumps(dict(tariff='0'))
        db.session.commit()
    assert preview().json['electricity'] == '0.00'
    assert preview().json['provisional'] == []
    with workshop.app_context():
        assert Movement.query.count() == 0
        assert BomScenario.query.count() == 0


def test_legacy_minutes_save_reopen_and_snapshot_preservation(workshop):
    client = client_for(workshop)
    with workshop.app_context():
        eid = Equipment.query.first().id
        document = dict(lines=[], equipment=[dict(equipment_id=eid, minutes='250')])
        scenario = BomScenario(name='Legacy runtime', document=json.dumps(document), version=1)
        db.session.add(scenario)
        db.session.commit()
        sid = scenario.id
    url = f'/production/bom/{sid}'
    response = client.get(url)
    assert response.status_code == 200
    assert b'value="250"' in response.data
    assert b'One finished item' in response.data and b'Whole batch / print plate' in response.data
    assert b'data-process-preview=' in response.data
    form = dict(name='Legacy runtime', version='1', action='save', equipment_id=str(eid), minutes='250',
                timing_mode='batch', usable_units='5', allocation='identical', share_percent='100')
    assert client.post(url, data=form).status_code == 303
    with workshop.app_context():
        saved = json.loads(db.session.get(BomScenario, sid).document)
        assert saved['equipment'][0]['minutes'] == '250'
        assert saved['equipment'][0]['allocation'] == 'identical'
        snapshot = saved['_snapshot']
        db.session.add(CostingSettings(id=1, document=json.dumps(dict(tariff='999')), version=1))
        db.session.commit()
    assert b'Saved calculation snapshot' in client.get(url).data
    client.post('/production/bom/process-preview', json=dict(equipment_id=eid, runtime_hours='4', runtime_minutes='10'))
    with workshop.app_context():
        assert json.loads(db.session.get(BomScenario, sid).document)['_snapshot'] == snapshot
        assert Movement.query.count() == 0
