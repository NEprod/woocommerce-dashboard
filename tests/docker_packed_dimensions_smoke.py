"""Offline0017→0018, two workers and restart; no Shopify/Discord/network."""
import sys
import docker_orders_webhooks_smoke as previous

smoke=previous.smoke
smoke.IMPORT+='''
from app.packaging_contract import effective,packed_cm,finished_mm
from app.utils.json_utils import apply_variation_modifiers
from app.packing import recommend
document={'price':'3.50','weight':'50','dimensions':{'length':420,'width':297,'height':1},
 'packaging':{'packed_dimensions':{'length':'31','width':'4','height':'4','unit':'cm'},'measured':True,'selected_package_id':None},
 'variation_modifiers':{'Size=A3':{'price':'8.50','weight':'150','packaging':{'fragile':True}}}}
assert finished_mm(document)['length']=='420'
assert packed_cm(effective(document))['length']=='31'
assert apply_variation_modifiers(document,{'Size':'A3'},log=lambda *a,**k:None)['price']=='8.50'
assert recommend(effective(document),[])['recommendation'] is None
from pathlib import Path
Path('/catalogue/fictional-dimensions.json').write_text(json.dumps(document))
'''
smoke.CHECK+='''
from app.packaging_contract import effective,packed_cm,finished_mm
document=json.loads(Path('/catalogue/fictional-dimensions.json').read_text())
assert document['dimensions']=={'length':420,'width':297,'height':1}
assert packed_cm(effective(document))['length']=='31' and effective(document)['selected_package_id'] is None
assert finished_mm(document)['width']=='297'
'''

if __name__=='__main__':smoke.main(sys.argv[1])
