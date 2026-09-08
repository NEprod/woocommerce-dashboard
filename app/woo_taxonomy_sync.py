"""Bounded, reviewed registry definition sync. Never publishes products or deletes."""
from datetime import UTC, datetime
import hashlib
import html
import json
import re
import time
from urllib.parse import urlencode

from app import db, taxonomy_workspace as registry
from app.models import WooTaxonomyIdentity, CatalogueOperation
from app.taxonomy_assignments import normal
from app.woocommerce_connection import (effective_configuration, ReadOnlyWooClient,
    PublisherWooClient, WooConnectionError)
from app.woo_publish_preview import store_identity
from app.utils.operation_control import operation_context

ROUTES = {"categories": "products/categories", "attributes": "products/attributes",
          "storefront_collections": "products/brands"}
MAX_ACTIONS = 50
PARENT_CACHE_SECONDS = 15
MAX_REQUESTS = 60
MAX_PAGES = 10


class SyncError(ValueError):
    """Safe local diagnostics; remote error bodies are never rendered."""

    def __init__(self, message, report=None):
        super().__init__(message)
        self.report = report


class TaxonomyClient(PublisherWooClient):
    allowed_methods = {"GET", "POST", "PUT"}


def make_client(write=False):
    configuration = effective_configuration()
    if not configuration.complete:
        raise SyncError("Woo connection is not configured. Local registry and metadata editing remain available.")
    try:
        return (TaxonomyClient if write else ReadOnlyWooClient)(configuration)
    except WooConnectionError:
        raise SyncError("Woo connection configuration is invalid; check the connection workspace.") from None


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def canonical(row, kind):
    if not isinstance(row, dict) or not isinstance(row.get("name"), str) or not isinstance(row.get("slug"), str):
        raise SyncError("Woo returned an invalid taxonomy definition.")
    slug = row["slug"]
    if kind == "attributes" and slug.startswith("pa_"):
        slug = slug[3:]
    result = {"name": normal(html.unescape(row["name"])), "slug": slug}
    if kind == "storefront_collections" and row.get("parent", 0) != 0:
        raise SyncError("Hierarchical Woo Brands cannot be flattened into flat Storefront Collections; manual review required.")
    if kind == "categories":
        if type(row.get("parent")) is not int or row["parent"] < 0:
            raise SyncError("Woo category hierarchy is incomplete.")
        result["parent"] = row["parent"]
    return result


