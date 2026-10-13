"""First-use controlled terms over the existing registry, within signed publishing."""
import json
from app import db, shopify_drafts as drafts
from app.models import ShopifyAttributeIdentity, Product
from app.taxonomy_assignments import product_document, resolve
from app.taxonomy_registry import load_configured_registry
from app.shopify_connection import ShopifyError, _gid

NAMESPACE = "$app:tlc_attributes"
READ_SCOPES = {"read_metaobject_definitions", "read_metaobjects"}
WRITE_SCOPES = {"write_metaobject_definitions", "write_metaobjects", "write_products"}
PAYLOADS = {"definition":("metaobjectDefinitionCreate","metaobjectDefinition","MetaobjectDefinition"),
    "field":("metafieldDefinitionCreate","createdDefinition","MetafieldDefinition"),
    "term":("metaobjectCreate","metaobject","Metaobject")}


def plain(value):
    from collections.abc import Mapping
    if isinstance(value,Mapping): return {k:plain(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)): return [plain(v) for v in value]
    return value


def assigned(product):
    doc = product_document(product)
    resolved = resolve(doc)
    registry = load_configured_registry()
    definitions = {a["key"]:plain(a) for a in registry.snapshot.data["attributes"]} if registry.available else {}
    result=[]
    for item in resolved["attributes"]:
        if item["variation"]:
            continue  # effective role, not name-based classification
        definition=item["definition"]
        if not definition or definition["state"]!="active":
            raise ShopifyError("Informational attribute requires an active TLC registry definition: "+item["name"])
        attr=definitions[definition["key"]]
        terms=[]
        for term in item["terms"]:
            value=term["definition"]
            if not value or value["state"]!="active":
                raise ShopifyError("Informational term requires an active registered TLC identity: "+str(term["value"]))
            terms.append(next(plain(t) for t in attr["terms"] if t["key"]==value["key"]))
        public=attr.get("browse_role","internal")!="internal" or any(t.get("browse_role",attr.get("browse_role","internal"))!="internal" for t in terms)
        result.append({"key":attr["key"],"name":attr["name"],"role":attr.get("browse_role","internal"),"public":public,
            "terms":sorted(terms,key=lambda t:t["key"])})
    if len(result)>8 or sum(len(a["terms"]) for a in result)>40:
        raise ShopifyError("Review at most eight informational attributes / forty assigned terms per product.")
    return sorted(result,key=lambda a:a["key"])


def resource(store,key):
    return ShopifyAttributeIdentity.query.filter_by(store_id=store,source_key=key).first()


def valid(kind, remote, spec, source, expected=None, *, allow_access=False):
    if remote is None:
        return None
    keys={"definition":{"id","type","fieldDefinitions","access"},"term":{"id","type","handle","fields"},"field":{"id","key","type","validations","access"}}[kind]
    if not isinstance(remote,dict) or not keys.issubset(remote) or kind in {"definition","field"} and not isinstance(remote["access"],dict):
        raise ShopifyError("Attribute resource readback is incomplete; no equality or retry is authorised.")
    _gid(remote.get("id"),PAYLOADS[kind][2])
    if expected and remote["id"]!=expected:
        raise ShopifyError("Retained attribute resource identity changed; no replacement/duplicate creation.")
    if kind=="definition":
        if remote.get("name")!=spec["name"]:
            raise ShopifyError("Registered attribute label differs; review a mapping rename separately, never replace its identity implicitly.")
        if not remote.get("type","").endswith(spec["type"].removeprefix("$app:")) or {f["key"]:f["type"]["name"] for f in remote["fieldDefinitions"]}!={"name":"single_line_text_field","tlc_key":"single_line_text_field"} or (not allow_access and remote["access"]["storefront"]!=spec["access"]["storefront"]):
            raise ShopifyError("Attribute definition schema/access is incompatible; inspect its retained mapping.")
    elif kind=="term":
        fields={f["key"]:f["value"] for f in remote["fields"]}
        if fields.get("name")!=next(f["value"] for f in spec["fields"] if f["key"]=="name"):
            raise ShopifyError("Registered term label differs; explicit mapping rename required, not a duplicate term Create.")
        if remote["handle"]!=spec["handle"] or not remote["type"].endswith(spec["type"].removeprefix("$app:")) or fields.get("tlc_key")!=source:
            raise ShopifyError("Controlled term lacks its exact TLC/type/handle identity.")
    else:
        validations={v["name"]:v["value"] for v in remote["validations"]}
        if remote["key"]!=spec["key"] or remote["type"]["name"]!="list.metaobject_reference" or validations.get("metaobject_definition_id")!=spec["validations"][0]["value"] or (not allow_access and remote["access"]["storefront"]!=spec["access"]["storefront"]):
            raise ShopifyError("Attribute metafield definition/reference/access is incompatible.")
    return remote


