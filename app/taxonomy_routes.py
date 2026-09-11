"""Authenticated registry workspace and reviewed Woo definition sync; no product publishing."""
import hashlib
import json

from flask import Blueprint, current_app, render_template, request, redirect, url_for, flash, abort
from flask_login import current_user, login_required
from itsdangerous import URLSafeTimedSerializer, BadData

from app.models import CatalogueOperation
from app import taxonomy_registry as registry
from app import taxonomy_workspace as service
from app import woo_taxonomy_sync as sync
from app.taxonomy_sync_view import sync_views
from app.utils.operation_control import operation_context, CatalogueOperationActive


taxonomy = Blueprint("taxonomy", __name__, url_prefix="/taxonomy")
LABELS = {"categories": "Categories", "storefront_collections": "Storefront Collections",
          "attributes": "Attributes", "terms": "Attribute terms", "tags": "Tags"}


@taxonomy.route("/variation-proposal/<int:product_id>", methods=["GET", "POST"])
@login_required
def variation_proposal(product_id):
    from app import db
    from app.models import Product
    from app.variation_taxonomy_proposal import proposal
    if request.method == "POST":
        signed = verified(request.form.get("review", ""))
        if (signed.get("mode") != "variation-proposal" or signed.get("product_id") != product_id
                or request.form.get("acknowledge") != "yes"):
            raise service.RegistryEditError("Explicit acknowledgement and a product-specific review are required.")
        with operation_context("taxonomy_registry_update", {"action": "variation-proposal", "product_id": product_id}):
            db.session.expire_all()
            current = proposal(db.get_or_404(Product, product_id))
            if current["digest"] != signed.get("digest") or not current["additions"]:
                raise service.RegistryEditError("Registry, product or child projection changed. Open a fresh proposal.")
            service.save_reviewed(current["data"], signed["revision"])
        flash("Variation taxonomy saved and verified locally. Product metadata and SKUs are unchanged. Continue to Woo Taxonomy Sync to review and verify definitions; no Woo action has run.", "success")
        return redirect(url_for("taxonomy.sync_workspace", view="attributes"))
    product = db.get_or_404(Product, product_id)
    current = proposal(product)
    token = signature(current["revision"], mode="variation-proposal", product_id=product_id, digest=current["digest"])
    return render_template("taxonomy/variation_proposal.html", product=product, proposal=current, token=token)


@taxonomy.errorhandler(sync.SyncError)
def sync_rejected(error):
    if error.report:
        return render_template("taxonomy/sync_result.html", report=error.report), 409
    return render_template("taxonomy/error.html", error=str(error)), 409


@taxonomy.route("/sync")
@taxonomy.route("/sync/<view>")
@login_required
def sync_workspace(view="overview"):
    # No Woo discovery on page load, including when the store is offline.
    return render_sync(view=view)


def render_sync(plan=None, token=None, view="overview"):
    if view not in {"overview", "categories", "attributes", "storefront_collections"}:
        abort(404)
    return render_template("taxonomy/sync.html", plan=plan, token=token, labels=LABELS,
                           views=sync_views(plan), active_view=view, limit=sync.MAX_ACTIONS)


@taxonomy.route("/sync/preview", methods=["POST"])
@login_required
def sync_preview():
    plan = sync.plan()
    token = signature(plan["revision"], mode="sync-preview", digest=plan["digest"])
    return render_sync(plan, token, request.form.get("view", "overview"))


@taxonomy.route("/sync/review", methods=["POST"])
@login_required
def sync_review():
    signed = verified(request.form.get("review", ""))
    ids = request.form.getlist("selected")
    if signed.get("mode") != "sync-preview" or not 1 <= len(ids) <= sync.MAX_ACTIONS:
        raise sync.SyncError(f"Select 1–{sync.MAX_ACTIONS} definitions from a fresh Woo Sync Preview.")
    plan = sync.plan()
    if signed.get("digest") != plan["digest"]:
        raise sync.SyncError("Preview changed. Generate a fresh Woo Sync Preview before review.")
    rows = sync.selected(plan, ids)
    document = None
    if rows[0]["action"] == "import":
        proposed, _ = sync.import_proposal(rows)
        document = service.validate(proposed).decode()
    token = signature(plan["revision"], mode="sync-confirm", digest=plan["digest"], ids=ids)
    return render_template("taxonomy/sync_review.html", rows=rows, plan=plan, token=token, document=document, labels=LABELS)


