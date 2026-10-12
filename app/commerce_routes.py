"""Administrator configuration/discovery/drafts. GET never contacts either store."""
from flask import Blueprint, abort, render_template, request, redirect, url_for
from flask_login import login_required, current_user
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SelectField, HiddenField

from app import commerce_config as config
from app.shopify_connection import ShopifyAdapter, ShopifyConfiguration, ShopifyError, VERSIONS

commerce = Blueprint("commerce", __name__)


class ConnectionForm(FlaskForm):
    domain = StringField("Shopify store domain")
    token = PasswordField("Admin API access token")
    api_version = SelectField("GraphQL Admin API version", choices=[(v, v) for v in VERSIONS], default="2026-10")
    revision = HiddenField()


@commerce.route("/settings/commerce", methods=["GET", "POST"])
@login_required
def settings():
    if not current_user.is_admin:
        abort(403)
    form = ConnectionForm()
    result, error, saved = None, None, False
    try:
        active = config.active_platform()
        stored = config.shopify_settings()
        owned = config.deployment_owned()
        if request.method == "GET":
            form.domain.data = "" if owned else stored.get("domain", "")
            form.api_version.data = stored.get("api_version", "2026-10")
            form.revision.data = config.revision()
        elif form.validate_on_submit():
            if request.form.get("active_platform", active) != active:
                raise config.CommerceConfigError("Platform changes require a separate reviewed migration; activation is not available here.")
            if form.revision.data != config.revision():
                raise config.CommerceConfigError("Settings changed. Reload before testing or saving.")
            action = request.form.get("action")
            if action not in {"test", "save"}:
                abort(400)
            if owned:
                if action == "save":
                    raise config.CommerceConfigError("Shopify settings are deployment-owned. Update your environment instead.")
                candidate = ShopifyConfiguration.from_settings(stored)
            else:
                token = form.token.data or ""
                domain = (form.domain.data or "").strip().lower()
                if not token and domain == stored.get("domain"):
                    token = stored.get("token", "")
                candidate = ShopifyConfiguration(domain, token, form.api_version.data)
            candidate = candidate.validated()
            result = ShopifyAdapter().test_connection(candidate)
            if action == "save":
                config.save_shopify(dict(domain=candidate.domain, token=candidate.token,
                                         api_version=candidate.api_version, shop_id=result["shop_id"]), form.revision.data)
                saved = True
                form.revision.data = config.revision()
            form.domain.data, form.api_version.data = candidate.domain, candidate.api_version
            if owned:
                form.domain.data = ""
        elif request.method == "POST":
            error = "Invalid form or API version. Reload and try again."
    except (config.CommerceConfigError, ShopifyError) as exc:
        error = str(exc)
        active, stored, owned = "unavailable", {}, False
        # Never fall back to Woo if protected configuration is corrupt.
        try:
            active, stored, owned = config.active_platform(), config.shopify_settings(), config.deployment_owned()
        except config.CommerceConfigError:
            pass
    form.token.data = None  # No password redisplay, including rejected submissions.
    response = render_template("commerce_settings.html", form=form, active=active,
        owned=owned, has_token=bool(stored.get("token")),
        has_client=bool(stored.get("client_id") and stored.get("client_secret")),
        result=result, error=error, saved=saved)
    return response, 400 if error else 200, {"Cache-Control": "no-store"}


@commerce.route("/shopify/discovery", methods=["GET", "POST"])
@login_required
def discovery():
    """Explicit administrator reads only; no stored catalogue/identity mutation."""
    if not current_user.is_admin:
        abort(403)
    form = FlaskForm()
    result, error, action = None, None, None
    if request.method == "POST":
        if not form.validate_on_submit():
            abort(400)
        action = request.form.get("action")
        if action not in {"test", "products", "product", "variant", "collections", "collection"}:
            abort(400)
        try:
            stored = config.shopify_settings()
            configuration = ShopifyConfiguration.from_settings(stored)
            adapter = ShopifyAdapter()
            after, resource_id = request.form.get("after"), request.form.get("resource_id")
            if action == "test":
                result = adapter.test_connection(configuration)
                if config.deployment_owned():
                    result = dict(result, domain="deployment-configured store")
            elif action == "products":
                result = adapter.discover_products(configuration, after)
            elif action == "product":
                result = adapter.inspect_product(configuration, resource_id, request.form.get("variants"), request.form.get("media"))
            elif action == "variant":
                result = adapter.inspect_variant(configuration, resource_id, after)
            elif action == "collections":
                result = adapter.discover_collections(configuration, after)
            else:
                result = adapter.inspect_collection(configuration, resource_id, after)
        except (config.CommerceConfigError, ShopifyError) as exc:
            error = str(exc)
    return render_template("shopify_discovery.html", form=form, result=result, error=error,
                           action=action), 400 if error else 200, {"Cache-Control": "no-store"}


