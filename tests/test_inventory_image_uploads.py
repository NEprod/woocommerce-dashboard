from io import BytesIO
import json
from pathlib import Path

import pytest
from PIL import Image, PngImagePlugin
from werkzeug.datastructures import FileStorage
from sqlalchemy.exc import OperationalError

from app import create_app, db
from app.inventory.images import optimise, MAX_SOURCE_BYTES, HEIF_SUPPORTED
from app.inventory.models import Item, Identity, Movement, Transaction, BomScenario, CostingSettings, Equipment
from test_inventory import workshop, client_for, item, transact, location  # noqa: F401
from test_setup_flow import setup_app  # noqa: F401


def encoded(image, format='JPEG', **options):
    output=BytesIO();image.save(output,format,**options);return output.getvalue()


def upload(data, filename='photo.jpg'):
    return FileStorage(stream=BytesIO(data),filename=filename)


@pytest.fixture(scope='module')
def large_photos():
    noise=Image.effect_noise((3200,2400),30)
    gradient=Image.linear_gradient('L').resize(noise.size)
    photo=Image.blend(gradient,noise,.35).convert('RGB')
    jpeg=encoded(photo,quality=100)
    png=encoded(noise.convert('RGB'),'PNG')
    assert len(jpeg)>2*1024*1024 and len(png)>2*1024*1024
    return jpeg,png


@pytest.mark.parametrize('index',[0,1])
def test_large_sources_optimised_and_smaller(large_photos,index):
    result=optimise(upload(large_photos[index]))
    with Image.open(BytesIO(result)) as image:
        assert image.format=='WEBP' and image.size==(1600,1200)
        assert not image.getexif() and 'exif' not in image.info
    assert len(result)<len(large_photos[index])
    print(f'fixture {index}: {len(large_photos[index])} source bytes → {len(result)} WebP bytes (1600 × 1200)')


def test_orientation_transparency_small_images_and_metadata():
    exif=Image.Exif();exif[274]=6;exif[315]='Private GPS / camera metadata'
    from PIL.TiffImagePlugin import IFDRational
    exif[34853]={1:'N',2:(IFDRational(1),IFDRational(2),IFDRational(3))}
    result=optimise(upload(encoded(Image.new('RGB',(120,60),'red'),exif=exif)))
    with Image.open(BytesIO(result)) as image:
        assert image.size==(60,120) and not image.getexif()
    metadata=PngImagePlugin.PngInfo();metadata.add_text('GPS','private coordinates')
    source=Image.new('RGBA',(150,100),(12,40,80,100))
    result=optimise(upload(encoded(source,'PNG',pnginfo=metadata),'not-the-extension.exe'))
    with Image.open(BytesIO(result)) as image:
        assert image.size==(150,100) and image.mode=='RGBA'
        assert image.getpixel((20,20))[3]==100
        assert 'GPS' not in image.info and 'exif' not in image.info
    webp=optimise(upload(encoded(source,'WEBP')))
    assert webp[:4]==b'RIFF'


def test_source_bounds_formats_and_decode_limits(monkeypatch):
    with pytest.raises(ValueError,match='25 MB'):
        optimise(upload(b'x'*(MAX_SOURCE_BYTES+1)))
    for content in (b'<!DOCTYPE html><script>alert(1)</script>',b'\x89PNG\r\n\x1a\ninvalid'):
        with pytest.raises(ValueError):optimise(upload(content,'photo.png'))
    with pytest.raises(ValueError,match='Unsupported'):
        optimise(upload(encoded(Image.new('RGB',(12,12)),'GIF')))
    animation=BytesIO()
    Image.new('RGB',(12,12),'red').save(animation,'WEBP',save_all=True,append_images=[Image.new('RGB',(12,12),'blue')],duration=100)
    with pytest.raises(ValueError,match='single still'):
        optimise(upload(animation.getvalue(),'animated.webp'))
    if not HEIF_SUPPORTED:
        with pytest.raises(ValueError,match='HEIC/HEIF decoding is unavailable'):
            optimise(upload(b'\0\0\0\x18ftypheic'+b'0'*100,'phone.heic'))
    from app.inventory import images
    monkeypatch.setattr(images,'MAX_PIXELS',100)
    with pytest.raises(ValueError,match='megapixel'):
        optimise(upload(encoded(Image.new('RGB',(12,12)))))


def edit_form(version=1, **extra):
    return dict(name='PLA saved',active='yes',version=str(version),unit='g',pack_qty='1000',pack_unit='g',pack_price='13.99',
                category='Filament',filament_type='PLA',filament_colour='Red',spool_grams='1000',**extra)


