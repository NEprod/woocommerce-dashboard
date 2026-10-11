"""Shared informational Woo reconciliation. Never a write authorization.

The one-worker deployment shares a bounded cache and a nonblocking refresh lock
across its request threads. Durable proof lives only on existing trusted links.
"""
from collections import OrderedDict
from copy import deepcopy
from datetime import UTC, datetime
import json
from threading import Lock
from time import monotonic

from flask import current_app

from app import db
from app.models import Product, CatalogueOperation, WooProductIdentity, WooVariationIdentity
from app import woo_publish_preview as preview
from app.taxonomy_assignments import product_document
from app.woocommerce_connection import ReadOnlyWooClient, WooConnectionError, effective_configuration

VERSION = 1
MAX_AGE = 600
MAX_RECORD_BYTES = 4096
MAX_READS = 60
LABELS = {"unknown": "Unknown / not checked", "local_only": "Local only", "no_change": "No Change",
          "update": "Update available", "remote_drift": "Remote drift", "blocked": "Blocked",
          "link_candidate": "Link required", "recovery_required": "Recovery required"}


def _runtime():
    return current_app.extensions.setdefault("woo_live_state", {
        "lock": Lock(), "states": OrderedDict(), "reads": OrderedDict()})


def _put(cache, key, value, limit):
    cache[key] = value
    cache.move_to_end(key)
    while len(cache) > limit:
        cache.popitem(last=False)


def _now():
    return datetime.now(UTC).isoformat()


def _age(timestamp):
    try:
        age = (datetime.now(UTC) - datetime.fromisoformat(timestamp)).total_seconds()
        return age if age >= 0 else float("inf")
    except (ValueError, TypeError):
        return float("inf")


def source_digest(product):
    # Reuse the Preview source graph, not its identity/payload hashes. Authored
    # source is included even for legacy products and before a new Update scan.
    state = preview._local_state([product], {}, {})[0]
    state.pop("identity", None)
    state.pop("variation_identities", None)
    if state.get("explicit_taxonomy"):
        state["explicit_taxonomy"] = dict(state["explicit_taxonomy"])
        # Unrelated vocabulary additions do not change this product's contract.
        state["explicit_taxonomy"].pop("registry_digest", None)
        state["explicit_taxonomy"].pop("digest", None)
    return preview._digest({"version": VERSION, "state": state, "authored": product_document(product)})


def _dependencies(product, store):
    ids = {edge.resolved_target_product_id for edge in product.relationship_edges}
    ids.add(product.id)
    parents = sorted((r.product_id, r.woo_product_id, r.verification_state, r.sku, r.stable_identity)
        for r in WooProductIdentity.query.filter(WooProductIdentity.product_id.in_(ids),
                                                 WooProductIdentity.store_key == store).all())
    children = sorted((r.variation_id, r.woo_parent_product_id, r.woo_variation_id, r.verification_state, r.sku)
        for r in WooVariationIdentity.query.filter_by(product_id=product.id, store_key=store).all())
    return preview._digest([parents, children])


def _complete_publish(op, summary, result, product, identity):
    """Only terminal full-workflow evidence, never Pass 1 timestamps or Preview."""
    return bool(product and _trusted(product, identity)
        and op.status == "succeeded" and op.recovery_state in (None, "none", "")
        and op.finished_at and op.started_at and op.finished_at >= op.started_at
        and not op.error and op.products_failed == 0
        and summary.get("failed_products") == 0
        and summary.get("pending_relationship_count") == 0
        and summary.get("recovery_required") is False and not summary.get("failure")
        and not summary.get("pending_relationships")
        and result.get("status") == "verified" and not result.get("error")
        and result.get("sku") == product.sku
        and result.get("woo_id") == identity.woo_product_id
        and isinstance(result.get("variations"), list)
        and isinstance(result.get("relationships"), dict)
        and result["relationships"].get("pending") == []
        and isinstance(result["relationships"].get("payload"), dict))


def _covers_recovery(old, summary, result, success_result):
    # This derives product health only. Retained taxonomy candidates remain
    # reviewable in the unchanged historical operation, not implicitly cleaned up.
    if summary.get("diagnostics_truncated") or any(
            error.get("uncertain") for error in summary.get("woo_errors", [])):
        return False
    return bool(old.finished_at and old.started_at and old.finished_at >= old.started_at
        and result.get("sku") == success_result.get("sku")
        and result.get("woo_id") in (None, success_result.get("woo_id"))
        and not result.get("diagnostic", {}).get("uncertain"))


