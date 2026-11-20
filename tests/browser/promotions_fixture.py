"""Synthetic promotion row in the isolated, outbound-blocked catalogue browser fixture."""
from organisation_fixture import app
from app import db
from app.promotions import domain, publish
from app.promotions.models import Promotion

with app.app_context():
    document = domain.definition(dict(name='Fictional ornament offer',kind='exact',method='automatic',
        scope='product',intent='draft',starts=None,ends=None,targets=[],exclusions=[],combines={},
        tiers=[{'qty':3,'total':1995}],repeat=True))
    db.session.add(Promotion(id='f'*32,document=domain.canonical(document),updated_at=publish.stamp()))
    db.session.commit()

if __name__ == '__main__':
    app.run(host='127.0.0.1',port=7504,use_reloader=False)