class API:
    def __init__(self, client, max_requests=MAX_REQUESTS):
        self.client = client
        self.requests = 0
        self.max_requests = max_requests

    def request(self, method, route, params=None, body=None):
        if method not in {"GET", "POST", "PUT"} or not re.fullmatch(r"products/(?:categories|brands|attributes)(?:/[1-9][0-9]*(?:/terms(?:/[1-9][0-9]*)?)?)?", route):
            if not (method == "GET" and route == ""):
                raise SyncError("Only reviewed taxonomy definition endpoints are permitted.")
        if method == "POST" and (re.search(r"/[0-9]+$", route) or set(body or {}) - {"name", "slug", "parent", "type", "order_by", "menu_order"}):
            raise SyncError("Taxonomy updates and non-definition payloads are not permitted.")
        if method == "PUT":
            attribute = re.fullmatch(r"products/attributes/[1-9][0-9]*", route)
            term = re.fullmatch(r"products/attributes/[1-9][0-9]*/terms/[1-9][0-9]*", route)
            if not ((attribute and body == {"order_by": "menu_order"}) or
                    (term and set(body or {}) == {"menu_order"} and type(body["menu_order"]) is int and 0 <= body["menu_order"] <= 1000000)):
                raise SyncError("Only reviewed attribute/term ordering corrections are permitted.")
        url = f"{self.client.base_url}/wp-json/wc/v3/{route}"
        if params:
            url += "?" + urlencode(params)
        for attempt in range(2):
            self.requests += 1
            if self.requests > self.max_requests:
                raise SyncError("Woo request budget exhausted; no absence may be inferred. Use a fresh smaller review.")
            try:
                return self.client.request_json(method, url, authenticated=True, json_body=body)
            except WooConnectionError as error:
                transient = error.category in {"connect_timeout", "read_timeout", "connection_failed"} or error.status_code in {502, 503, 504}
                if method == "GET" and attempt == 0 and transient:
                    time.sleep(0.25)
                    continue
                raise SyncError(f"Woo taxonomy access unavailable ({error.category}). Review again; a failed write is never repeated automatically.") from None

    def read(self, route, kind):
        rows, seen = [], set()
        for page in range(1, MAX_PAGES + 1):
            payload, response = self.request("GET", route, {"per_page": 100, "page": page, "hide_empty": "false"})
            if not isinstance(payload, list) or len(payload) > 100:
                raise SyncError("Woo taxonomy discovery is invalid or exceeds the bounded page size.")
            for row in payload:
                if not isinstance(row, dict) or type(row.get("id")) is not int or row["id"] <= 0 or row["id"] in seen:
                    raise SyncError("Woo discovery contains duplicate or invalid identities.")
                canonical(row, kind)
                seen.add(row["id"])
                rows.append(row)
            headers = getattr(response, "headers", {})
            total = headers.get("X-WP-Total")
            if total is not None:
                if not str(total).isdigit() or int(total) < len(rows):
                    raise SyncError("Woo discovery totals are inconsistent.")
                if int(total) == len(rows):
                    return sorted(rows, key=lambda row: row["id"])
            if len(payload) < 100:
                if total is not None and (not str(total).isdigit() or int(total) != len(rows)):
                    raise SyncError("Woo discovery is incomplete; absence is not established.")
                return sorted(rows, key=lambda row: row["id"])
        raise SyncError("Woo taxonomy page limit reached; absence is not established.")

    def brands_available(self):
        payload, _ = self.request("GET", "")
        route = (payload.get("routes", {}) if isinstance(payload, dict) else {}).get("/wc/v3/products/brands", {})
        if not isinstance(route, dict) or not isinstance(route.get("methods", []), list) or not isinstance(route.get("endpoints", []), list):
            raise SyncError("Woo Brands capability response is incompatible.")
        methods = set(route.get("methods", []))
        for endpoint in route.get("endpoints", []):
            if not isinstance(endpoint, dict) or not isinstance(endpoint.get("methods", []), list):
                raise SyncError("Woo Brands capability response is incompatible.")
            methods.update(endpoint.get("methods", []))
        return {"GET", "POST"}.issubset(methods)


def local_digest(row, kind, scope):
    return digest({"name": normal(html.unescape(row["name"])), "slug": row["slug"],
                   "parent": row.get("parent"), "scope": scope, "kind": kind})


def ordering(payload, kind):
    field = "menu_order" if kind == "terms" else "order_by" if kind == "attributes" else None
    return {field: payload.get(field)} if field else {}


def similar_rows(rows, expected, kind):
    """Slugs identify a taxonomy; category display names also require parent context."""
    result = []
    for row in rows:
        value = canonical(row, kind)
        if value["slug"] == expected["slug"] or (value["name"] == expected["name"] and (kind != "categories" or value["parent"] == expected["parent"])):
            result.append(row)
    return result


def identity_rows(store):
    return WooTaxonomyIdentity.query.filter_by(store_key=store).order_by(WooTaxonomyIdentity.id).all()


def candidate_reasons(candidates, expected, kind, scope, claimed):
    reasons = []
    if len(candidates) > 1:
        reasons.append(f"Ambiguous: {len(candidates)} Woo candidates satisfy the name/slug search.")
    for candidate in candidates:
        actual = canonical(candidate, kind)
        for field in expected:
            if actual.get(field) != expected[field]:
                reasons.append(f"Woo ID {candidate['id']} {field} mismatch: expected {expected[field]!r}, observed {actual.get(field)!r}.")
        owner = claimed.get((kind, scope, candidate['id']))
        if owner:
            reasons.append(f"Woo ID {candidate['id']} is already linked/reserved to local key {owner.local_key} (scope {owner.scope_key or 'root'}, state {owner.state}) in this store.")
    return reasons


