"""Explicit, non-destructive first-run empty catalogue completion."""
import pytest
from app import db, onboarding
from app.models import User, Product, Collection, CatalogueOperation
from test_taxonomy_workspace import registry_app, field
from test_onboarding_deployment import configured


@pytest.fixture
def empty_setup(configured):
    app,client,root,catalogue,output=configured
    with app.app_context():
        User.query.one().is_admin=True;db.session.commit()
    return configured


def complete(client):
    page=client.get('/initial-settings')
    return client.post('/setup/empty',data=dict(csrf_token=field(page,'csrf_token'),confirmed_empty='yes'))


def test_start_empty_reaches_dashboard_and_is_restart_stable(empty_setup):
    app,client,root,catalogue,output=empty_setup
    assert not list(catalogue.iterdir())
    assert client.get('/').headers['Location'].endswith('/initial-settings')
    page=client.get('/initial-settings')
    assert b'Start with an empty catalogue' in page.data
    assert complete(client).headers['Location'].endswith('/')
    dashboard=client.get('/')
    assert dashboard.status_code==200 and b'Dashboard' in dashboard.data
    with app.app_context():
        assert not onboarding.pending()
        state=onboarding.state()
        assert state['completion']=='empty_catalogue' and state['completed_by']
        assert not Product.query.count() and not Collection.query.count()
        assert not CatalogueOperation.query.count()  # No fake scan receipt.
    # A new session/connection reads the durable instance state, not process memory.
    with app.app_context():db.session.remove();assert onboarding.state()==state
    assert complete(client).status_code==303
    assert not list(catalogue.iterdir())
    assert (root/'registry.json').is_file()


@pytest.mark.parametrize('content',['folder','file','marker_only','hidden_folder'])
def test_empty_option_checks_contents_without_touching_files(empty_setup,content):
    app,client,root,catalogue,output=empty_setup
    if content=='marker_only':
        (catalogue/'.tlc-deployment.json').write_text('{"fixture":"untouched"}')
    elif content=='folder':(catalogue/'Cards').mkdir()
    elif content=='hidden_folder':(catalogue/'.private-products').mkdir()
    else:(catalogue/'product_info.json').write_text('{"title":"Retained"}')
    before={p.name:p.read_bytes() if p.is_file() else None for p in catalogue.iterdir()}
    assert complete(client).status_code==303
    with app.app_context():assert onboarding.pending() is (content!='marker_only')
    assert {p.name:p.read_bytes() if p.is_file() else None for p in catalogue.iterdir()}==before


def test_missing_mount_and_existing_records_cannot_be_skipped(empty_setup):
    app,client,root,catalogue,output=empty_setup
    catalogue.rmdir()
    assert complete(client).status_code==303
    with app.app_context():assert onboarding.pending()
    catalogue.mkdir()
    # A retained operation must not be bypassed by the alternative setup button.
    with app.app_context():
        db.session.add(CatalogueOperation(id='retained-scan',operation_type='append',status='running',scope='{}'))
        db.session.commit()
    assert complete(client).status_code==303
    with app.app_context():
        assert onboarding.pending()
        operation=db.session.get(CatalogueOperation,'retained-scan');operation.status='succeeded';db.session.commit()
        db.session.add(Product(sku='RETAINED-1',title='Retained product',product_type='simple'))
        db.session.commit()
    assert complete(client).status_code==303
    with app.app_context():assert onboarding.pending() and Product.query.count()==1


def test_empty_setup_needs_admin_confirmation_and_csrf(empty_setup):
    app,client,*_=empty_setup
    assert client.post('/setup/empty',data={'confirmed_empty':'yes'}).status_code==400
    page=client.get('/initial-settings');csrf=field(page,'csrf_token')
    assert client.post('/setup/empty',data={'csrf_token':csrf}).status_code==400
    with app.app_context():User.query.one().is_admin=False;db.session.commit()
    assert client.post('/setup/empty',data={'csrf_token':csrf,'confirmed_empty':'yes'}).status_code==403
    with app.app_context():assert onboarding.pending()


def test_empty_catalogue_without_saved_settings_row(empty_setup):
    from app.models import Settings
    app,client,root,catalogue,output=empty_setup
    with app.app_context():
        db.session.delete(Settings.query.one());db.session.commit()
        assert onboarding.readiness()['ready']
    assert complete(client).status_code==303
    assert client.get('/').status_code==200