def inspect_resource(kind, key, spec, store, config, adapter):
    kept=resource(store,key)
    if kept and kept.state not in {"verified","absent_verified"}:
        raise ShopifyError("Attribute registration recovery required: "+key+". Inspect/reconcile before any new Create.")
    remote=valid(kind,adapter.attribute_resource(config,kind,spec),spec,key,kept.remote_id if kept else None,allow_access=kind in {"definition","field"})
    if kept and kept.state=="verified" and remote is None:
        raise ShopifyError("Retained attribute resource missing; no automatic recreation.")
    return {"kind":kind,"source":key,"spec":spec,"remote_id":remote["id"] if remote else None,
        "action":"Update storefront access" if remote and kind in {"definition","field"} and remote["access"]["storefront"]!=spec["access"]["storefront"] else "Reuse" if kept and kept.state=="verified" else "Link owned resource" if remote else "Create",
        "before_access":remote["access"]["storefront"] if remote and kind in {"definition","field"} else None,
        "remote_digest":drafts.digest(remote),"mapping_digest":drafts.digest(kept.evidence_json) if kept else None}


def preview(product, store, remote_id, config, shop, adapter):
    assignments=assigned(product)
    if not assignments:
        return []  # no definition/term/API work for unused informational vocabulary
    missing=READ_SCOPES-set(shop["scopes"])
    if missing:
        raise ShopifyError("Missing informational-attribute read scopes: "+", ".join(sorted(missing)))
    schema=adapter.attribute_schema(config)
    if (not {"type","fieldDefinitions"}.issubset({f["name"] for f in (schema.get("definition") or {}).get("inputFields",[])})
            or "compareDigest" not in {f["name"] for f in (schema.get("assignment") or {}).get("inputFields",[])}):
        raise ShopifyError("Configured Shopify API lacks the controlled-attribute/CAS contract.")
    observed={v["key"]:v for v in adapter.attribute_values(config,remote_id,NAMESPACE)} if remote_id else {}
    rows=[]
    for attr in assignments:
        suffix="tlc_"+drafts.digest(attr["key"])[:20]
        visibility="PUBLIC_READ" if attr["public"] else "NONE"
        definition={"name":"TLC · "+attr["name"],"type":"$app:"+suffix,"displayNameKey":"name",
            "access":{"storefront":visibility},"fieldDefinitions":[{"name":"Name","key":"name","type":"single_line_text_field","required":True},
                {"name":"TLC identity","key":"tlc_key","type":"single_line_text_field","required":True}]}
        prefix="attribute:"+attr["key"]
        resources=[inspect_resource("definition",prefix+":definition",definition,store,config,adapter)]
        did=resources[0]["remote_id"]
        field={"name":"TLC · "+attr["name"],"namespace":NAMESPACE,"key":suffix,"ownerType":"PRODUCT",
            "type":"list.metaobject_reference","access":{"storefront":visibility},
            "validations":[{"name":"metaobject_definition_id","value":did or "pending reviewed definition"}]}
        resources.append(inspect_resource("field",prefix+":field",field,store,config,adapter) if did else
            {"kind":"field","source":prefix+":field","spec":field,"remote_id":None,"action":"Create","remote_digest":drafts.digest(None),"mapping_digest":None})
        for term in attr["terms"]:
            key=prefix+":term:"+term["key"]
            spec={"type":definition["type"],"handle":"tlc-"+drafts.digest(key)[:24],
                "fields":[{"key":"name","value":term["name"]},{"key":"tlc_key","value":key}]}
            resources.append(inspect_resource("term",key,spec,store,config,adapter))
        before=observed.get(suffix)
        if before and before["type"]!="list.metaobject_reference":
            raise ShopifyError("Owned informational metafield has an incompatible type; no merchant value is overwritten.")
        desired=[r["remote_id"] for r in resources if r["kind"]=="term"]
        try:
            current=json.loads(before["value"]) if before else []
        except ValueError:
            raise ShopifyError("Informational reference evidence is malformed.") from None
        changed=any(r["action"]!="Reuse" for r in resources) or None in desired or current!=desired
        required=set()
        for item in resources:
            if item["action"] in {"Create","Update storefront access"}:
                required.add({"definition":"write_metaobject_definitions","term":"write_metaobjects","field":"write_products"}[item["kind"]])
        if changed: required.add("write_products")
        missing_writes=sorted(required-set(shop["scopes"]))
        if any(r["action"]=="Update storefront access" for r in resources):
            # Access belongs to the attribute definition, not one assignment;
            # disclosure is required because other products share this definition.
            access_notice="Reviewed access change affects all products referencing this app-owned attribute."
        else:
            access_notice=""
        rows.append({**attr,"field_key":suffix,"resources":resources,"before":before,"changed":changed,
            "desired_ids":desired,"missing_scopes":missing_writes,"access_notice":access_notice,"action":"Clear owned references" if not attr["terms"] else "Update" if changed else "Already matching"})
    return rows