def plan(client=None):
    client = client or make_client()
    store = store_identity(client.configuration)
    data, revision, missing = registry.current_source()
    if missing:
        raise SyncError("Provide a valid local registry before reviewing Woo sync.")
    api = API(client)
    identities = identity_rows(store["key"])
    mappings = {(i.kind, i.scope_key, i.local_key): i for i in identities}
    claimed = {(i.kind, i.remote_scope, i.woo_id): i for i in identities if i.woo_id}
    rows, unavailable, verified = [], [], {}

    def reconcile(kind, scope, local, remote, route, remote_scope=0, parent_ids=None):
        remote_by_id = {r["id"]: r for r in remote}
        candidates_claimed = set()
        for position, definition in enumerate(local):
            key = definition["key"]
            entry = {"id": f"{kind}:{scope}:{key}", "kind": kind, "scope": scope,
                     "key": key, "name": definition["name"], "local": definition,
                     "remote": None, "remote_scope": remote_scope, "route": route,
                     "state": "local_only", "action": "create", "reason": "Reviewed create only."}
            expected = {"name": definition["name"], "slug": definition["slug"]}
            if kind == "attributes":
                expected["type"] = "select"
                expected["order_by"] = "menu_order"
            if kind == "terms":
                expected["menu_order"] = definition.get("order", position)
            if kind == "categories":
                parent = definition["parent"]
                if parent and parent not in (parent_ids or {}):
                    # These are potential local matches, not new registry imports.
                    potential = [r for r in remote if r["slug"] == definition["slug"] or normal(html.unescape(r["name"])) == normal(definition["name"])]
                    candidates_claimed.update(r["id"] for r in potential)
                    entry.update(state="conflict", action=None, remote=potential[0] if len(potential) == 1 else None,
                                 reason=f"Local parent {parent} is not verified in this store. Verify/link/import that parent and regenerate Preview; child hierarchy cannot yet be trusted. {len(potential)} possible Woo matches held out of import.")
                    rows.append(entry)
                    continue
                expected["parent"] = (parent_ids or {}).get(parent, 0)
            entry["payload"] = expected
            wanted = canonical(expected, kind)
            matches = [r for r in remote if canonical(r, kind) == wanted]
            similar = similar_rows(remote, wanted, kind)
            identity = mappings.get((kind, scope, key))
            if identity and identity.woo_id:
                actual = remote_by_id.get(identity.woo_id)
                entry["remote"] = actual
                if (identity.state == "verified" and identity.local_digest == local_digest(definition, kind, scope)
                        and len(matches) == 1 and len(similar) == 1 and matches[0]["id"] == identity.woo_id and actual
                        and identity.remote_scope == remote_scope and identity.remote_digest == digest(canonical(actual, kind))):
                    entry.update(state="verified", action=None, reason="Current-store identity and definition verified.")
                    if ordering(actual, kind) != ordering(expected, kind):
                        entry.update(state="ordering_drift", action="order", order_payload=ordering(expected, kind), reason=f"Ordering differs: expected {ordering(expected, kind)}, Woo {ordering(actual, kind)}. Review an ordering-only correction.")
                    else:
                        verified[(kind, key)] = identity.woo_id
                else:
                    reasons = [f"Stored identity for {key} points to Woo ID {identity.woo_id}; exact current match IDs: {[r['id'] for r in matches]}."]
                    if not actual:
                        reasons.append("Stored Woo ID is missing from current discovery.")
                    else:
                        reasons.extend(candidate_reasons([actual], wanted, kind, remote_scope, {}))
                    if identity.state != 'verified':
                        reasons.append(f"Stored identity state is {identity.state}.")
                    if identity.remote_scope != remote_scope:
                        reasons.append(f"Stored remote scope {identity.remote_scope} differs from expected {remote_scope}.")
                    if identity.local_digest != local_digest(definition, kind, scope):
                        reasons.append("Local definition changed since identity verification.")
                    if actual and identity.remote_digest != digest(canonical(actual, kind)):
                        reasons.append("Woo definition changed since identity verification.")
                    if len(similar) > 1:
                        reasons.append(f"Ambiguous: {len(similar)} possible Woo candidates.")
                    entry.update(state="stale", action=None, reason=" ".join(reasons))
            elif len(matches) == 1 and len(similar) == 1 and not claimed.get((kind, remote_scope, matches[0]["id"])):
                entry.update(state="safe_match", action="link", remote=matches[0], reason="Exact name/slug/scope match; explicit link required.")
                candidates_claimed.add(matches[0]["id"])
            elif similar:
                entry.update(state="conflict", action=None, remote=similar[0] if len(similar) == 1 else None,
                             reason=" ".join(candidate_reasons(similar, wanted, kind, remote_scope, claimed)))
                candidates_claimed.update(r["id"] for r in similar)
            elif identity:
                entry.update(state="stale", action=None, reason="Earlier create outcome is uncertain. Inspect Woo and regenerate; never retry create blindly.")
            if definition["state"] == "deprecated":
                entry.update(action=None, reason="Deprecated definitions are not synchronised.")
            rows.append(entry)
        for remote_row in remote:
            if remote_row["id"] in candidates_claimed or (kind, remote_scope, remote_row["id"]) in claimed:
                continue
            imported = {"key": ("range-" if kind == "storefront_collections" else "attr-" if kind == "attributes" else "term-" if kind == "terms" else "cat-") + canonical(remote_row, kind)["slug"],
                        "name": html.unescape(remote_row["name"]), "slug": canonical(remote_row, kind)["slug"], "state": "draft"}
            reason, action = "Import as Draft; existing local definitions will not be overwritten.", "import"
            if kind == "categories":
                parent = remote_row["parent"]
                imported["parent"] = next((key for key, value in (parent_ids or {}).items() if value == parent), None)
                if parent and imported["parent"] is None:
                    reason, action = "Import/link the parent category first, then regenerate Preview.", None
            if kind == "attributes":
                imported.update(terms=[], navigation=False, visible_default=False)
            if kind == "terms" and type(remote_row.get("menu_order")) is int and remote_row["menu_order"] >= 0:
                imported["order"] = remote_row["menu_order"]
            proposed = json.loads(json.dumps(data))
            destination = proposed[kind] if kind != "terms" else next(a["terms"] for a in proposed["attributes"] if a["key"] == scope)
            destination.append(imported)
            try:
                registry.validate(proposed)
            except registry.RegistryEditError:
                collisions = [r for r in destination[:-1] if r["key"] == imported["key"] or r["slug"] == imported["slug"] or normal(r["name"]) == normal(imported["name"])]
                reason = (f"Import collides with existing local keys: {', '.join(r['key'] for r in collisions)} (key/name/slug). Review those local definitions instead."
                          if collisions else "Import fails registry validation (hierarchy, aliases, bounds or schema). Review the proposed definition locally.")
                action = None
            rows.append({"id": f"remote:{kind}:{scope}:{remote_row['id']}", "kind": kind, "scope": scope,
                         "key": imported["key"], "name": imported["name"], "local": imported,
                         "remote": remote_row, "remote_scope": remote_scope, "route": route,
                         "state": "woo_only" if action else "conflict", "action": action, "reason": reason})

    for kind, route in ROUTES.items():
        try:
            if kind == "storefront_collections" and not api.brands_available():
                raise SyncError("Woo Brands GET/POST capability is not advertised by this store. No alternative taxonomy is used.")
            remote = api.read(route, kind)
            if kind == "categories":
                # Existing verified parent identities only; staged rounds avoid speculative dependencies.
                parents = {}
                todo = list(data[kind])
                while todo:
                    ready = [r for r in todo if not r["parent"] or r["parent"] in parents]
                    if not ready:
                        break
                    for definition in ready:
                        identity = mappings.get((kind, "", definition["key"]))
                        wanted = canonical({**definition, "parent": parents.get(definition["parent"], 0)}, kind)
                        matches = [r for r in remote if canonical(r, kind) == wanted]
                        if (identity and identity.state == "verified" and identity.local_digest == local_digest(definition, kind, "")
                                and len(matches) == 1 and len(similar_rows(remote, wanted, kind)) == 1 and matches[0]["id"] == identity.woo_id
                                and identity.remote_digest == digest(wanted)):
                            parents[definition["key"]] = identity.woo_id
                    todo = [r for r in todo if r not in ready]
                reconcile(kind, "", data[kind], remote, route, parent_ids=parents)
            else:
                reconcile(kind, "", data[kind], remote, route)
        except SyncError as error:
            unavailable.append({"kind": kind, "reason": str(error)})
    for attribute in data["attributes"]:
        remote_id = verified.get(("attributes", attribute["key"]))
        if not remote_id:
            unavailable.append({"kind": f"terms:{attribute['key']}", "reason": "Verify/link/import the global attribute first; terms are not matched across attributes."})
            continue
        route = f"products/attributes/{remote_id}/terms"
        try:
            reconcile("terms", attribute["key"], attribute["terms"], api.read(route, "terms"), route, remote_id)
        except SyncError as error:
            unavailable.append({"kind": f"terms:{attribute['key']}", "reason": str(error)})
    known = {(r["kind"], r["scope"], r["key"]) for r in rows if not r["id"].startswith("remote:")}
    for identity in identities:
        unavailable_keys = {entry["kind"] for entry in unavailable}
        resource = f"terms:{identity.scope_key}" if identity.kind == "terms" else identity.kind
        if (identity.kind, identity.scope_key, identity.local_key) not in known and resource not in unavailable_keys:
            rows.append({"id": f"stale:{identity.id}", "kind": identity.kind, "scope": identity.scope_key,
                         "key": identity.local_key, "name": identity.local_key, "state": "stale", "action": None,
                         "reason": "Persisted identity no longer has a reconciled local definition. No automatic removal."})
    result = {"store": store, "revision": revision, "rows": sorted(rows, key=lambda r: r["id"]), "unavailable": unavailable,
              "identities": [{"id": i.id, "state": i.state, "local": i.local_digest, "remote": i.remote_digest, "woo": i.woo_id} for i in identities]}
    result["digest"] = digest(result)
    return result