@commerce.route("/shopify/drafts", methods=["GET", "POST"])
@commerce.route("/products/<int:product_id>/shopify/review", methods=["GET", "POST"])
@login_required
def drafts(product_id=None):
    from app import db
    from app.models import Product, ShopifyProductIdentity
    from app import shopify_drafts as service
    from app.utils.operation_control import CatalogueOperationActive
    if not current_user.is_admin:
        abort(403)
    form = FlaskForm()
    scoped_product = db.session.get(Product, product_id) if product_id else None
    if product_id and not scoped_product:
        abort(404)
    plan, token, error = None, None, None
    if request.method == "POST":
        if not form.validate_on_submit():
            abort(400)
        try:
            if request.form.get("action") == "preview":
                product = scoped_product or db.session.get(Product, request.form.get("product_id", type=int))
                if not product:
                    abort(404)
                plan, token = service.preview(product)
            elif request.form.get("action") == "confirm":
                row = service.confirm(request.form.get("review", ""), request.form.get("acknowledge") == "yes", expected_product_id=product_id)
                return redirect(url_for("commerce.draft_identity", identity_id=row.id))
            else:
                abort(400)
        except (ShopifyError, config.CommerceConfigError, CatalogueOperationActive) as exc:
            error = str(exc)
    products = Product.query.filter_by(product_type="simple", catalogue_status="active").order_by(Product.title).limit(500).all()
    identities_query = ShopifyProductIdentity.query
    if scoped_product:
        identities_query = identities_query.filter_by(product_id=product_id)
    identities = identities_query.order_by(ShopifyProductIdentity.updated_at.desc()).limit(30).all()
    return render_template("shopify_drafts.html", form=form, plan=plan, token=token, error=error,
        products=products, identities=identities, scoped_product=scoped_product,
        selected_id=product_id or request.form.get("product_id", type=int) or request.args.get("product_id", type=int)), 400 if error else 200, {"Cache-Control": "no-store"}


@commerce.route("/shopify/drafts/identity/<int:identity_id>", methods=["GET", "POST"])
@login_required
def draft_identity(identity_id):
    from app import db
    from app.models import ShopifyProductIdentity
    from app import shopify_drafts as service
    from sqlalchemy.exc import IntegrityError
    from app.utils.operation_control import CatalogueOperationActive
    if not current_user.is_admin:
        abort(403)
    row = db.session.get(ShopifyProductIdentity, identity_id)
    if not row:
        abort(404)
    form = FlaskForm()
    error = None
    if request.method == "POST":
        if not form.validate_on_submit():
            abort(400)
        try:
            approval = request.form.get("approval")
            if approval and request.form.get("acknowledge_mapping") != "yes":
                raise ShopifyError("Explicit approval of the displayed media mapping is required.")
            service.reconcile(row, request.form.get("candidate_id") or None, approval=approval)
        except (ShopifyError, config.CommerceConfigError, CatalogueOperationActive) as exc:
            error = str(exc)
        except IntegrityError:
            db.session.rollback()
            error = "Remote ID is already reserved by another local mapping. Review both identities; no duplicate mapping was saved."
    data = service.evidence(row)
    return render_template("shopify_draft_identity.html", form=form, row=row, data=data,
        approval=service.recovery_token(row), error=error), 400 if error else 200, {"Cache-Control": "no-store"}


def guard_platform():
    """Do not execute Woo workflows on a Shopify installation. Woo path is untouched."""
    endpoint = request.endpoint or ""
    if endpoint.startswith(("main.woocommerce", "taxonomy.sync")) or endpoint in {"main.woo_sync", "main.woo_live_refresh", "main.sync"}:
        try:
            selected = config.active_platform()
        except config.CommerceConfigError:
            abort(503, "Commerce configuration requires administrator review.")
        if selected == "shopify":
            if request.method == "GET":
                return redirect(url_for("commerce.settings"))
            abort(409, "Woo workflows are disabled for this Shopify installation. Use the separate reviewed Shopify Simple draft workflow.")