def recovery_operation(product_id, store):
    # Derive current health without rewriting immutable operation history.
    operations = CatalogueOperation.query.filter_by(
        operation_type="woo_controlled_publish").order_by(CatalogueOperation.started_at.desc())
    successes = []
    product = db.session.get(Product, product_id)
    identity = WooProductIdentity.query.filter_by(product_id=product_id, store_key=store).first()
    for op in operations:
        matched = False
        try:
            scope = json.loads(op.scope or "{}")
            if scope.get("store_identity") != store or product_id not in scope.get("product_ids", []):
                continue
            matched = True
            summary = scope.get("operation_summary", {})
            results = [r for r in summary.get("product_results", []) if r.get("product_id") == product_id]
            result = results[0] if len(results) == 1 else {}
            if _complete_publish(op, summary, result, product, identity):
                successes.append((op, result))
            if op.recovery_state in (None, "none", ""):
                continue
            if result.get("status") not in {"verified", "verified_with_warnings"}:
                if any(success.started_at > op.finished_at
                       and _covers_recovery(op, summary, result, success_result)
                       for success, success_result in successes if op.finished_at):
                    continue
                return op.id
        except (TypeError, ValueError, AttributeError):
            if matched and op.recovery_state not in (None, "none", ""):
                return op.id
            continue
    return None


def _trusted(product, identity):
    return bool(identity and identity.woo_product_id and identity.verification_state == "verified"
                and identity.sku == product.sku
                and identity.stable_identity == (product.source_relpath or f"product:{product.id}"))


def _saved(identity):
    try:
        raw = identity.live_state_json if identity else None
        if not raw or len(raw.encode()) > MAX_RECORD_BYTES:
            return {}
        value = json.loads(raw)
        if (value.get("version") == VERSION and value.get("builder") == preview.BUILDER_VERSION
                and value.get("woo_id") == identity.woo_product_id):
            return value
    except (ValueError, TypeError, AttributeError):
        pass
    return {}


def _record(identity):
    value = _saved(identity)
    if (value.get("state") == "no_change" and all(isinstance(value.get(k), str) and len(value[k]) == 64
            for k in ("source", "dependencies", "managed", "remote"))
            and _age(value.get("checked_at")) != float("inf")
            and _age(value.get("verified_at")) != float("inf")):
        return value
    return {}


def _view(state, *, checked_at=None, verified_at=None, message="", refresh=True, recovery_id=None):
    return dict(state=state, label=LABELS[state], checked_at=checked_at, verified_at=verified_at,
                message=message, needs_refresh=refresh, recovery_id=recovery_id)


def product_state(product, *, store=None, identity=None):
    """Local facts and last verified proof only: no Woo request, no Preview cache."""
    try:
        store = store or preview.store_identity()["key"]
        identity = identity or WooProductIdentity.query.filter_by(store_key=store, product_id=product.id).first()
        recovery = recovery_operation(product.id, store)
        if recovery:
            return _view("recovery_required", message="Review the existing recovery operation before retrying.",
                         refresh=False, recovery_id=recovery)
        running = CatalogueOperation.query.filter_by(operation_type="woo_controlled_publish", status="running").first()
        if running:
            return _view("unknown", message="Publishing is running; verification is not yet complete.", refresh=False)
        source = source_digest(product)
        dependencies = _dependencies(product, store)
    except (ValueError, WooConnectionError) as error:
        return _view("blocked", message=str(error)[:500], refresh=False)
    proof = _record(identity) if _trusted(product, identity) else {}
    observation = _saved(identity).get("observation", {}) if _trusted(product, identity) else {}
    if not isinstance(observation, dict):
        observation = {}
    cached = _runtime()["states"].get((store, product.id), {})
    if cached.get("source") == source and cached.get("dependencies") == dependencies and _age(cached.get("fresh_from", cached.get("attempted_at"))) < MAX_AGE:
        return deepcopy(cached["view"])
    if proof:
        if proof["source"] != source or proof["dependencies"] != dependencies:
            return _view("update", verified_at=proof["verified_at"],
                         message="Local product changed after the last verified Woo sync.", refresh=False)
    if (observation.get("source") == source and observation.get("dependencies") == dependencies
            and observation.get("state") in LABELS and observation.get("state") != "no_change"):
        fresh = _age(observation.get("checked_at")) < MAX_AGE
        return _view(observation["state"], checked_at=observation.get("checked_at"),
                     verified_at=proof.get("verified_at"), message=observation.get("message", "")[:500]
                     + ("" if fresh else " Last known state is stale; checking Woo…"), refresh=not fresh)
    if proof:
        fresh = _age(proof.get("checked_at")) < MAX_AGE
        return _view("no_change", checked_at=proof["checked_at"], verified_at=proof["verified_at"],
                     message="Verified managed state." if fresh else "Last verified state is stale; checking Woo…", refresh=not fresh)
    if identity and not _trusted(product, identity):
        return _view("recovery_required", message="Stored Woo identity needs review.", refresh=False)
    return _view("unknown" if identity else "local_only", message="Not yet checked." if identity else "No current-store link. An exact Woo match has not yet been checked.")


