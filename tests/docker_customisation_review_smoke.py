"""Current offline two-worker upgrade/restart plus readable review from saved JSON."""
import subprocess
import sys
import docker_override_scan_smoke as previous

smoke = previous.smoke
smoke.CHECK = smoke.CHECK.replace("'local_pages':True", "'customisation_review':True,'local_pages':True")
smoke.CHECK += '''
from copy import deepcopy
from flask import Flask,render_template
from app.storefront_customisations import public_configuration
from app.shopify_simple_fields import review_rows
current=public_configuration(bootstrap['resolved']['customisation'])
assert [f['id'] for f in current['fields']]==['recipient','age','personal_message']
previous=deepcopy(current);previous['fields']=previous['fields'][:-1]
previous['fields'][0]['required']=False;previous['revision']='a'*64
spec={'before':{'value':{'value':json.dumps(previous)}},'desired':current}
plan={'classification':'update','customisations':spec,'fields':[{
 'field':'customisations.configuration','before':{},'after':{},'changed':True}]}
rows=review_rows(plan)
view=Flask('review-smoke',template_folder='/app/app/templates')
with view.test_request_context():
 rendered=render_template('includes/_customisation_review.html',review_rows=rows)
assert 'Required: No' in rendered and 'Personal Message</strong> — Added' in rendered
assert 'Technical details' in rendered and current['revision'] in rendered
assert rows[0]['customisation_details']['matching'][0]['id']=='age'
assert 'revision' not in rows[0]['before'] and 'revision' not in rows[0]['after']
'''

if __name__ == '__main__':
    try:
        smoke.main(sys.argv[1])
    except subprocess.CalledProcessError as error:
        print(error.stderr, file=sys.stderr)
        raise SystemExit(error.returncode)
