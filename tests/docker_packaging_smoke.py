"""Extend the established offline two-worker Orders restart smoke with packaging."""
import sys
import docker_orders_smoke as smoke

smoke.CHECK=smoke.CHECK.replace('0016_shopify_orders','0017_packaging').replace('*0015*0016*','*0015*0017*')+'''
assert opener.open('http://127.0.0.1:7485/packaging/',timeout=5).status==200
assert opener.open('http://127.0.0.1:7485/packaging/simulator',timeout=5).status==200
assert opener.open('http://127.0.0.1:7485/packaging/rates',timeout=5).status==200
with sqlite3.connect('/app/instance/site.db') as connection:
 assert connection.execute('select count(*) from packaging_package').fetchone()[0]==8
 assert connection.execute('select count(*) from packaging_plan').fetchone()[0]==1
 assert connection.execute('pragma foreign_key_check').fetchall()==[]
'''
smoke.IMPORT+='''
from app.packaging_catalogue import save
from app.packaging_workspace import simulate
from app.packaging_models import Package
with app.app_context():
 save({'name':'Offline measured fixture','type':'BOX','external_cm':{'length':'18','width':'18','height':'2'},
 'internal_cm':{'length':'17','width':'17','height':'1.5'},'tare_g':'40','max_contents_g':'500',
 'clearance_cm':'0','protection_weight_g':'0','dimensions_measured':True,'tare_measured':True,'automatic':True,'flat':True,'stackable':True})
 result=simulate([{'identity':'synthetic-line','sku':'FIXTURE','title':'Fictional card','quantity':2,'requires_shipping':True,
 'packed_dimensions_cm':{'length':'15','width':'15','height':'.3'},'weight_g':'50','clearance_cm':'0','protection_weight_g':'0',
 'orientation':'flat','classification':'flat','may_share':True,'may_stack':True,'stack_limit':10,'measured':True}])
 assert result['state']=='verified' and len(result['parcels'])==1
'''

if __name__=='__main__':smoke.main(sys.argv[1])