@taxonomy.route("/sync/confirm", methods=["POST"])
@login_required
def sync_confirm():
    signed = verified(request.form.get("review", ""))
    if signed.get("mode") != "sync-confirm" or request.form.get("acknowledge") != "yes":
        raise sync.SyncError("Explicit acknowledgement and a current sync review are required.")
    report = sync.execute(signed["digest"], signed["ids"], return_report=True)
    return render_template("taxonomy/sync_result.html", report=report)


@taxonomy.route("/options")
@login_required
def assignment_options():
    from app.taxonomy_assignments import options
    return options()


def signer():
    return URLSafeTimedSerializer(current_app.secret_key, salt="taxonomy-review-v1")


def signature(revision, **extra):
    return signer().dumps({"user": str(current_user.id), "revision": revision, **extra})


def verified(token):
    try:
        value = signer().loads(token, max_age=1800)
        if value["user"] != str(current_user.id):
            raise ValueError()
        return value
    except (BadData, ValueError, KeyError, TypeError):
        raise service.RegistryEditError("Review expired or is invalid. Open a fresh review.") from None


def base_source():
    before, revision, missing = service.current_source()
    if request.method == "POST" and verified(request.form.get("base", ""))["revision"] != revision:
        raise service.RegistryEditError("Registry changed while editing. Open a fresh review.")
    return before, revision, missing


def review(before, proposed, revision, missing, mode):
    document = service.validate(proposed).decode()
    token = signature(revision, digest=hashlib.sha256(document.encode()).hexdigest(), mode=mode)
    return render_template("taxonomy/review.html", document=document, token=token, mode=mode,
                           missing=missing, counts=service.counts(proposed),
                           changes=service.change_summary(before, proposed), labels=LABELS,
                           before=json.dumps(before, indent=2, ensure_ascii=False))


@taxonomy.errorhandler(service.RegistryEditError)
def rejected(error):
    draft = request.form.get("document", "")[:registry.MAX_BYTES] if request.method == "POST" else ""
    return render_template("taxonomy/error.html", error=str(error), draft=draft), 409


@taxonomy.errorhandler(OSError)
def storage_error(error):
    return render_template("taxonomy/error.html", error="Registry storage could not be accessed safely. Check mount permissions and reload."), 409


@taxonomy.errorhandler(CatalogueOperationActive)
def busy(error):
    return render_template("taxonomy/error.html", error="Another local operation is active. Wait for it to finish, then review again."), 409


