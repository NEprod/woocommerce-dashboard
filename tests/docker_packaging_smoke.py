"""Two-worker offline migration0016→0017, package persistence and Orders regression."""
import sys
import docker_orders_smoke as smoke

smoke.SEED_OLD=smoke.SEED_OLD.replace('0015_customisation_templates','0016_shopify_orders').replace('synthetic_0015_seeded','synthetic_0016_seeded')
smoke.CHECK=smoke.CHECK.replace('0016_shopify_orders','0017_packaging').replace('*0015*0016*','*0016*0017*')+'''
assert opener.open('http://127.0.0.1:7485/packaging/',timeout=5).status==200
with sqlite3.connect('/app/instance/site.db') as connection:
 assert connection.execute('select count(*) from packaging_package').fetchone()[0]==7
 tables={r[0] for r in connection.execute("select name from sqlite_master where type='table'")}
 assert not {'packaging_plan','packaging_rates'} & tables
 assert connection.execute('pragma foreign_key_check').fetchall()==[]
'''
smoke.IMPORT+='''
from app.packaging_catalogue import save
from app.packing import recommend
with app.app_context():
 row=save({'name':'Offline measured fixture','type':'BOX','external_cm':{'length':'18','width':'18','height':'2'},
 'internal_cm':{'length':'17','width':'17','height':'1.5'},'tare_g':'40','max_contents_g':'500',
 'clearance_cm':'0','protection_weight_g':'0','dimensions_measured':True,'tare_measured':True,'automatic':True,'flat':True})
 from app.packaging_catalogue import view
 result=recommend({'packed_dimensions_cm':{'length':'15','width':'15','height':'.3'},'weight_g':'50','clearance_cm':'0','protection_weight_g':'0',
 'orientation':'flat','method':'flat','fragile':False,'measured':True},[view(row)])
 assert result['state']=='verified'
'''

if __name__=='__main__': smoke.main(sys.argv[1])
