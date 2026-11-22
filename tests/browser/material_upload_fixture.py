"""Isolated image/editor fixtures. Parent fixture denies every outbound request."""
from production_bom_fixture import app, client, item
from PIL import Image

item(client,'material','SHEET-TEST','Birch sheet',unit='sheet',category='Plywood',
     sheet_length='600',sheet_width='400',sheet_thickness='3',pack_qty='10',pack_price='25')
noise=Image.effect_noise((3200,2400),30)
photo=Image.blend(Image.linear_gradient('L').resize(noise.size),noise,.35).convert('RGB')
photo.save('/tmp/tlc-material-form-source.jpg','JPEG',quality=100)

if __name__=='__main__':
    app.run(host='127.0.0.1',port=7515,use_reloader=False)
