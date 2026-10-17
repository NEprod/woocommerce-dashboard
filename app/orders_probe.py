"""Read-only merchant diagnostic: no app initialisation, migration or DB writes.

Run inside the existing container: python -m app.orders_probe
Prints only phases, request counts and fixed safe categories, never order data.
"""
import json
from pathlib import Path
from flask import Flask
from config import Config


def main():
    from app.shopify_connection import ShopifyAdapter
    from app.shopify_drafts import configuration
    from app.order_diagnostics import diagnostic
    from app.shopify_orders import read_order, connection, now
    from app.order_queries import INDEX
    from datetime import timedelta
    app=Flask('orders-read-only-probe',instance_path=str(Path(__file__).resolve().parents[1]/'instance'))
    app.config.from_object(Config)
    adapter=ShopifyAdapter();phase='configuration'
    with app.app_context():
        try:
            config=configuration();phase='preflight'
            shop=adapter.test_connection(config)
            if not {'read_orders','write_orders'} & set(shop['scopes']):
                from app.shopify_orders import OrdersPermissionRequired
                raise OrdersPermissionRequired('Missing read_orders')
            phase='index'
            data=adapter._read(config,INDEX,{'after':None,'first':1,'query':"updated_at:>='"+(now()-timedelta(days=30)).isoformat()+"Z'"})
            nodes,_=connection(data.get('orders'),maximum=1)
            if nodes:
                phase='order_read';read_order(adapter,config,nodes[0]['id'])
            print(json.dumps({'result':'complete_sample' if nodes else 'no_recent_orders','api_version':config.api_version,
                'read_orders':True,'api_requests':getattr(adapter,'request_count',0),'database_writes':0}))
        except Exception as error:
            print(json.dumps({'result':'failed','api_requests':getattr(adapter,'request_count',0),**diagnostic(error,phase)}))


if __name__=='__main__':main()
