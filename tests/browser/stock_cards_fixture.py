"""Stock-card layouts using disposable local inventory only."""
from production_bom_fixture import app, client, item, db
from app.inventory.models import Item
from io import BytesIO
from PIL import Image

item(client,'purchased','ABCDEFGHIJKLMNOPQRSTUVWX','Mechanical keyboard switch with an unusually long descriptive name ' * 2)
with app.app_context():
    output = BytesIO()
    Image.new('RGB',(100,100),'teal').save(output,format='JPEG')
    db.session.get(Item,1).image = output.getvalue()
    db.session.commit()

if __name__ == '__main__':
    app.run(host='127.0.0.1',port=7514,use_reloader=False)
