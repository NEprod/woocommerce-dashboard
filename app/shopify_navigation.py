"""Read-only proposal over registry assignments; no Shopify menu mutation."""
import json
import os
from app import db, shopify_drafts as drafts
from app.models import Product, ShopifyProductIdentity, ShopifyProductComparison, ShopifyCollectionIdentity
from app.shopify_taxonomy import sources, assignments, mapping
from app.taxonomy_assignments import product_document
from app.shopify_comparison import recent, MAX_AGE, store_summary
from app.shopify_connection import ShopifyAdapter, ShopifyError, _gid
from app.utils.operation_control import acquire_catalogue_operation, finish_catalogue_operation

BASELINE = ["Shop","Occasions","Recipients","Age / Milestone","Collections","Personalised Gifts","New In","Best Sellers","About","Contact"]


def preview():
    """GET never requests remote state. Unknown/stale eligibility is hidden safely."""
    rows={s["key"]:dict(s,local=set(),verified=set(),published=set(),unknown=set(),primary=set()) for s in sources()}
    identities={r.product_id:r for r in ShopifyProductIdentity.query.filter_by(state="verified").all()}
    comparisons={(r.store_id,r.source_identity):json.loads(r.summary_json) for r in ShopifyProductComparison.query.all()}
    parents=Product.query.filter_by(catalogue_status="active").order_by(Product.id).limit(501).all()
    try: config=drafts.configuration().fingerprint()
    except Exception: config=None
    mapped={}
    for item in ShopifyCollectionIdentity.query.all():
        if item.state=="verified": mapped.setdefault(item.source_key,[]).append(item)
    for product in parents[:500]:
        groups, blockers=assignments(product)
        identity=identities.get(product.id)
        proof=(comparisons.get((identity.store_id,product.source_relpath),{}) if identity else {}).get("online_store",{})
        eligible=bool(not blockers and identity and proof.get("config")==config and proof.get("remote_id")==identity.shopify_product_id and proof.get("publication")==os.environ.get("SHOPIFY_ONLINE_STORE_PUBLICATION_ID")
            and recent(proof.get("checked_at"),MAX_AGE) and proof.get("status")=="ACTIVE" and proof.get("published") is True)
        for group in groups:
            row=rows[group["key"]]; row["local"].add(product.sku)
            if identity:
                row["verified"].add(product.sku)
                if eligible: row["published"].add(product.sku)
                elif not proof or not recent(proof.get("checked_at"),MAX_AGE): row["unknown"].add(product.sku)
            if product_document(product).get("primary_category") and group["key"]=="categories:"+product_document(product)["primary_category"]:
                row["primary"].add(product.sku)
    result=[]
    for row in rows.values():
        linked=mapped.get(row["key"],[])
        remote=linked[0].remote_id if len(linked)==1 else None
        result.append({**row,**{k:sorted(row[k]) for k in ("local","verified","published","unknown","primary")},
            "remote_id":remote,"visible":bool(remote and row["published"]),"registration":"Verified / last-known" if remote else "Defined locally / not registered"})
    return {"baseline":BASELINE,"destinations":result,"truncated":len(parents)>500,
        "publication":os.environ.get("SHOPIFY_ONLINE_STORE_PUBLICATION_ID"),"policy":"Only fresh verified ACTIVE + exact Online Store publication + mapped destinations are proposed. Empty/unknown destinations remain local and hidden."}


def check(*, after=0, adapter=None):
    if type(after) is not int or after<0:
        raise ShopifyError("Invalid navigation product cursor.")
    publication=_gid(os.environ.get("SHOPIFY_ONLINE_STORE_PUBLICATION_ID"),"Publication")
    adapter=adapter or ShopifyAdapter()
    lease=acquire_catalogue_operation("shopify_status_refresh",{"mode":"navigation_read_only","after":after})
    try:
        config=drafts.configuration(); shop=adapter.test_connection(config)
        if "read_publications" not in shop["scopes"] or "read_products" not in shop["scopes"]:
            raise ShopifyError("Navigation eligibility requires read_publications and read_products; no publication/menu write scopes requested.")
        rows=ShopifyProductIdentity.query.filter_by(store_id=shop["shop_id"],state="verified").filter(ShopifyProductIdentity.id>after).order_by(ShopifyProductIdentity.id).limit(26).all()
        if rows:
            response=adapter.navigation_eligibility(config,[r.shopify_product_id for r in rows[:25]],publication)
            channel=response.get("publication")
            if not channel or channel.get("id")!=publication or channel.get("name")!="Online Store" or channel.get("app",{}).get("title")!="Online Store":
                raise ShopifyError("Configured publication is not the verified Online Store channel. No storefront eligibility is claimed.")
            observed={p["id"]:p for p in response["nodes"] if p}
            for identity in rows[:25]:
                remote=observed.get(identity.shopify_product_id)
                if not remote or (remote.get("metafield") or {}).get("value")!=drafts.evidence(identity)["plan"]["nonce"] or type(remote.get("publishedOnPublication")) is not bool:
                    raise ShopifyError("Navigation product identity/publication evidence is incomplete. Use normal exact-identity reconciliation.")
                comparison=ShopifyProductComparison.query.filter_by(store_id=identity.store_id,source_identity=identity.source_identity).first()
                if comparison:
                    data=json.loads(comparison.summary_json)
                    data["online_store"]={"publication":publication,"remote_id":identity.shopify_product_id,"published":remote["publishedOnPublication"],"status":remote["status"],"checked_at":drafts.now().isoformat(),"config":config.fingerprint()}
                    store_summary(comparison,data)
        finish_catalogue_operation(lease.id,status="succeeded",operation_summary={"read_only":True,"checked":min(len(rows),25)})
        return rows[24].id if len(rows)>25 else None
    except Exception:
        finish_catalogue_operation(lease.id,status="failed",error="Navigation eligibility not established. No Shopify write occurred.")
        raise