def selected(plan_value, ids):
    if not isinstance(ids, list) or not 1 <= len(ids) <= MAX_ACTIONS or len(set(ids)) != len(ids):
        raise SyncError(f"Select between 1 and {MAX_ACTIONS} distinct reviewed definitions.")
    lookup = {row["id"]: row for row in plan_value["rows"]}
    if any(key not in lookup or not lookup[key].get("action") for key in ids):
        raise SyncError("Selection includes an unavailable/conflicting/stale definition.")
    rows = [lookup[key] for key in ids]
    # No fake cross-file transaction: local imports and remote writes get separate reviews.
    if any(r["action"] == "import" for r in rows) and any(r["action"] != "import" for r in rows):
        raise SyncError("Review local imports separately from Woo creates/identity links.")
    return rows


def import_proposal(rows):
    data, revision, _ = registry.current_source()
    for row in rows:
        destination = data[row["kind"]] if row["kind"] != "terms" else next(a["terms"] for a in data["attributes"] if a["key"] == row["scope"])
        destination.append(row["local"])
    registry.validate(data)
    return data, revision


def persist(row, store, remote):
    identity = WooTaxonomyIdentity.query.filter_by(store_key=store, kind=row["kind"], scope_key=row["scope"], local_key=row["key"]).first()
    if identity is None:
        identity = WooTaxonomyIdentity(store_key=store, kind=row["kind"], scope_key=row["scope"], local_key=row["key"])
        db.session.add(identity)
    identity.remote_scope = row["remote_scope"]
    identity.local_digest = local_digest(row["local"], row["kind"], row["scope"])
    identity.state = "verified" if remote else "uncertain"
    identity.woo_id = remote["id"] if remote else None
    identity.remote_digest = digest(canonical(remote, row["kind"])) if remote else None
    identity.verified_at = datetime.now(UTC).replace(tzinfo=None) if remote else None
    db.session.commit()