def attach(product, plan, config, shop, adapter, remote_id=None):
    plan["attribute_diagnostics"]=[]
    try:
        plan["attributes"]=preview(product,shop["shop_id"],remote_id,config,shop,adapter)
    except ShopifyError as exc:
        plan["attributes"]=[]; plan["blockers"].append(str(exc))
        plan["attribute_diagnostics"]=[{"name":a["name"],"action":"Required assignment / verification blocked: "+str(exc)} for a in assigned(product)]
    for attr in plan["attributes"]:
        if attr.get("missing_scopes"):
            plan["blockers"].append("Required informational operation for "+attr["name"]+" is blocked. Missing token scopes: "+", ".join(attr["missing_scopes"]))
    plan["attribute_review_digest"]=drafts.digest(plan["attributes"])
    plan["taxonomy_review_digest"]=drafts.digest({"base":plan["taxonomy_review_digest"],"attributes":plan["attribute_review_digest"]})
    plan["fields"] += [{"field":"attributes."+a["name"],"before":(a["before"] or {}).get("value"),
        "after":[t["name"] for t in a["terms"]],"changed":a["changed"]} for a in plan["attributes"]]


def save_resource(row,data,state):
    encoded=json.dumps(data,sort_keys=True)
    if len(encoded.encode())>16384:
        raise ShopifyError("Attribute evidence exceeds its bound.")
    row.evidence_json,row.state=encoded,state
    db.session.add(row); db.session.commit()


