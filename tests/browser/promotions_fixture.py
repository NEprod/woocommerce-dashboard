"""Synthetic promotion row in the isolated, outbound-blocked catalogue browser fixture."""
from organisation_fixture import app
from app import db
from app.promotions import domain, publish
from app.promotions import routes, planner
from app.promotions.models import Promotion

with app.app_context():
    document = domain.definition(dict(name='Fictional ornament offer',kind='exact',method='automatic',
        scope='product',intent='draft',starts=None,ends=None,targets=[],exclusions=[],combines={},
        tiers=[{'qty':3,'total':1995}],repeat=True))
    db.session.add(Promotion(id='f'*32,document=domain.canonical(document),updated_at=publish.stamp()))
    db.session.commit()

# Synthetic review projection for visual QA only. The real resolver and BOM
# calculation are covered by isolated Python tests; this fixture blocks outbound
# transport and must never be used as application data.
routes._local_rows = lambda _: [{'sku':'FIXTURE-001','local_id':1,'price':'6.00',
                                  'name':'Fictional ornament','options':{}}]
planner.variant_costs = lambda _: {'FIXTURE-001':{'cost':200,'bom_complete':True,
    'mode':'calculated','material_component':200,'packaging':None,
    'lines':[{'name':'Fictional material','category':'Material','cost':200,'problem':None}],
    'missing':[]}}

if __name__ == '__main__':
    app.run(host='127.0.0.1',port=7504,use_reloader=False)
