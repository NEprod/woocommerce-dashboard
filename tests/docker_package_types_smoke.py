"""Offline old-record review/transfer, stable IDs, two workers and restart."""
import sys
import docker_package_simplification_smoke as previous

smoke=previous.smoke
# Synthetic pre-upgrade historical data, never an application default.
smoke.SEED_OLD+='''
import json
with sqlite3.connect('/app/instance/site.db') as connection:
 ident,raw=connection.execute("select id,specification from packaging_package where seed_key='c5-plus'").fetchone()
 spec=json.loads(raw);spec['max_contents_thickness_mm']='3'
 connection.execute('update packaging_package set specification=? where id=?',(json.dumps(spec),ident))
'''
smoke.IMPORT+='''
from app.packing import recommend
with app.app_context():
 package=Package.query.filter_by(seed_key='c5-plus').one()
 spec=catalogue.view(package)
 assert catalogue.envelope_height(spec)[0] is None
 assert recommend({'packed_dimensions_cm':{'length':'15','width':'15','height':'.2'}},[spec])['recommendation'] is None
 catalogue.save({'name':package.name,'internal_cm':{'height':'3'},'dimension_unit':'mm',
  'confirm_envelope_thickness_change':True},package,version=package.version)
 assert catalogue.envelope_height(catalogue.view(package))[0]==__import__('decimal').Decimal('.3')
'''
smoke.CHECK=smoke.CHECK.replace("spec['max_contents_thickness_mm']=='3'", "spec['max_contents_thickness_mm'] is None")
smoke.CHECK=smoke.CHECK.replace("all(v is None for v in spec['internal_cm'].values())", "spec['internal_cm']=={'length':None,'width':None,'height':'0.3'}")
smoke.CHECK+='''
with sqlite3.connect('/app/instance/site.db') as connection:
 history=json.loads(connection.execute("select value from packaging_evidence where key like 'package-thickness-history:%'").fetchone()[0])
 assert history['max_contents_thickness_mm']=='3' and history['internal_height_cm'] is None
 assert history['replacement_internal_height_cm']=='0.3'
assert 'name="max_contents_thickness_mm"' not in page
assert 'Transfer historical thickness to internal height' in page
'''

if __name__=='__main__':smoke.main(sys.argv[1])