def _persist(product, identity, source, managed, remote, *, observed_at=None):
    if not _trusted(product, identity):
        return
    record = {"version": VERSION, "builder": preview.BUILDER_VERSION, "woo_id": identity.woo_product_id,
              "source": source, "dependencies": _dependencies(product, identity.store_key),
              "managed": preview._digest(managed), "remote": preview._digest(remote),
              "state": "no_change", "checked_at": observed_at or _now(), "verified_at": observed_at or _now()}
    encoded = json.dumps(record, separators=(",", ":"))
    if len(encoded.encode()) > MAX_RECORD_BYTES:
        raise ValueError("Live verification evidence exceeded its safe bound.")
    identity.live_state_json = encoded


def record_verified_publish(product, plan, result, source_before, store):
    """Called after ALL managed stages; pending relationships are not No Change."""
    identity = WooProductIdentity.query.filter_by(store_key=store, product_id=product.id).first()
    runtime = _runtime()
    runtime["states"].pop((store, product.id), None)
    runtime["reads"].clear()
    if identity:
        identity.live_state_json = None
    try:
        current_source = source_digest(product)
    except ValueError:
        return
    if (result.get("status") != "verified" or result.get("relationships", {}).get("pending")
            or len(result.get("variations", [])) != len(plan.get("variations", []))
            or not source_before or source_before != current_source):
        return
    children = WooVariationIdentity.query.filter_by(product_id=product.id, store_key=store).order_by(WooVariationIdentity.variation_id).all()
    selected = {v["id"] for v in plan.get("variations", [])}
    children = [v for v in children if v.variation_id in selected]
    if len(children) != len(selected) or not _trusted(product, identity):
        return
    _persist(product, identity, source_before,
             {"parent": identity.last_published_digest,
              "children": [(v.woo_variation_id, v.last_published_digest) for v in children],
              "relationships": result.get("relationships", {}).get("payload")},
             {"parent": identity.last_remote_digest,
              "children": [(v.woo_variation_id, v.last_remote_digest) for v in children],
              "relationships": result.get("relationships", {}).get("payload")})


def invalidate_products(product_ids, store):
    """A write attempt must not leave pre-write proof looking current on failure."""
    runtime = _runtime()
    for product_id in product_ids:
        runtime["states"].pop((store, product_id), None)
    runtime["reads"].clear()
    for row in WooProductIdentity.query.filter(WooProductIdentity.store_key == store,
            WooProductIdentity.product_id.in_(product_ids)).all():
        row.live_state_json = None
    # The existing operation transaction owns persistence, not this helper.


class _Reads:
    """GET-only facade; bounded shared response reuse is never used by publishing."""
    def __init__(self, client, store, force):
        self.client, self.store, self.force = client, store, force
        self.base_url = client.base_url
        self.request_count = 0
        self.started = monotonic()
        self.observed_at = _now()
        self.failure = None

    def request_json(self, method, url, **kwargs):
        if method != "GET" or kwargs.get("json_body") is not None:
            raise WooConnectionError("unsafe_method", "Live reconciliation permits GET only.")
        cache = _runtime()["reads"]
        key = (self.store, url, bool(kwargs.get("authenticated")))
        cached = cache.get(key)
        if not self.force and cached and _age(cached[0]) < MAX_AGE:
            self.observed_at = min(self.observed_at, cached[0])
            return deepcopy(cached[1]), None
        if self.request_count >= MAX_READS or monotonic() - self.started > 20:
            self.failure = WooConnectionError("read_budget", "Read budget reached; refresh this product separately.")
            raise self.failure
        self.request_count += 1
        try:
            data, response = self.client.request_json(method, url, **kwargs)
        except WooConnectionError as error:
            # Canonical helpers may absorb failed reads into incomplete plans.
            # A confirmed 404 still belongs to normal identity classification.
            if error.category != "not_found":
                self.failure = error
            raise
        # Responses can be large; don't retain arbitrary discovery bodies.
        if len(json.dumps(data, default=str).encode()) <= 262144:
            _put(cache, key, (_now(), deepcopy(data)), 128)
        return data, response


