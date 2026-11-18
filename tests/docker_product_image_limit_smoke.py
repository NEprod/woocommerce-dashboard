"""Run inside the image with --network none --memory 256m; no app/database startup."""
import hashlib
import io
import json
import resource
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import requests
from PIL import Image
from app import shopify_drafts as drafts
from app.shopify_connection import ShopifyAdapter, ShopifyConfiguration


def main():
    with tempfile.TemporaryDirectory(prefix='product-image-limit-') as temp:
        root=Path(temp).resolve(); folder=root/'Synthetic';folder.mkdir()
        path=folder/'max.png'
        raw=io.BytesIO();Image.new('RGB',(32,32),'red').save(raw,'PNG')
        with path.open('wb') as stream:
            stream.write(raw.getvalue());stream.write(b'\0'*(drafts.MAX_IMAGE-len(raw.getvalue())))
        with patch('app.shopify_drafts._source_context',return_value=(root,folder,None)):
            content,name,mime=drafts.source_bytes(None,'Synthetic/max.png')
        digest=hashlib.sha256(content).hexdigest()
        class Response:
            status_code=201
            def __enter__(self):return self
            def __exit__(self,*args):return False
        class Session:
            trust_env=True
            def post(self,url,**options):
                assert not self.trust_env and options['timeout']==(5,30)
                assert options['allow_redirects'] is False
                prepared=requests.Request('POST',url,data=options['data'],files=options['files']).prepare()
                assert 'X-Shopify-Access-Token' not in prepared.headers
                assert prepared.body.count(content)==1
                assert int(prepared.headers['Content-Length'])>drafts.MAX_IMAGE
                return Response()
            def close(self):pass
        with patch('app.shopify_connection.requests.Session',Session):
            ShopifyAdapter().upload_image(ShopifyConfiguration('fictional.myshopify.com','fictional_admin_token_for_tests'),
                {'url':'https://storage.googleapis.com/offline','resourceUrl':'https://storage.googleapis.com/offline',
                 'parameters':[{'name':'key','value':'offline'}]},
                {'filename':name,'mime':mime,'size':len(content)},content)
        assert digest==hashlib.sha256(path.read_bytes()).hexdigest()
        peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1048576 if sys.platform=='darwin' else 1024)
        assert peak<256,peak
        print(json.dumps({'individual_bytes':len(content),'peak_rss_mib':round(peak,2),
            'batch_byte_limit':drafts.MAX_UPLOAD_BYTES,'source_unchanged':True,'external_requests':0}))


if __name__=='__main__':main()