def execute(review_digest, ids, client=None, *, return_report=False):
    client = client or make_client(write=True)
    completed = []
    with operation_context("woo_taxonomy_sync", {"selected": ids}) as lease:
        current = plan(client)
        if current["digest"] != review_digest:
            raise SyncError("Registry, Woo definitions, store or identity state changed. Regenerate Preview and review again.")
        rows = selected(current, ids)
        succeeded, attempted, uncertain = [], None, False
        parents = {}
        # Discovery keeps its original budget; execution scales to the explicitly
        # reviewed selection, bounded by 260 requests for 50 definitions.
        api = API(client, max_requests=MAX_REQUESTS + 4 * len(rows))

        def parent_read(route):
            cached = parents.get(route)
            if cached and time.monotonic() - cached[0] < PARENT_CACHE_SECONDS:
                return cached[1]
            observed, _ = api.request("GET", route)
            parents[route] = (time.monotonic(), observed)
            return observed

        def result(message=None):
            items = []
            for row in rows:
                status = ('succeeded' if row['id'] in succeeded else
                          ('uncertain' if uncertain else 'failed') if message and row['id'] == attempted else 'not_attempted')
                reason = ("Saved and verified." if status == 'succeeded' else message if status in {'failed', 'uncertain'}
                          else "Operation stopped after a safety/access/persistence failure; fresh review required.")
                items.append(dict(id=row['id'], name=row['name'], status=status, reason=reason))
            counts = {s: sum(i['status'] == s for i in items) for s in ('succeeded', 'failed', 'uncertain', 'not_attempted')}
            counts.update(selected=len(rows), attempted=len(rows) - counts['not_attempted'])
            return dict(completed=completed, succeeded=succeeded, pending=[i['id'] for i in items if i['status'] == 'not_attempted'],
                        **({'uncertain' if uncertain else 'failed': attempted} if message else {}), counts=counts, items=items)
        try:
            if rows[0]["action"] == "import":
                proposed, revision = import_proposal(rows)
                if revision != current["revision"]:
                    raise SyncError("Registry changed after review.")
                # Re-read every selected remote before touching authored registry.
                for row in rows:
                    attempted = row["id"]
                    observed, _ = api.request("GET", f"{row['route']}/{row['remote']['id']}")
                    if observed.get("id") != row["remote"]["id"] or canonical(observed, row["kind"]) != canonical(row["remote"], row["kind"]):
                        raise SyncError("Woo import source changed after review.")
                    parent_kind, parent_id = None, None
                    if row["kind"] == "terms":
                        parent_kind, parent_id = "attributes", row["remote_scope"]
                    elif row["kind"] == "categories" and row["remote"]["parent"]:
                        parent_kind, parent_id = "categories", row["remote"]["parent"]
                    if parent_id:
                        prior = next(r["remote"] for r in current["rows"] if r["kind"] == parent_kind and r["state"] == "verified" and r["remote"]["id"] == parent_id)
                        observed_parent = parent_read(f"{ROUTES[parent_kind]}/{parent_id}")
                        if observed_parent.get("id") != parent_id or canonical(observed_parent, parent_kind) != canonical(prior, parent_kind):
                            raise SyncError("Verified import parent changed; registry save refused.")
                registry.save_reviewed(proposed, revision)
                completed.append("Local registry saved and verified; product assignments unchanged")
                for row in rows:
                    attempted = row["id"]
                    persist(row, current["store"]["key"], row["remote"])
                    completed.append(row["name"])
                    succeeded.append(row["id"])
            else:
                for row in rows:
                    attempted, uncertain = row["id"], False
                    if registry.current_source()[1] != current["revision"]:
                        raise SyncError("Registry changed during sync.")
                    # Fresh exhaustive scoped list immediately before each mutation/link.
                    remotes = api.read(row["route"], row["kind"])
                    expected = canonical(row["payload"], row["kind"])
                    similar = similar_rows(remotes, expected, row["kind"])
                    if row["kind"] == "categories" and row["payload"]["parent"]:
                        parent_id = row["payload"]["parent"]
                        prior = next((r.get("remote") for r in current["rows"] if r["kind"] == "categories" and r["state"] == "verified" and r.get("remote", {}).get("id") == parent_id), None)
                        observed = next((r for r in remotes if r["id"] == parent_id), None)
                        if not prior or not observed or canonical(prior, "categories") != canonical(observed, "categories"):
                            raise SyncError("Verified parent category changed; child write refused.")
                    if row["kind"] == "terms":
                        prior = next(r["remote"] for r in current["rows"] if r["kind"] == "attributes" and r["key"] == row["scope"] and r["state"] == "verified")
                        observed = parent_read(f"products/attributes/{row['remote_scope']}")
                        if observed.get("id") != prior["id"] or canonical(observed, "attributes") != canonical(prior, "attributes") or ordering(observed, 'attributes') != ordering(prior, 'attributes'):
                            raise SyncError("Verified global attribute changed; term write refused.")
                    if row["action"] == "create":
                        if similar:
                            raise SyncError("A Woo match appeared after review; create refused. Review a link instead.")
                        persist(row, current["store"]["key"], None)  # durable uncertain-write reservation
                        uncertain = True
                        remote, _ = api.request("POST", row["route"], body=row["payload"])
                    else:
                        if len(similar) != 1 or similar[0]["id"] != row["remote"]["id"] or canonical(similar[0], row["kind"]) != expected:
                            raise SyncError("Reviewed Woo match changed or became ambiguous.")
                        remote = similar[0]
                        if row["action"] == "order":
                            if ordering(remote, row["kind"]) != ordering(row["remote"], row["kind"]):
                                raise SyncError("Reviewed ordering changed; generate a fresh Preview.")
                            uncertain = True
                            api.request("PUT", f"{row['route']}/{remote['id']}", body=ordering(row["payload"], row["kind"]))
                    if type(remote.get("id")) is not int or remote["id"] <= 0:
                        raise SyncError("Woo did not return a verified identity. Review the uncertain outcome; do not retry create.")
                    readback, _ = api.request("GET", f"{row['route']}/{remote['id']}")
                    if readback.get("id") != remote["id"] or canonical(readback, row["kind"]) != expected:
                        raise SyncError("Woo readback failed name/slug/hierarchy verification. Outcome retained for review.")
                    if row["action"] in {"create", "order"} and ordering(readback, row["kind"]) != ordering(row["payload"], row["kind"]):
                        raise SyncError(f"Woo ordering readback differs: expected {ordering(row['payload'], row['kind'])}, observed {ordering(readback, row['kind'])}. Outcome retained for review.")
                    if registry.current_source()[1] != current["revision"]:
                        raise SyncError("Registry changed during readback; identity was not trusted.")
                    persist(row, current["store"]["key"], readback)
                    if row['kind'] == 'attributes':
                        parents.pop(f"products/attributes/{readback['id']}", None)
                    completed.append(row["name"])
                    succeeded.append(row["id"])
                    uncertain = False
        except Exception as error:
            db.session.rollback()
            operation = db.session.get(CatalogueOperation, lease.id)
            scope = json.loads(operation.scope)
            message = str(error) if isinstance(error, (SyncError, registry.RegistryEditError)) else "Local persistence or Woo verification failed safely."
            report = result(message)
            scope["operation_summary"] = report
            operation.scope = json.dumps(scope)
            db.session.commit()
            raise SyncError(f"{message} Completed {len(completed)} steps remain saved. {'Uncertain' if uncertain else 'Failed'}: {attempted}. No rollback/delete was attempted; regenerate Preview.", report=report) from None
        operation = db.session.get(CatalogueOperation, lease.id)
        scope = json.loads(operation.scope)
        report = result()
        scope["operation_summary"] = report
        operation.scope = json.dumps(scope)
        db.session.commit()
    return report if return_report else completed
