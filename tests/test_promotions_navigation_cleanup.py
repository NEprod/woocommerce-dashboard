import json
import re
from app import db
from app.inventory.models import BomScenario
from test_inventory import workshop, client_for  # noqa: F401
from test_setup_flow import setup_app  # noqa: F401


def test_tools_links_use_original_promotions_and_bom_routes(workshop):
    client = client_for(workshop)
    html = client.get('/promotions/').get_data(as_text=True)
    groups = re.findall(r'<details[^>]*data-navigation-group="Tools".*?</details>',html,re.S)
    assert len(groups)==2
    for tools in groups:
        assert re.findall(r'<a\b[^>]*href="([^"]+)"',tools)==['/production/bom','/promotions/']
        assert 'BOM &amp; Viability Planner' in tools and 'Promotions &amp; Discounts' in tools
        assert ' open>' in tools
    for shopify in re.findall(r'<details[^>]*data-navigation-group="Shopify".*?</details>',html,re.S):
        assert 'href="/promotions/"' not in shopify
    assert '/production/bundles' not in html
    assert client.get('/production/bom').status_code == 200
    assert client.get('/promotions/new').status_code == 200
    assert client.get('/production/bundles').headers['Location'] == '/promotions/'
    assert client.post('/production/bundles',data={'action':'save'}).status_code == 410


def test_retired_duplicate_records_are_read_only_and_not_deleted(workshop):
    document = json.dumps(dict(kind='bundle',products=[dict(product_id=42,sku='SAVED-SKU',quantity='2')],lines=[]))
    with workshop.app_context():
        saved = BomScenario(name='Retained duplicate scenario',document=document,version=3)
        db.session.add(saved);db.session.commit(); ident=saved.id
    client=client_for(workshop)
    html=client.get(f'/production/bom/{ident}').get_data(as_text=True)
    assert 'SAVED-SKU' in html and 'Retained historical scenario' in html
    assert 'Save Local Scenario' not in html and 'bundle_product' not in html
    assert client.post(f'/production/bom/{ident}',data={'action':'save'}).status_code==410
    assert 'Retained duplicate scenario' not in client.get('/production/bom').get_data(as_text=True)
    with workshop.app_context():
        retained=db.session.get(BomScenario,ident)
        assert retained.document==document and retained.version==3