def refresh_product(product, *, force=False, client=None):
    store = preview.store_identity()["key"]
    previous = product_state(product, store=store)
    identity = WooProductIdentity.query.filter_by(store_key=store, product_id=product.id).first()
    if previous["recovery_id"] or (identity and not _trusted(product, identity)):
        return _view("recovery_required", message=previous["message"], refresh=False,
                     recovery_id=previous["recovery_id"])
    if not force and not previous["needs_refresh"]:
        return previous
    runtime = _runtime()
    from app.utils.operation_control import get_active_operation
    if get_active_operation():
        return dict(previous, message="An operation is running. Refresh Woo after it finishes.", busy=True)
    if not runtime["lock"].acquire(blocking=False):
        return dict(previous, message="Another Woo check is running. Refresh again shortly.", busy=True)
    try:
        source = source_digest(product)
        dependencies = _dependencies(product, store)
        # A second tab arriving after the first completed reuses its proof/cache.
        previous = product_state(product, store=store)
        if previous["recovery_id"]:
            return previous
        if not force and not previous["needs_refresh"]:
            return previous
        reader = _Reads(client or ReadOnlyWooClient(effective_configuration()), store, force)
        try:
            plan = preview.generate_publish_plan({"kind": "product", "product_id": product.id},
                                                client=reader, record_operation=False)
            item = plan["products"][0]
            if reader.failure:
                raise reader.failure
            media = item["media"]
            if any(image["state"] == "unreachable" for image in media["parent"] +
                   [image for row in media["variations"] for image in row["images"]]):
                raise WooConnectionError("incomplete_read", "Media identity reads are incomplete; refresh again.")
            state = item["action"]
            state = "local_only" if state == "create" else state
            message = "; ".join(item["blockers"])[:500]
            # Preview's parent/child comparison is canonical. Pass 2 is a separate
            # managed stage, so compare its ordered assignments independently too.
            relationship = item["relationships"]
            if relationship["pending_count"]:
                state, message = "blocked", "Relationship targets still require verified Woo identities."
            elif state == "no_change":
                remote = preview.PreviewWooReader(reader, plan["capability"]["selected_namespace"]).get(f"products/{item['woo_id']}")
                if any(remote.get(k, []) != relationship["payload"][k] for k in ("cross_sell_ids", "upsell_ids")):
                    state = "update"
                if previous["state"] == "update" and previous["message"].startswith("Local product changed"):
                    state, message = "update", "Local source changed. Review the local Update scan and Publish Preview before claiming a new verified baseline."
            if state == "update" and item.get("drift") == "remote_drift":
                state = "remote_drift"
            db.session.expire_all()
            if source != source_digest(product) or dependencies != _dependencies(product, store):
                return _view("update", message="Local state changed during the check. Refresh again.")
            if recovery_operation(product.id, store):
                return product_state(product, store=store)
            identity = WooProductIdentity.query.filter_by(store_key=store, product_id=product.id).first()
            if identity and not _trusted(product, identity):
                return _view("recovery_required", message="Stored Woo identity needs review.", refresh=False)
            if state == "no_change":
                _persist(product, identity, source, {"parent": item["payload"], "children": item["variations"], "relationships": relationship},
                         {"parent": item["remote_managed"], "children": [v["remote_managed"] for v in item["variations"]], "relationships": relationship["payload"]}, observed_at=reader.observed_at)
                db.session.commit()
            view = _view(state, checked_at=reader.observed_at, verified_at=reader.observed_at if state == "no_change" else previous["verified_at"], message=message, refresh=False)
            if state != "no_change" and _trusted(product, identity):
                saved = _saved(identity) or {"version": VERSION, "builder": preview.BUILDER_VERSION, "woo_id": identity.woo_product_id}
                saved["observation"] = dict(state=state, checked_at=reader.observed_at, source=source,
                                            dependencies=dependencies, message=message[:500])
                encoded = json.dumps(saved, separators=(",", ":"))
                if len(encoded.encode()) <= MAX_RECORD_BYTES:
                    identity.live_state_json = encoded
                    db.session.commit()
        except (preview.PreviewError, WooConnectionError, ValueError) as error:
            # A failed refresh must not erase a previously verified baseline.
            view = dict(previous, needs_refresh=False,
                        message=f"Unable to refresh — showing last known state. {str(error)[:350]}")
            if view["state"] == "recovery_required":
                view["state"], view["label"] = "unknown", "Unable to refresh"
            if view["state"] == "unknown":
                view["label"] = "Unable to refresh"
            if (isinstance(error, preview.PreviewError) and error.category == "blocked"
                    and not isinstance(error.__cause__, WooConnectionError) and not reader.failure):
                view = _view("blocked", message=str(error)[:500], refresh=False)
        if recovery_operation(product.id, store):
            return product_state(product, store=store)
        _put(runtime["states"], (store, product.id), dict(source=source, dependencies=dependencies,
             attempted_at=_now(), fresh_from=_now() if view["message"].startswith("Unable to refresh") else view.get("checked_at") or _now(),
             view=deepcopy(view)), 1000)
        return view
    finally:
        runtime["lock"].release()
