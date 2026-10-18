"""Offline V2 ID/local metadata save and restart, preserving Orders/checkpoints."""
import sys
import docker_reconciliation_repair_smoke as previous

smoke=previous.smoke
V2='gid://shopify/ShippingPackageV2/116237992319'
smoke.IMPORT=smoke.IMPORT.replace('gid://shopify/ShippingPackage/123',V2)
smoke.CHECK=smoke.CHECK.replace('gid://shopify/ShippingPackage/123',V2)
original_docker=smoke.docker


def docker(*args,**kwargs):
    if args[0]=='run':
        args=(args[0],'-e','SHOPIFY_STORE_DOMAIN=fictional.myshopify.com',
            '-e','SHOPIFY_ADMIN_ACCESS_TOKEN=synthetic_offline_only_000000000000',*args[1:])
    return original_docker(*args,**kwargs)


smoke.docker=docker
smoke.IMPORT+='''
from app import package_links
with app.app_context():
 package=Package.query.filter_by(seed_key='c5-plus').one()
 old=json.loads(package.specification)
 PackageLink.query.filter_by(package_id=package.id).delete();db.session.commit()
 package_links.set_id(package,'gid://shopify/ShippingPackageV2/116237992319',[])
 catalogue.save({'name':package.name,'purchase_price':'0.42','purchase_url':'https://supplier.example.test/package'},package,version=package.version)
 assert package_links.current(package.id)['id']=='gid://shopify/ShippingPackageV2/116237992319'
 assert all(json.loads(package.specification)[key]==value for key,value in old.items() if key not in {'purchase_price','purchase_url'})
'''
smoke.CHECK+='''
assert spec['purchase_price']=='0.42' and spec['purchase_url']=='https://supplier.example.test/package'
page=opener.open('http://127.0.0.1:7485/packaging/'+c5[0]+'/edit',timeout=5).read().decode()
assert 'gid://shopify/ShippingPackageV2/116237992319' in page
assert 'not remotely verified' in page and 'Advanced · optional internal usable dimensions' in page
assert 'name="dimensions_measured"' not in page and 'name="max_gross_g"' not in page
'''

if __name__=='__main__':smoke.main(sys.argv[1])
