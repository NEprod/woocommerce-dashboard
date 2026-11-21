"""Stock-card layouts using disposable local inventory only."""
from production_bom_fixture import app, client, item, db
from app.inventory.models import Item
from io import BytesIO
from PIL import Image

item(client,'purchased','ABCDEFGHIJKLMNOPQRSTUVWX','Mechanical keyboard switch with an unusually long descriptive name ' * 2)
switch=item(client,'purchased','KAI-BOX-WHITE','Kailh Box White V2',supplier='Fictional Mechboards',minimum='25')
with app.app_context():
    output = BytesIO()
    Image.new('RGB',(100,100),'teal').save(output,format='JPEG')
    db.session.get(Item,1).image = output.getvalue()
    raw=db.session.get(Item,1)
    raw.name='PLA Matte Charcoal with a deliberately long material name for portrait layout'
    raw.filament_type,raw.filament_colour,raw.supplier='PLA','Charcoal','Fictional Supplier'
    raw.default_location_id=3
    output=BytesIO();Image.new('RGB',(240,120),'#c7d8d1').save(output,format='JPEG')
    db.session.get(Item,switch).image=output.getvalue()
    db.session.commit()

if __name__ == '__main__':
    app.run(host='127.0.0.1',port=7514,use_reloader=False)