def ensure(item,store,config,adapter,operation):
    kind,key,spec=item["kind"],item["source"],item["spec"]
    row=resource(store,key)
    changing_access=item["action"]=="Update storefront access"
    remote=valid(kind,adapter.attribute_resource(config,kind,spec),spec,key,row.remote_id if row else None,allow_access=changing_access)
    if row:
        if row.state=="absent_verified" and remote is None and item["mapping_digest"]==drafts.digest(row.evidence_json):
            pass  # explicit read-confirmed noncreation + a NEW signed product review
        elif row.state!="verified" or remote is None:
            raise ShopifyError("Retained attribute needs explicit read-only reconciliation; no create retry.")
        elif not changing_access:
            return remote["id"]
    if drafts.digest(remote)!=item["remote_digest"]:
        raise ShopifyError("Attribute candidate changed since signed review; rebuild before registration.")
    if row is None:
        row=ShopifyAttributeIdentity(store_id=store,source_key=key,state="pending",operation_id=operation,evidence_json='{"version":1}')
    if changing_access:
        row.remote_id=remote["id"]  # exact retained target durable BEFORE access mutation
    row.operation_id=operation
    save_resource(row,{"version":1,"kind":kind,"spec":spec,"source":key,"before_access":item.get("before_access")},"pending")
    try:
        if changing_access:
            save_resource(row,json.loads(row.evidence_json),"updating_access")
            payload=drafts.payload_result(adapter.update_attribute_access(config,kind,remote["id"],spec),"metaobjectDefinitionUpdate" if kind=="definition" else "metafieldDefinitionUpdate")
            returned=(payload.get("metaobjectDefinition" if kind=="definition" else "updatedDefinition") or {}).get("id")
            if returned!=remote["id"]:
                raise ShopifyError("Access update receipt identity differs; reconcile retained resource.")
            remote=valid(kind,adapter.attribute_resource(config,kind,spec),spec,key,row.remote_id)
        if remote is None:
            save_resource(row,json.loads(row.evidence_json),"creating")
            name,receipt,model=PAYLOADS[kind]
            payload=drafts.payload_result(adapter.create_attribute_resource(config,kind,spec),name)
            row.remote_id=_gid((payload.get(receipt) or {}).get("id"),model); db.session.commit()
            remote=valid(kind,adapter.attribute_resource(config,kind,spec),spec,key,row.remote_id)
        if remote is None:
            raise ShopifyError("Attribute receipt is not independently verified.")
        row.remote_id=remote["id"]
        save_resource(row,{"version":1,"kind":kind,"spec":spec,"source":key,"observed":remote},"verified")
        return row.remote_id
    except Exception:
        db.session.rollback(); save_resource(row,json.loads(row.evidence_json),"uncertain")
        raise ShopifyError("Attribute registration uncertain: "+key+". Reconcile by deterministic owned identity; no retry.") from None


def apply(row,data,config,adapter):
    entries=data["plan"].get("attributes",[])
    if not entries:
        return
    wanted=assigned(db.session.get(Product,row.product_id))
    if [{k:a[k] for k in ("key","name","role","public","terms")} for a in entries]!=wanted or config.fingerprint()!=data["plan"]["config_digest"]:
        raise ShopifyError("Informational source/configuration changed; no additional write.")
    for attr in entries:
        if not attr["changed"]:
            continue
        data["stage"]="attribute_registration"; drafts.save(row,data)
        definition=attr["resources"][0]
        did=ensure(definition,row.store_id,config,adapter,row.operation_id)
        field=attr["resources"][1]
        field["spec"]["validations"][0]["value"]=did
        ensure(field,row.store_id,config,adapter,row.operation_id)
        ids=[ensure(term,row.store_id,config,adapter,row.operation_id) for term in attr["resources"][2:]]
        current={v["key"]:v for v in adapter.attribute_values(config,row.shopify_product_id,NAMESPACE)}.get(attr["field_key"])
        if current!=attr["before"]:
            raise ShopifyError("Informational metafield changed after review; retain prior stages and reconcile, no blind overwrite.")
        attr["desired_ids"]=ids
        data["stage"]="attribute_assignment"; drafts.save(row,data)  # durable BEFORE CAS mutation
        value={"ownerId":row.shopify_product_id,"namespace":NAMESPACE,"key":attr["field_key"],"type":"list.metaobject_reference",
            "value":json.dumps(ids),"compareDigest":current["compareDigest"] if current else None}
        drafts.payload_result(adapter.set_attributes(config,[value]),"metafieldsSet")
        data.setdefault("completed_steps",[]).append("attribute:"+attr["key"]); drafts.save(row,data)


def verify(plan,config,adapter,product_id):
    entries=plan.get("attributes",[])
    if not entries:
        return
    current={v["key"]:v for v in adapter.attribute_values(config,product_id,NAMESPACE)}
    for attr in entries:
        desired=[]
        for item in attr["resources"]:
            kept=resource(plan["store_id"],item["source"])
            if not kept or kept.state!="verified" or valid(item["kind"],adapter.attribute_resource(config,item["kind"],json.loads(kept.evidence_json)["spec"]),json.loads(kept.evidence_json)["spec"],item["source"],kept.remote_id) is None:
                raise ShopifyError("Informational resource not independently verified; reconcile mapping first.")
            if item["kind"]=="term": desired.append(kept.remote_id)
        actual=current.get(attr["field_key"])
        if not actual or actual["type"]!="list.metaobject_reference" or json.loads(actual["value"])!=desired:
            raise ShopifyError("Informational references differ from retained reviewed assignments; no automatic write replay.")