def test_large_upload_replacement_legacy_and_atomic_failure(workshop,large_photos,monkeypatch):
    import requests
    monkeypatch.setattr(requests.sessions.Session,'request',lambda *a,**k:pytest.fail('No external requests'))
    client=client_for(workshop)
    iid=item(client,'material','PHOTO-RAW','PLA saved',unit='g')
    legacy=encoded(Image.new('RGB',(40,20),'green'))
    with workshop.app_context():
        db.session.get(Item,iid).image=legacy;db.session.commit()
        counts=[m.query.count() for m in (Identity,Item,Movement,Transaction)]
    assert client.get(f'/production/item/{iid}/image.jpg').mimetype=='image/jpeg'
    response=client.post(f'/production/item/{iid}/edit',data=edit_form(image=(BytesIO(large_photos[0]),'../../spool.jpg')))
    assert response.status_code==303
    current=client.get(f'/production/item/{iid}/image.jpg')
    assert current.mimetype=='image/webp' and current.headers['X-Content-Type-Options']=='nosniff'
    assert client.get(f'/production/item/{iid}/stock-card').status_code==200
    failed=client.post(f'/production/item/{iid}/edit',data=edit_form(2,image=(BytesIO(b'corrupt'),'image.jpg')))
    assert failed.status_code==400 and client.get(f'/production/item/{iid}/image.jpg').data==current.data
    oversized=client.post(f'/production/item/{iid}/edit',data=edit_form(2,image=(BytesIO(b'x'*(MAX_SOURCE_BYTES+1)),'big.jpg')))
    assert oversized.status_code==400 and b'25 MB' in oversized.data
    too_large=client.post(f'/production/item/{iid}/edit',data=edit_form(2,image=(BytesIO(b'x'*(27*1024*1024)),'huge.jpg')))
    assert too_large.status_code==413 and b'25 MB' in too_large.data
    original_commit=db.session.commit
    monkeypatch.setattr(db.session,'commit',lambda:(_ for _ in ()).throw(OperationalError('write',{},Exception('disk full'))))
    failed=client.post(f'/production/item/{iid}/edit',data=edit_form(2,image=(BytesIO(encoded(Image.new('RGB',(30,20),'blue'))),'replacement.png')))
    assert failed.status_code==503 and b'previous saved record' in failed.data
    monkeypatch.setattr(db.session,'commit',original_commit)
    assert client.get(f'/production/item/{iid}/image.jpg').data==current.data
    with workshop.app_context():
        saved=db.session.get(Item,iid)
        assert saved.identity.code=='PHOTO-RAW' and saved.version==2
        assert saved.pack_price==13_990_000
        assert [m.query.count() for m in (Identity,Item,Movement,Transaction)]==counts
    assert client_for(workshop,3).post('/production/item/purchase-cost-preview',json={}).status_code==403
    assert client.get('/production/item/../../site.db/image.jpg').status_code==404


@pytest.mark.parametrize('quantity,purchase,price,unit,expected',[
    ('1000','g','13.99','g','.01399'),('1','kg','13.99','g','.01399'),
    ('10','sheet','25','sheet','2.5'),('100','piece','12','piece','.12'),
    ('10','piece','','piece',None),('10','piece','0','piece','0'),
])
def test_purchase_preview_shared_costs(workshop,quantity,purchase,price,unit,expected):
    from decimal import Decimal
    client=client_for(workshop)
    response=client.post('/production/item/purchase-cost-preview',json=dict(pack_qty=quantity,pack_unit=purchase,pack_price=price,unit=unit))
    assert response.status_code==200
    assert response.json['unit_cost'] is None if expected is None else Decimal(response.json['unit_cost'])==Decimal(expected)
    for bad in [dict(pack_qty='0'),dict(pack_qty='-1'),dict(pack_unit='ml'),dict(pack_price='-1')]:
        assert client.post('/production/item/purchase-cost-preview',json=dict(pack_qty='10',pack_unit='g',pack_price='5',unit='g')|bad).status_code==400


def test_images_scenarios_settings_and_history_survive_new_app_instance(workshop,setup_app):
    client=client_for(workshop)
    iid=item(client,'material','PERSIST-RAW','Persistent material',unit='g',pack_qty='1000',pack_price='13.99')
    tub=location(client,'PERSIST-TUB','Persistence test tub')
    transact(client,'opening',iid,qty='1000',cost='13.99',destination=str(tub),reason='isolated fixture')
    image=optimise(upload(encoded(Image.new('RGB',(90,60),'yellow'))))
    with workshop.app_context():
        db.session.get(Item,iid).image=image
        db.session.add(BomScenario(name='Persistent scenario',document=json.dumps(dict(lines=[],equipment=[])),version=1))
        db.session.add(CostingSettings(id=1,document=json.dumps(dict(tariff='.24',fee_profiles=[dict(id='test',percent='2',fixed='.25')])),version=1))
        db.session.commit()
        before=[m.query.count() for m in (Item,Movement,Transaction,BomScenario,Equipment)]
        database=Path(db.engine.url.database).resolve()
        assert database==Path(workshop.instance_path)/'site.db'
        db.session.remove();db.engine.dispose()
    fresh=create_app(recover=False);fresh.config.update(TESTING=True,WTF_CSRF_ENABLED=False)
    with fresh.app_context():
        assert db.session.get(Item,iid).image==image
        assert [m.query.count() for m in (Item,Movement,Transaction,BomScenario,Equipment)]==before
        assert json.loads(db.session.get(CostingSettings,1).document)['tariff']=='.24'
        assert db.session.get(Item,iid).identity.code=='PERSIST-RAW'
        db.session.remove();db.engine.dispose()


def test_request_temporary_streams_closed_after_success_and_failure(workshop,large_photos,monkeypatch):
    import werkzeug.formparser as parser
    original=parser.SpooledTemporaryFile
    streams=[]
    def tracked(*args,**kwargs):
        stream=original(*args,**kwargs);streams.append(stream);return stream
    monkeypatch.setattr(parser,'SpooledTemporaryFile',tracked)
    client=client_for(workshop)
    success=client.post('/production/item/new/material',data=dict(name='Spool stream',code='STREAM-TEST',active='yes',unit='g',image=(BytesIO(large_photos[0]),'large.jpg')))
    assert success.status_code==303
    assert streams and all(stream.closed for stream in streams)
    failed=client.post('/production/item/1/edit',data=edit_form(image=(BytesIO(b'not an image'),'broken.png')))
    assert failed.status_code==400
    assert all(stream.closed for stream in streams)
