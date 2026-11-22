"""Run twice in disposable containers against the SAME temporary /app/instance bind.

No image build, real appdata, credentials, network or Shopify access is needed.
The current app source and this script are mounted read-only for this local check.
"""
from io import BytesIO
from pathlib import Path
import json
import sys

from PIL import Image
from werkzeug.datastructures import FileStorage
import requests
requests.sessions.Session.request=lambda *a,**k:(_ for _ in ()).throw(AssertionError('External HTTP forbidden'))

from app import create_app, db
from app.inventory import service as stock
from app.inventory.images import optimise, stored_mime
from app.inventory.models import Item, Identity, BomScenario, CostingSettings, Equipment, Movement, Transaction

app=create_app(recover=False)
with app.app_context():
    assert Path(db.engine.url.database).resolve()==Path('/app/instance/site.db')
    if sys.argv[1]=='seed':
        photo=BytesIO();Image.new('RGB',(80,40),'green').save(photo,'PNG')
        image=optimise(FileStorage(stream=BytesIO(photo.getvalue()),filename='../../not-a-storage-path.png'))
        stock.writer_lock()
        item=stock.save_item('material',dict(name='Container-persistent PLA',code='PERSIST-PLA',active='yes',unit='g',pack_qty='1000',pack_unit='g',pack_price='13.99'),image=image)
        location=stock.save_location(dict(name='Fixture tub',code='FIXTURE-TUB',active='yes'))
        db.session.commit()
        data=dict(item_id=str(item.id),destination=str(location.id),qty='1000',cost='13.99',reason='Isolated persistence fixture')
        stock.post('opening',data,'a'*32,stock.digest(stock.plan('opening',data)),actor='fixture')
        db.session.add(BomScenario(name='Container scenario',document=json.dumps(dict(lines=[],equipment=[])),version=1))
        db.session.add(CostingSettings(id=1,document=json.dumps(dict(tariff='.24226',fee_profiles=[dict(id='fixture',percent='2',fixed='.25')])),version=1))
        Equipment.query.first().watts='321'
        db.session.commit()
    elif sys.argv[1]!='verify':
        raise AssertionError('Choose seed or verify')
    row=Item.query.one()
    assert row.identity.code=='PERSIST-PLA' and stored_mime(row.image)=='image/webp'
    assert Image.open(BytesIO(row.image)).size==(80,40)
    assert stock.balance(row.id)==1000*stock.SCALE
    assert Transaction.query.count()==Movement.query.count()==1
    assert BomScenario.query.one().name=='Container scenario'
    assert json.loads(db.session.get(CostingSettings,1).document)['fee_profiles'][0]['fixed']=='.25'
    assert Equipment.query.first().watts=='321'
    print('PASS:',sys.argv[1],'- image, barcode, inventory ledger, scenario, equipment and fee settings in persistent appdata')
    db.session.remove();db.engine.dispose()
