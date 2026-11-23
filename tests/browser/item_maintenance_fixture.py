"""Reuse the offline catalogue fixture with two explicitly disposable items."""
from catalogue_bom_fixture import app, client
from test_inventory import item

ids=[item(client,'purchased',f'FIX-{mode.upper()}',f'Unused {mode}') for mode in ['desktop','mobile']]
app._got_first_request=False


@app.get('/maintenance-fixture')
def maintenance_fixture():
    return dict(items=ids)


if __name__=='__main__':
    app.run(host='127.0.0.1',port=7516,use_reloader=False)
