"""Bounded catalogue-wide relationship browsing and mutual-family proposals."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
from urllib.parse import urlencode

from flask import current_app, url_for
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import aliased, joinedload, selectinload

from app import db
from app.catalogue_images import product_thumbnail_url
from app.collection_identity import collection_display_name
from app.models import Category, Collection, Product, ProductAttribute, ProductRelationship, Tag
from app.product_relationships import MAX_MUTUAL_PRODUCTS, RelationshipValidationError, adopt_relationship_workspace_metadata, preview_mutual_cross_sells, relationship_owner, target_warnings
from app.publishing import projected_publishing_intent


PAGE_SIZES = {25, 50, 100}
RELATIONSHIP_FILTERS = {"cross_sells", "upsells", "both", "none", "broken", "unresolved", "legacy", "explicit", "inactive_targets"}
SORTS = {"title", "sku", "collection", "cross_sells", "upsells", "broken", "updated"}


def parse_relationship_filters(args):
    relationship = (args.get("relationship") or "").strip()
    product_type = (args.get("type") or "").strip()
    intent = (args.get("intent") or "").strip()
    sort = (args.get("sort") or "title").strip()
    if relationship and relationship not in RELATIONSHIP_FILTERS:
        raise ValueError("Unsupported relationship filter")
    if product_type and product_type not in {"simple", "variable"}:
        raise ValueError("Unsupported product type")
    if intent and intent not in {"published", "draft", "unresolved"}:
        raise ValueError("Unsupported publishing intent")
    if sort not in SORTS:
        raise ValueError("Unsupported relationship sort")
    try:
        collection = int(args.get("collection")) if args.get("collection") else None
        page = max(1, int(args.get("page", 1)))
        per_page = int(args.get("per_page", 25))
    except (TypeError, ValueError) as error:
        raise ValueError("Invalid relationship pagination") from error
    if collection is not None and collection < 1:
        raise ValueError("Unsupported collection")
    if per_page not in PAGE_SIZES:
        raise ValueError("Unsupported page size")
    return {"q": (args.get("q") or "").strip()[:191], "relationship": relationship, "collection": collection, "type": product_type, "intent": intent, "active_only": args.get("active_only") in {"1", "true", "on"}, "sort": sort, "page": page, "per_page": per_page}


def _count_subquery(kind=None, broken=False, inactive=False):
    target = aliased(Product)
    query = select(func.count(ProductRelationship.id)).where(ProductRelationship.source_product_id == Product.id).correlate(Product)
    if kind:
        query = query.where(ProductRelationship.relationship_type == kind)
    if broken:
        query = query.where(ProductRelationship.resolved_target_product_id.is_(None))
    if inactive:
        query = query.join(target, ProductRelationship.resolved_target_product_id == target.id).where(target.catalogue_status != "active")
    return query.scalar_subquery()


CROSS_COUNT = _count_subquery("cross_sell")
UPSELL_COUNT = _count_subquery("upsell")
BROKEN_COUNT = _count_subquery(broken=True)
INACTIVE_COUNT = _count_subquery(inactive=True)


def _search_condition(text):
    pattern = f"%{text}%"
    target_match = select(ProductRelationship.id).where(ProductRelationship.source_product_id == Product.id, ProductRelationship.target_sku.ilike(pattern)).exists()
    return or_(Product.title.ilike(pattern), Product.sku.ilike(pattern), Product.product_type.ilike(pattern), Product.catalogue_status.ilike(pattern), Product.collection.has(Collection.name.ilike(pattern)), Product.categories.any(Category.name.ilike(pattern)), Product.tags.any(Tag.name.ilike(pattern)), Product.attributes.any(ProductAttribute.name.ilike(pattern)), Product.attributes.any(ProductAttribute.values.ilike(pattern)), target_match)


def _apply(query, filters):
    if filters["q"]:
        query = query.filter(_search_condition(filters["q"]))
    if filters["collection"]:
        query = query.filter(Product.collection_id == filters["collection"])
    if filters["type"]:
        query = query.filter(Product.product_type == filters["type"])
    if filters["intent"] == "published":
        query = query.filter(Product.published.is_(True))
    elif filters["intent"] == "draft":
        query = query.filter(Product.published.is_(False))
    elif filters["intent"] == "unresolved":
        query = query.filter(Product.published.is_(None))
    if filters["active_only"]:
        query = query.filter(Product.catalogue_status == "active")
    relation = filters["relationship"]
    if relation == "cross_sells": query = query.filter(CROSS_COUNT > 0)
    elif relation == "upsells": query = query.filter(UPSELL_COUNT > 0)
    elif relation == "both": query = query.filter(CROSS_COUNT > 0, UPSELL_COUNT > 0)
    elif relation == "none": query = query.filter(CROSS_COUNT == 0, UPSELL_COUNT == 0)
    elif relation in {"broken", "unresolved"}: query = query.filter(BROKEN_COUNT > 0)
    elif relation == "legacy": query = query.filter(Product.relationship_source_kind == "legacy")
    elif relation == "explicit": query = query.filter(Product.relationship_source_kind == "authored")
    elif relation == "inactive_targets": query = query.filter(INACTIVE_COUNT > 0)
    return query


def _order(filters):
    mappings = {
        "title": (Product.title.asc(), Product.sku.asc()), "sku": (Product.sku.asc(), Product.id.asc()),
        "collection": (Collection.name.asc(), Product.title.asc()), "cross_sells": (CROSS_COUNT.desc(), Product.title.asc()),
        "upsells": (UPSELL_COUNT.desc(), Product.title.asc()), "broken": (BROKEN_COUNT.desc(), Product.title.asc()),
        "updated": (Product.relationships_updated_at.desc(), Product.title.asc()),
    }
    return mappings[filters["sort"]] + (Product.id.asc(),)


def relationship_summary():
    product_count = Product.query.count()
    cross_products = db.session.query(func.count(Product.id)).filter(CROSS_COUNT > 0).scalar() or 0
    upsell_products = db.session.query(func.count(Product.id)).filter(UPSELL_COUNT > 0).scalar() or 0
    both = db.session.query(func.count(Product.id)).filter(CROSS_COUNT > 0, UPSELL_COUNT > 0).scalar() or 0
    unresolved = ProductRelationship.query.filter(ProductRelationship.resolved_target_product_id.is_(None)).count()
    self_links = db.session.query(func.count(ProductRelationship.id)).join(Product, ProductRelationship.source_product_id == Product.id).filter(ProductRelationship.target_sku == Product.sku).scalar() or 0
    return {
        "products": product_count, "with_cross_sells": cross_products, "with_upsells": upsell_products,
        "with_both": both, "with_none": product_count - int(db.session.query(func.count(func.distinct(ProductRelationship.source_product_id))).scalar() or 0),
        "broken_targets": unresolved + self_links,
        "unresolved_targets": unresolved,
        "legacy_products": Product.query.filter_by(relationship_source_kind="legacy").count(),
        "explicit_products": Product.query.filter_by(relationship_source_kind="authored").count(),
        "inactive_targets": db.session.query(func.count(ProductRelationship.id)).join(Product, ProductRelationship.resolved_target_product_id == Product.id).filter(Product.catalogue_status != "active").scalar() or 0,
    }


def _row_view(product, cross_count, upsell_count, broken_count, inactive_count):
    intent = projected_publishing_intent(product.published)
    return {"id": product.id, "title": product.title or "Untitled product", "sku": product.sku or "", "collection": collection_display_name(product.collection) if product.collection else "Unassigned", "product_type": product.product_type or "unknown", "catalogue_status": product.catalogue_status or "unknown", "publishing_intent": intent["compact_label"], "cross_sell_count": int(cross_count or 0), "upsell_count": int(upsell_count or 0), "broken_count": int(broken_count or 0), "inactive_count": int(inactive_count or 0), "source": product.relationship_source_kind or "none", "updated_at": product.relationships_updated_at, "thumbnail": product_thumbnail_url(product), "edit_url": f"{url_for('main.product_detail', product_id=product.id)}#relationships-title", "warnings": target_warnings(product)}


def build_relationship_browser(filters):
    adopt_relationship_workspace_metadata()
    base = db.session.query(Product)
    total = _apply(base, filters).count()
    pages = max(1, math.ceil(total / filters["per_page"])); page = min(filters["page"], pages)
    rows = _apply(db.session.query(Product, CROSS_COUNT.label("cross_count"), UPSELL_COUNT.label("upsell_count"), BROKEN_COUNT.label("broken_count"), INACTIVE_COUNT.label("inactive_count")).outerjoin(Collection, Product.collection_id == Collection.id), filters).options(joinedload(Product.collection), selectinload(Product.images), selectinload(Product.assets)).order_by(*_order(filters)).offset((page - 1) * filters["per_page"]).limit(filters["per_page"]).all()
    def page_url(value):
        params = {key: value_ for key, value_ in filters.items() if value_ not in {None, "", False}}
        params["page"] = value
        return f"{url_for('main.relationships')}?{urlencode(params)}"
    return {"summary": relationship_summary(), "items": [_row_view(*row) for row in rows], "filters": filters, "pagination": {"page": page, "pages": pages, "per_page": filters["per_page"], "total": total, "from": ((page - 1) * filters["per_page"] + 1) if total else 0, "to": min(page * filters["per_page"], total), "previous_url": page_url(page - 1) if page > 1 else None, "next_url": page_url(page + 1) if page < pages else None}}


def family_search(query, *, page=1, per_page=25):
    filters = {"q": query[:191], "relationship": "", "collection": None, "type": "", "intent": "", "active_only": False, "sort": "title", "page": page, "per_page": per_page}
    data = build_relationship_browser(filters)
    pagination = data["pagination"]
    pagination["previous_url"] = url_for("main.relationships_mutual", q=query, page=pagination["page"] - 1) if pagination["page"] > 1 else None
    pagination["next_url"] = url_for("main.relationships_mutual", q=query, page=pagination["page"] + 1) if pagination["page"] < pagination["pages"] else None
    return data


def _proposal_payload(selected_skus):
    preview = preview_mutual_cross_sells(selected_skus)
    products = {row.sku: row for row in Product.query.filter(Product.sku.in_(preview["selected_skus"])).options(joinedload(Product.collection)).all()}
    documents, selected, fingerprints = set(), [], []
    single_variable_documents = set()
    for sku in preview["selected_skus"]:
        product = products.get(sku)
        if not product: continue
        owner = relationship_owner(product); documents.add(owner["relative"])
        if product.collection and product.collection.collection_type == "Single Variable":
            single_variable_documents.add(owner["relative"])
        content = owner["path"].read_bytes() if owner["path"].exists() else b""
        fingerprints.append({"sku": sku, "product_id": product.id, "catalogue_status": product.catalogue_status, "document": owner["relative"], "content_sha256": hashlib.sha256(content).hexdigest()})
        selected.append({"id": product.id, "sku": sku, "title": product.title, "collection": collection_display_name(product.collection) if product.collection else "Unassigned", "catalogue_status": product.catalogue_status, "warnings": target_warnings(product)})
    broken_target_count = ProductRelationship.query.filter(ProductRelationship.source_product_id.in_([item["id"] for item in selected]), ProductRelationship.resolved_target_product_id.is_(None)).count() if selected else 0
    preview.update({"affected_document_count": len(documents), "affected_documents": sorted(documents), "single_variable_document_count": len(single_variable_documents), "selected_products": selected, "skipped_count": 0, "warning_count": sum(len(item["messages"]) for item in preview["warnings"]), "broken_target_count": broken_target_count, "self_links_prevented": len(preview["selected_skus"]), "duplicate_links_prevented": preview["already_linked_count"], "operation_type": "Mutual cross-sells", "rollback_behavior": "All authored documents roll back if any promotion or projection step fails.", "woo_activity": False})
    digest_input = {"version": 1, "selected_skus": preview["selected_skus"], "fingerprints": fingerprints, "new_count": preview["new_count"], "existing_count": preview["already_linked_count"]}
    body = json.dumps(digest_input, sort_keys=True, separators=(",", ":")).encode()
    preview["proposal_digest"] = hmac.new(current_app.config["SECRET_KEY"].encode(), body, hashlib.sha256).hexdigest()
    return preview


def mutual_proposal(selected_skus):
    return _proposal_payload(selected_skus)


def verify_mutual_proposal(selected_skus, digest):
    fresh = _proposal_payload(selected_skus)
    if not isinstance(digest, str) or not hmac.compare_digest(fresh["proposal_digest"], digest):
        raise RelationshipValidationError("The relationship proposal is stale. Review the refreshed preview before confirming.", details=fresh)
    return fresh
