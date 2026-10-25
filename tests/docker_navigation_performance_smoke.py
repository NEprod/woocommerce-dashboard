"""Existing offline two-worker upgrade/restart and per-view registry budget."""
import sys

import docker_customisation_review_smoke as previous

smoke = previous.smoke
smoke.CHECK += '''
from unittest.mock import patch
from app import create_app
from app import taxonomy_registry
read_app=create_app(recover=False)
client=read_app.test_client()
with client.session_transaction() as session:
 session['_user_id']='1';session['_fresh']=True
for url in ('/','/api/edit_products'):
 with patch.object(taxonomy_registry,'load_registry',wraps=taxonomy_registry.load_registry) as reads:
  response=client.get(url)
  assert response.status_code==200,(url,response.status_code)
  assert reads.call_count==1,(url,reads.call_count)
'''

if __name__ == '__main__':
    smoke.main(sys.argv[1])