@taxonomy.route("")
@login_required
def index():
    status = registry.load_configured_registry()
    data = None
    if status.available:
        data, _, _ = service.current_source()
    kind = request.args.get("kind", "categories")
    if kind not in registry.KINDS:
        abort(404)
    query = request.args.get("q", "")[:191].casefold()
    paths = service.category_paths(data) if data else {}
    rows = data[kind] if data else []
    if kind == "categories":
        # Preorder keeps descendants beside their parent; search retains ancestry.
        children = {}
        for row in rows:
            children.setdefault(row["parent"], []).append(row)
        def branch(parent):
            for row in sorted(children.get(parent, []), key=lambda r: (r.get("order", 0), r["name"].casefold())):
                yield row
                yield from branch(row["key"])
        rows = list(branch(None))
    rows = [r for r in rows if query in json.dumps(r, ensure_ascii=False).casefold() or query in paths.get(r["key"], "").casefold()]
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
    page = min(page, max(1, (len(rows) + 24) // 25))
    last = CatalogueOperation.query.filter_by(operation_type="taxonomy_registry_update").order_by(CatalogueOperation.started_at.desc()).first()
    return render_template("taxonomy/index.html", status=status, counts=service.counts(data) if data else None,
                           rows=rows[(page-1)*25:page*25], total=len(rows), page=page, kind=kind,
                           labels=LABELS, paths=paths, query=request.args.get("q", "")[:191], last=last)


@taxonomy.route("/advanced", methods=["GET", "POST"])
@login_required
def advanced():
    before, revision, missing = base_source()
    document = request.form.get("document", "") if request.method == "POST" else json.dumps(before, ensure_ascii=False, indent=2)
    error = None
    if request.method == "POST":
        try:
            return review(before, service.decode(document.encode()), revision, missing, "advanced")
        except service.RegistryEditError as invalid:
            error = str(invalid)
    return render_template("taxonomy/advanced.html", document=document, error=error, base=signature(revision)), 422 if error else 200


@taxonomy.route("/import", methods=["GET", "POST"])
@login_required
def import_seed():
    before, revision, missing = base_source()
    if not missing:
        raise service.RegistryEditError("Bootstrap is only available for a missing registry. Existing registries use an explicit Advanced JSON replacement review.")
    if request.method == "POST":
        if "seed" not in request.files or "categories_csv" not in request.files:
            raise service.RegistryEditError("Select both the TLC JSON seed and category CSV.")
        proposed = service.bootstrap(request.files["seed"].stream.read(registry.MAX_BYTES + 1),
                                     request.files["categories_csv"].stream.read(registry.MAX_BYTES + 1))
        return review(before, proposed, revision, missing, "bootstrap")
    return render_template("taxonomy/import.html", base=signature(revision))


@taxonomy.route("/edit/<kind>", methods=["GET", "POST"])
@login_required
def edit(kind):
    if kind not in LABELS:
        abort(404)
    data, revision, missing = base_source()
    key, attribute = request.args.get("key", ""), request.args.get("attribute", "")
    rows = data[kind] if kind != "terms" else next((a["terms"] for a in data["attributes"] if a["key"] == attribute), None)
    if rows is None:
        abort(404)
    row = next((r for r in rows if r["key"] == key), None) if key else {}
    if row is None:
        abort(404)
    error = None
    if request.method == "POST":
        try:
            proposed = service.edit_definition(json.loads(json.dumps(data)), kind, key, attribute, request.form)
            return review(data, proposed, revision, missing, "guided")
        except service.RegistryEditError as invalid:
            error = str(invalid)
    values = dict(request.form) if request.method == "POST" else {
        **row, "aliases": "\n".join(row.get("aliases", [])),
        "navigation": "yes" if row.get("navigation") else "",
        "visible_default": "yes" if row.get("visible_default") else ""}
    if request.method == "GET" and not key and request.args.get("name"):
        # Prefill is only a proposal: the unchanged reviewed writer validates it.
        values["name"] = request.args["name"][:191]
    return render_template("taxonomy/edit.html", row=row, values=values, key=key, attribute=attribute,
                           kind=kind, labels=LABELS, categories=data["categories"],
                           paths=service.category_paths(data), base=signature(revision), error=error), 422 if error else 200


@taxonomy.route("/confirm", methods=["POST"])
@login_required
def confirm():
    signed = verified(request.form.get("review", ""))
    document = request.form.get("document", "")
    if request.form.get("acknowledge") != "yes":
        raise service.RegistryEditError("Explicit acknowledgement is required. Return to review.")
    if signed.get("digest") != hashlib.sha256(document.encode()).hexdigest() or signed.get("mode") not in {"bootstrap", "advanced", "guided"}:
        raise service.RegistryEditError("Proposed registry changed after review.")
    data = service.decode(document.encode())
    service.validate(data)
    with operation_context("taxonomy_registry_update", {"action": signed["mode"], "counts": service.counts(data)}):
        service.save_reviewed(data, signed["revision"], bootstrap_only=signed["mode"] == "bootstrap")
    flash("Local taxonomy registry saved and verified. No catalogue, scanner or WooCommerce changes were made. Return to your metadata editor, refresh registry choices, select the definition, then save metadata separately. If that save fails, the definition remains available; it is not rolled back.", "success")
    return redirect(url_for("taxonomy.index"))
