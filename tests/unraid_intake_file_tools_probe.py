"""Disposable real-mount acceptance. Never scans/registers/imports/publishes.

Run ONLY with a new TLC-INTAKE-AUDIT-* child of the supplied Intake root.
Overlay is a task-owned temporary copy of candidate modules/templates, not an
installation into /app. Database and settings are isolated under /tmp.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch


def main(intake_root, name, overlay):
    root = Path(intake_root).resolve(strict=True)
    assert name.startswith('TLC-INTAKE-AUDIT-') and '/' not in name and '\\' not in name
    fixture = root / name
    fixture.mkdir(exist_ok=False)
    (fixture / 'audit-owner.txt').write_text('Disposable TLC Intake acceptance fixture: ' + name)
    scratch = Path(tempfile.mkdtemp(prefix='tlc-intake-probe-'))
    for key in list(os.environ):
        if key.startswith(('SHOPIFY_', 'WOOCOMMERCE_', 'DISCORD_')) or key in {'PRODUCT_FOLDER','OUTPUT_FOLDER','TAXONOMY_ROOT'}:
            os.environ.pop(key)
    os.environ.update(SECRET_KEY='intake-disposable-probe-0000000000000000000', DISCORD_ENABLED='false',
                      INTAKE_ROOT=str(root), TAXONOMY_ROOT=str(scratch/'taxonomy'))
    import requests
    requests.sessions.Session.request = lambda *a, **k: (_ for _ in ()).throw(AssertionError('Network denied in disposable probe'))
    import app as package
    package.__path__.insert(0, str(Path(overlay)/'app'))
    from config import Config
    Config.SQLALCHEMY_DATABASE_URI = 'sqlite:///' + str(scratch/'site.db')
    from app import create_app, db
    from app.models import CatalogueOperation, Settings, User
    from app import intake_direct as tools
    from PIL import Image
    from jinja2 import ChoiceLoader, FileSystemLoader
    app = create_app(recover=False)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, INTAKE_TEST_MOUNTED=True,
                     INTAKE_MUTATION_LOCK_PATH=str(scratch/'intake.lock'))
    app.jinja_loader = ChoiceLoader([FileSystemLoader(str(Path(overlay)/'app/templates')), app.jinja_loader])
    with app.app_context():
        db.session.add(User(username='probe',email='probe@example.test',password='unused'))
        db.session.add(Settings(_product_folder=str(scratch/'catalogue'),_output_folder=str(scratch/'output'),_url_prefix='https://invalid.example/'))
        db.session.commit()
    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id']='1';session['_fresh']=True
    dogs = fixture/'Dogs';dogs.mkdir()
    for filename, colour in [('hound 1.png','red'),('hound 2.png','blue'),('beagle 1.png','green'),('beagle 2.png','yellow')]:
        Image.new('RGB',(12,9),colour).save(dogs/filename)
    original = sorted(hashlib.sha256(p.read_bytes()).hexdigest() for p in dogs.glob('*.png'))
    source = name+'/Dogs'
    def apply(stage, options):
        preview = client.post('/image-preparation/'+stage+'/preview?live=1',data={'path':source,'options':json.dumps(options)})
        assert preview.status_code==200 and preview.json['ready'], preview.get_data(as_text=True)
        data={'path':source,'options':json.dumps(preview.json['options']),'digest':preview.json['digest']}
        response=client.post('/image-preparation/'+stage+'/confirm',data=data)
        assert response.status_code==200 and 'Changes applied' in response.get_data(as_text=True),response.get_data(as_text=True)
        return data
    with patch('app.utils.scan_runner.start_scan',side_effect=AssertionError('No real registration authorised')):
        accepted=apply('group',{})
        duplicate=client.post('/image-preparation/group/confirm',data=accepted)
        assert duplicate.status_code==302
        apply('folders',{'renames':{'hound':'TLC Hound Card','beagle':'TLC Beagle Card'},'sku_prefix':'PROBE-'})
        apply('rename',{'prefix':'PROBE'})
        document={'collection_type':'Simple','sku_prefix':'PROBE-','live':False,'price':'3.50',
                  'customisation':{'storefront_enabled':True,'fields':{'dogs_name':{'label':'Dogs Name','type':'text','required':True}}}}
        apply('metadata',{'document':document})
        document['price']='4.00';apply('metadata',{'document':document})
        assert json.loads((dogs/'product_info.json').read_text())==document
        assert (dogs/'TLC Hound Card/probe_tlc_hound_card_01.png').exists()
        assert sorted(hashlib.sha256(p.read_bytes()).hexdigest() for p in dogs.rglob('*.png'))==original
        assert not list(dogs.rglob('.tlc-*')) and not (fixture/'Prepared').exists()
        for stage in ('group','folders','rename','metadata','handoff'):
            body=client.get('/image-preparation/stage/'+stage,query_string={'path':source}).get_data(as_text=True)
            assert body.index('Selected Intake folder') < body.index('Independent Intake stages')
            assert '/image-preparation/stage/'+stage+'?path=' in body
        # Reopen an already prepared source directly, with no operation history.
        with app.app_context():
            assert CatalogueOperation.query.count()==0
            assert tools.plan('metadata',source)['ready']
        report={'real_mount':str(root),'fixture':str(fixture),'runtime_uid':os.getuid(),
                'group':True,'folder_rename_and_sku':True,'image_rename':True,'json_create_edit':True,
                'duplicates_blocked':True,'independent_tools':True,'folder_selection_top':True,
                'original_hashes_preserved':True,'preparation_operation_rows':0,
                'catalogue_imports':0,'scans':0,'shopify_calls':0,'scratch':str(scratch)}
    (scratch/'report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report),flush=True)


if __name__=='__main__':main(*sys.argv[1:])
