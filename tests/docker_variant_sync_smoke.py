"""Existing offline two-worker upgrade/restart plus acknowledgement classification."""
import sys
import docker_shipping_editor_smoke as previous

smoke = previous.smoke
smoke.CHECK += '''
from types import SimpleNamespace
from app.shopify_package_assignments import completion
from app.shopify_notifications import complete
identity=SimpleNamespace(state='verified',verification_state='verified')
receipt={'plan':{'native':{'enabled':True,'unconfigured':True},'optional_packages':[
 {'identity':'size-a4','action':'assign','shipping_package_id':'gid://shopify/ShippingPackageV2/123',
  'inventory_id':'gid://shopify/InventoryItem/1201'}]},
 'verification':{key:'verified' for key in ('identity','fields','media','memberships','attributes')},
 'package_assignments':{'size-a4':{'state':'accepted','inventory_id':'gid://shopify/InventoryItem/1201',
  'shipping_package_id':'gid://shopify/ShippingPackageV2/123','independently_verified':False}}}
assert completion(receipt)=='accepted' and complete(identity,receipt)
assert receipt['package_assignments']['size-a4']['independently_verified'] is False
receipt['package_assignments']['size-a4']['state']='uncertain'
assert completion(receipt)=='uncertain' and not complete(identity,receipt)
receipt['package_assignments']['size-a4']['state']='rejected'
assert completion(receipt)=='rejected' and not complete(identity,receipt)
receipt['package_assignments']['size-a4']['state']='accepted'
receipt['package_assignments']['size-a4']['inventory_id']='gid://shopify/InventoryItem/999'
assert completion(receipt)=='outstanding' and not complete(identity,receipt)
'''

if __name__ == '__main__':
    smoke.main(sys.argv[1])
