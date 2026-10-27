"""Fixed GraphQL response bridge, never a network client or live mutation."""
import copy
import json
from app import shopify_connection as api


class ReadBridge(api.ShopifyAdapter):
    def __init__(self, remotes, backend):
        self.remotes, self.backend = remotes, backend

    def _request(self, config, query, variables=None, **kwargs):
        v=variables or {};b=self.backend
        if query in api.WRITE_QUERIES: raise AssertionError('reconciliation mutation')
        self._note_request(query,config.api_version,200)  # no invented Shopify costs
        if v.get('id') in self.remotes: b.remote=copy.deepcopy(self.remotes[v['id']])
        if query==api.QUERY:
            shop=b.test_connection(config)
            return {'shop':{'id':shop['shop_id'],'myshopifyDomain':config.domain,'primaryDomain':None,'domains':[]},
                    'currentAppInstallation':{'accessScopes':[{'handle':s} for s in shop['scopes']]}}
        if query==api.SIMPLE_QUERY:return {'product':copy.deepcopy(self.remotes.get(v['id']))}
        if query==api.COUNTRIES_QUERY:return {'__type':{'enumValues':[{'name':n} for n in b.country_codes(config)]}}
        if query==api.DIMENSION_DEFINITIONS:
            rows=b.dimension_definitions(config,v['namespace'],v['owner'])
            return {axis:{'nodes':[value] if value else [],'pageInfo':{'hasNextPage':False}} for axis,value in rows.items()}
        if query==api.COLLECTION_STATE:return {'collection':b.collection_state(config,v['id'],v['product'])}
        if query==api.PUBLICATIONS_QUERY:return {'publications':{'nodes':b.publications(config),'pageInfo':{'hasNextPage':False}}}
        if query==api.PUBLICATION_STATE:return {'product':b.publication_state(config,v['id'])}
        if query==api.CUSTOMISATION_DEFINITION:
            value=b.customisation_definition(config,v['namespace'],v['key'])
            return {'metafieldDefinitions':{'nodes':[value] if value else [],'pageInfo':{'hasNextPage':False}}}
        if query==api.CUSTOMISATION_VALUE:return {'product':{'id':v['id'],'metafield':b.customisation_value(config,v['id'],v['namespace'],v['key'])}}
        if query==api.ATTRIBUTE_SCHEMA:return b.attribute_schema(config)
        if query==api.ATTRIBUTE_DEFINITION:return {'metaobjectDefinitionByType':b.attribute_resource(config,'definition',v)}
        if query==api.ATTRIBUTE_TERM:return {'metaobjectByHandle':b.attribute_resource(config,'term',v)}
        if query==api.ATTRIBUTE_FIELD or query in api.ATTRIBUTE_FIELD_CAPABILITIES.values():
            value=b.attribute_resource(config,'field',v)
            return {'metafieldDefinitions':{'nodes':[value] if value else [],'pageInfo':{'hasNextPage':False}}}
        if query==api.ATTRIBUTE_ENTRIES:
            return {'nodes':[next((copy.deepcopy(x) for x in b.resources.values() if x['id']==id),None) for id in v['ids']]}
        if query==api.ATTRIBUTE_VALUES:return {'product':{'id':v['id'],'metafields':{'nodes':b.attribute_values(config,v['id'],v['namespace']),'pageInfo':{'hasNextPage':False}}}}
        raise AssertionError('Missing fixed read fixture: '+query[:70])


def catalogue(world, count, registry=None):
    """Clone already-verified fictional products and original-source folders."""
    from app import db, shopify_drafts as drafts
    from app.models import Product,ProductImage,ShopifyProductIdentity
    from test_shopify_publication_sales import AcceptanceAdapter
    product,folder,_=world;b=AcceptanceAdapter()
    if registry is not None:
        from test_shopify_live_fixes import AccessAdapter
        from test_shopify_simple_updates import change
        registry_data=json.loads(registry.read_text())
        registry_data['attributes']=[{'key':f'profile-{i}','name':f'Profile Attribute {i}','slug':f'profile-{i}',
            'state':'active','navigation':False,'visible_default':True,'browse_role':'filter',
            'terms':[{'key':f'value-{i}','name':f'Value {i}','slug':f'value-{i}','state':'active'}]} for i in range(5)]
        registry.write_text(json.dumps(registry_data))
        change(world,country_of_origin='GB',attributes={f'Profile Attribute {i}':[f'Value {i}'] for i in range(5)},variation_attributes=[])
        b=AccessAdapter()
    _,token=drafts.preview(product,b);identity=drafts.confirm(token,True,b)
    old_remote=copy.deepcopy(b.remote);old_data=drafts.evidence(identity)
    remotes={old_remote['id']:old_remote};products=[product]
    product_values={c.name:copy.deepcopy(getattr(product,c.name)) for c in Product.__table__.columns if c.name!='id'}
    identity_values={c.name:copy.deepcopy(getattr(identity,c.name)) for c in ShopifyProductIdentity.__table__.columns if c.name!='id'}
    for i in range(1,count):
        sibling=folder.parent/f'Profile {i:04}';sibling.mkdir()
        for p in folder.iterdir():
            if p.is_file() and p.suffix in {'.png','.jpg'}:(sibling/p.name).write_bytes(p.read_bytes())
        p=Product(**{**product_values,'sku':f'PROFILE-{i:04}','title':f'Profile {i:04}',
            'source_relpath':str(sibling.relative_to(folder.parent.parent)),'override_json_path':None})
        db.session.add(p);db.session.flush()
        for image in product.images:p.images.append(ProductImage(url=image.url,position=image.position,alt_text=image.alt_text))
        plan=drafts.local_plan(p)
        replacements={old_remote['id']:f'gid://shopify/Product/{1000+i}',
            old_data['variant_id']:f'gid://shopify/ProductVariant/{2000+i}',
            old_data['inventory_item_id']:f'gid://shopify/InventoryItem/{3000+i}',product.sku:p.sku}
        for j,node in enumerate(old_remote['media']['nodes']):replacements[node['id']]=f'gid://shopify/MediaImage/{10000+i*10+j}'
        def transform(v):
            if isinstance(v,dict):return {k:transform(x) for k,x in v.items()}
            if isinstance(v,list):return [transform(x) for x in v]
            return replacements.get(v,v) if isinstance(v,str) else v
        data=transform(copy.deepcopy(old_data));data['plan'].update(plan)
        data['plan']['nonce']=old_data['plan']['nonce'];data['plan']['handle']=f'profile-{i:04}'
        for receipt,image in zip(data['media'],plan['images']):receipt['source']=image['source']
        remote=transform(copy.deepcopy(old_remote));remote.update(title=plan['title'],handle=f'profile-{i:04}')
        remote['updatedAt']='2026-10-12T00:00:00Z'
        for key,value in plan['managed']['product'].items():
            if key not in {'metafields','handle'}:remote[key]=copy.deepcopy(value)
        row=ShopifyProductIdentity(**{**identity_values,'product_id':p.id,'source_identity':p.source_relpath,
            'sku':p.sku,'shopify_product_id':remote['id'],'media_evidence_json':drafts.encode(data)})
        db.session.add(row);products.append(p);remotes[remote['id']]=remote
    db.session.commit()
    return products,ReadBridge(remotes,b)
