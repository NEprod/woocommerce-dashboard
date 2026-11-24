"""The Components listing reuses collection cards without changing inventory."""
from io import BytesIO

from PIL import Image

from app import db
from app.inventory.models import Item, Movement
from test_inventory import workshop, client_for, item
from test_setup_flow import setup_app


def test_component_cards_images_links_filters_and_other_listings(workshop):
    client = client_for(workshop)
    photo = BytesIO()
    Image.new('RGB', (120, 60), 'green').save(photo, 'PNG')
    photo.seek(0)
    pictured = item(client, 'purchased', 'BOX', 'Product box', category='Packaging', image=(photo, 'box.png'))
    raw = item(client, 'material', 'PLA', 'PLA spool', unit='g')
    plain = item(client, 'manufactured', 'EYES', 'Gingerbread - Eyes', yield_qty='10',
                 material_id=str(raw), material_qty='10', material_unit='g')
    with workshop.app_context():
        db.session.get(Item, plain).active = False
        db.session.commit()
    html = client.get('/production/items/components?state=all').text
    assert html.count('data-component-card') == 2
    assert 'collections-grid' in html and 'collection-card-media' in html
    assert f'/production/item/{pictured}/image.jpg' in html
    assert 'Inventory photo for Product box' in html
    assert 'collection-image-fallback' in html
    assert 'Packaging' in html and 'Low stock' in html and 'Archived' in html
    for record_id in (pictured, plain):
        assert f'href="/production/item/{record_id}"' in html
        assert f'href="/production/item/{record_id}/edit"' in html
    assert 'View Component' in html and 'Stock Card' in html
    assert client.get(f'/production/item/{pictured}/image.jpg').status_code == 200
    assert client.get('/production/items/components?kind=purchased').text.count('data-component-card') == 1
    assert client.get('/production/items/components?state=all&q=EYES').text.count('data-component-card') == 1
    assert 'No components match this view.' in client.get('/production/items/components?q=absent').text
    materials = client.get('/production/items/materials').text
    assert materials.count('data-material-card') == 1
    assert 'View Material' in materials and f'href="/production/item/{raw}"' in materials
    assert 'PLA spool' in materials and 'collection-image-fallback' in materials
    assert 'data-component-card' not in materials
    other = client.get('/production/items/catalogue').text
    assert 'data-component-card' not in other and 'workshop-list-row' in other
    with workshop.app_context():
        assert Item.query.count() == 3 and Movement.query.count() == 0
