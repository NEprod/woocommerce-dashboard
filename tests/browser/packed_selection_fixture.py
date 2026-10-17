"""Disposable final real-DOM selection smoke; synthetic data, no outbound access."""
from packaging_fixture import app
from flask import render_template_string
from app.packaging_catalogue import save
from test_packaging import package

with app.app_context():
    save(dict(package('fictional-large',('45','32','2')),name='Fictional measured print box'))

@app.get('/fixture/packed-dimensions')
def selection_fixture():
    return render_template_string('''<!doctype html><html lang="en"><head>
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="csrf-token" content="{{ csrf_token() }}">
<link rel="stylesheet" href="/static/assets/vendor/bootstrap/dist/css/bootstrap.min.css">
<link rel="stylesheet" href="/static/assets/css/custom.css"><title>Fictional packed selection smoke</title>
</head><body><main style="padding:1rem"><h1>Fictional packed selection smoke</h1>
<p>Actual TLC component and local recommendation API. No save, Shopify or Discord.</p>
<article data-size="A5"><h2>A5</h2><div id="small"></div></article>
<article data-size="A3"><h2>A3</h2><div id="large"></div></article>
<button type="button" id="inspect">Inspect unsaved state</button><pre id="state"></pre></main>
<script src="/static/assets/js/packaging.js"></script><script>
const instances={};
for(const [id,length,width] of [['small','21','14.8'],['large','42','29.7']]){
 const value={packed_dimensions:{length,width,height:'.2',unit:'cm'},weight_g:'50',clearance_cm:'0',protection_weight_g:'0',orientation:'flat',method:'flat',fragile:false,measured:true};
 instances[id]=new TLCPackaging.Editor(document.getElementById(id),{value});
}
document.getElementById('inspect').addEventListener('click',()=>document.getElementById('state').textContent=JSON.stringify(Object.fromEntries(Object.entries(instances).map(([k,v])=>[k,v.get()])),null,2));
</script></body></html>''')

if __name__=='__main__':app.run(host='127.0.0.1',port=7497,use_reloader=False)
