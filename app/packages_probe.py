"""Read-only installed-store package schema probe: no app init or database writes."""
import json
from pathlib import Path
from flask import Flask
from config import Config


def main():
    from app.shopify_connection import ShopifyAdapter
    from app.shopify_drafts import configuration
    from app.shopify_packaging import assess
    from app.packaging_queries import CAPABILITY
    app=Flask('package-capabilities-only',instance_path=str(Path(__file__).resolve().parents[1]/'instance'))
    app.config.from_object(Config)
    app.config['SHOPIFY_CAPACITY_COORDINATION']=False
    adapter=ShopifyAdapter()
    with app.app_context():
        try:
            config=configuration()
            if config.api_version!='2026-10':raise ValueError('Package schema probe requires 2026-10')
            shop=adapter.test_connection(config)
            raw=adapter._read(config,CAPABILITY)
            if raw.get('shop',{}).get('id')!=shop['shop_id']:raise ValueError('Store mismatch')
            result=assess(raw,shop['scopes'])
            result.update(read_only=True,database_writes=0,api_requests=getattr(adapter,'request_count',0))
        except Exception:
            result={'read_only':True,'database_writes':0,'result':'unavailable',
                'next_action':'Check the existing Shopify Connection test. No credentials or raw API response are printed.'}
        print(json.dumps(result,indent=2))


if __name__=='__main__':main()