def reconcile(identity,adapter,config):
    data=json.loads(identity.evidence_json)
    observed=valid(data["kind"],adapter.attribute_resource(config,data["kind"],data["spec"]),data["spec"],identity.source_key,identity.remote_id,allow_access=bool(data.get("before_access")))
    if observed and data.get("before_access"):
        actual=observed["access"]["storefront"]
        if actual not in {data["before_access"],data["spec"]["access"]["storefront"]}:
            raise ShopifyError("Storefront access differs from both reviewed states; explicit conflict review required.")
        data["spec"]["access"]["storefront"]=actual  # read-only outcome, not automatic retry
    if observed is None:
        if identity.remote_id or adapter.attribute_resource(config,data["kind"],data["spec"]) is not None:
            raise ShopifyError("Attribute outcome unresolved or conflicting; no duplicate Create.")
        # Exact app-owned type/handle/key is unique remotely. Two successful null
        # reads resolve noncreation; any retry still needs a new signed Review.
        save_resource(identity,{**data,"absent_at":drafts.now().isoformat()},"absent_verified")
        return
    identity.remote_id=observed["id"]
    save_resource(identity,{**data,"observed":observed},"verified")


def remaining_review(plan,config,adapter,product_id):
    """Explicit recovery only: prove incomplete stages, not false full equality."""
    entries=plan.get("attributes",[])
    if not entries:
        raise ShopifyError("No reviewed attribute stages exist to recover.")
    current={v["key"]:v for v in adapter.attribute_values(config,product_id,NAMESPACE)}
    for attr in entries:
        desired=[]; unfinished=False
        for item in attr["resources"]:
            kept=resource(plan["store_id"],item["source"])
            if kept and kept.state not in {"verified","absent_verified"}:
                raise ShopifyError("Resolve the pending/uncertain informational resource first: "+item["source"])
            if kept and kept.state=="verified":
                evidence=json.loads(kept.evidence_json)
                observed=valid(item["kind"],adapter.attribute_resource(config,item["kind"],evidence["spec"]),evidence["spec"],item["source"],kept.remote_id)
                if observed is None: raise ShopifyError("Retained informational resource disappeared; identity review required.")
                if item["kind"]=="term": desired.append(kept.remote_id)
            else:
                unfinished=True
        actual=current.get(attr["field_key"])
        if not unfinished and actual and actual["type"]=="list.metaobject_reference" and json.loads(actual["value"])==desired:
            continue
        if actual!=attr["before"]:
            raise ShopifyError("Informational assignment differs from both original before/desired evidence. Resolve the conflict; no automatic replay.")
    return True  # base verifier still has to prove fields, exact IDs, media and memberships


def local_overview():
    """Bounded local usage/registration display, including unused filter/internal terms."""
    registry=load_configured_registry()
    if not registry.available: return {"rows":[],"partial":False}
    parents=Product.query.filter_by(catalogue_status="active").order_by(Product.id).limit(501).all()
    usage={}
    for product in parents[:500]:
        try:
            for attr in assigned(product):
                prefix="attribute:"+attr["key"]
                usage.setdefault(prefix+":definition",set()).add(product.sku)
                for term in attr["terms"]:
                    usage.setdefault(prefix+":term:"+term["key"],set()).add(product.sku)
        except ShopifyError:
            continue  # unresolved values remain explicit product-review blockers
    mappings={}
    for kept in ShopifyAttributeIdentity.query.all(): mappings.setdefault(kept.source_key,[]).append(kept)
    rows=[]
    for attr in registry.snapshot.data["attributes"]:
        prefix="attribute:"+attr["key"]
        for key,name,role in [(prefix+":definition",attr["name"],attr.get("browse_role","internal")),
            *[(prefix+":term:"+term["key"],attr["name"]+" / "+term["name"],term.get("browse_role",attr.get("browse_role","internal"))) for term in attr["terms"]]]:
            known=mappings.get(key,[]); kept=known[0] if len(known)==1 else None
            rows.append({"key":key,"name":name,"role":role,"products":sorted(usage.get(key,set())),
                "state":kept.state if kept else "defined locally / not registered","identity_id":kept.id if kept else None,"remote_id":kept.remote_id if kept else None})
    return {"rows":rows,"partial":len(parents)>500}
