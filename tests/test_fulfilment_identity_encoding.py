"""Publishing evidence encoding must not change purchased-variant resolution."""
import json
import pytest
from app import db, order_work as work
from app.models import ShopifyOrderLine, ShopifyProductIdentity
from app.shopify_drafts import encode
from test_metadata_editor_core import core
from test_order_fulfilment import fulfilment


@pytest.mark.parametrize('compressed',[False,True])
def test_verified_identity_decodes_publishing_evidence(fulfilment,compressed):
    core,oid,lid,*_=fulfilment
    with core[0].app_context():
        line=db.session.get(ShopifyOrderLine,lid)
        identity=ShopifyProductIdentity.query.filter_by(shopify_product_id=line.product_id).one()
        evidence=dict(version=1,variant_id=line.variant_id)
        if compressed:evidence['retained_review']='Verified publishing evidence. '*3000
        identity.media_evidence_json=encode(evidence);db.session.commit()
        assert ('zlib-json-v1' in identity.media_evidence_json)==compressed
        product=work.local_product(line)
        assert product.id==identity.product_id
        result=work.act(lid,'refresh',dict(confirmed='yes',reason='Resolve existing purchased product'),
            'a'*32,actor='test-admin',version=db.session.get(work.OrderWorkLine,lid).version)
        db.session.commit();assert result.state=='ready'
        # Correct decoding must not weaken variant or SKU identity checks.
        original=line.variant_id;line.variant_id='gid://shopify/ProductVariant/999999'
        with pytest.raises(ValueError,match='variant/SKU'):work.local_product(line)
        line.variant_id=original;line.sku='WRONG-SKU'
        with pytest.raises(ValueError,match='variant/SKU'):work.local_product(line)


def test_corrupt_compressed_identity_is_blocked(fulfilment):
    core,oid,lid,*_=fulfilment
    with core[0].app_context():
        line=db.session.get(ShopifyOrderLine,lid)
        identity=ShopifyProductIdentity.query.filter_by(shopify_product_id=line.product_id).one()
        packed=json.loads(encode(dict(version=1,variant_id=line.variant_id,retained_review='x'*70000)))
        packed['sha256']='0'*64;identity.media_evidence_json=json.dumps(packed);db.session.commit()
        with pytest.raises(ValueError,match='identity evidence'):work.local_product(line)
