"""Soft lifecycle reconciliation for explicitly authoritative catalogue scopes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app import db
from app.models import CatalogueOperationItem, Product


@dataclass(frozen=True)
class AuthoritativeScope:
    kind: str
    seen_source_relpaths: frozenset[str]
    collection_source_relpath: str | None = None


def _utcnow():
    return datetime.now(UTC).replace(tzinfo=None)


def authoritative_scope(
    operation_type,
    *,
    seen_source_relpaths,
    collection_source_relpath=None,
    complete=True,
):
    """Return a scope only for approved, successfully resolved exhaustive work."""

    if not complete:
        return None
    if operation_type in {"full", "reconstruction"}:
        return AuthoritativeScope(
            "catalogue", frozenset(seen_source_relpaths)
        )
    if operation_type == "shared_collection_update" and collection_source_relpath:
        return AuthoritativeScope(
            "collection",
            frozenset(seen_source_relpaths),
            collection_source_relpath,
        )
    return None


def reconcile_authoritative_products(scope, *, operation_id=None, failure_injector=None):
    """Mark unseen active products missing within one confirmed exhaustive scope."""

    if scope is None:
        return {"products_missing": 0}

    db.session.rollback()
    missing = []
    now = _utcnow()
    with db.session.begin():
        query = Product.query.filter_by(catalogue_status="active")
        if scope.kind == "collection":
            query = query.filter(
                Product.collection.has(
                    source_relpath=scope.collection_source_relpath
                )
            )
        missing = [
            product
            for product in query.all()
            if product.source_relpath not in scope.seen_source_relpaths
        ]
        for product in missing:
            preserved_local_updated_at = product.local_updated_at
            product.catalogue_status = "missing"
            product.missing_at = now
            product.local_updated_at = preserved_local_updated_at
            if operation_id:
                db.session.add(
                    CatalogueOperationItem(
                        operation_id=operation_id,
                        source_path=product.source_relpath,
                        sku=product.sku,
                        status="missing",
                        database_state="committed",
                        marker_state="not_applicable",
                        finished_at=now,
                    )
                )
        if failure_injector:
            failure_injector("product_reconciliation", None)
    return {"products_missing": len(missing)}
