"""Administrator configuration/discovery/drafts. GET never contacts either store."""
import json
from flask import Blueprint, abort, render_template, request, redirect, url_for, jsonify, current_app, g, flash
from flask_login import login_required, current_user
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SelectField, HiddenField

from app import commerce_config as config
from app.shopify_connection import ShopifyAdapter, ShopifyConfiguration, ShopifyError, VERSIONS

commerce = Blueprint("commerce", __name__)


def products_return(product_id=None):
    from urllib.parse import urlsplit
    candidate = request.form.get("return_to") or request.args.get("return_to") or request.referrer or ""
    parsed = urlsplit(candidate[:2048])
    if (not parsed.netloc or parsed.netloc == request.host) and parsed.path == url_for("main.products") and "\r" not in candidate and "\n" not in candidate:
        return parsed.path + ("?"+parsed.query if parsed.query else "") + ("#"+parsed.fragment if parsed.fragment else "")
    return url_for("main.products") + ("#product-"+str(product_id) if product_id else "")


@commerce.route("/products/<int:product_id>/shopify/progress", methods=["POST"])
@login_required
def shopify_progress(product_id):
    if not current_user.is_admin: abort(403)
    form = FlaskForm()
    if not form.validate_on_submit(): abort(400)
    from app.shopify_progress import receipt
    from sqlalchemy.exc import SQLAlchemyError
    try:
        return jsonify(receipt(product_id,request.form.get("review",""))), 200, {"Cache-Control":"no-store"}
    except ShopifyError as exc:
        return jsonify(error=str(exc)),400,{"Cache-Control":"no-store"}
    except SQLAlchemyError:
        from app import db
        db.session.rollback()
        return jsonify(error="Receipt temporarily unavailable (database busy). Inspect retained History; no Shopify failure or retry is assumed."),503,{"Cache-Control":"no-store"}


@commerce.route("/api/shopify/status", methods=["POST"])
@login_required
def refresh_status():
    # Global CSRFProtect validates X-CSRFToken before reaching this JSON endpoint.
    if not current_user.is_admin:
        abort(403)
    from app.models import Product
    from app.shopify_status import refresh
    from app.shopify_products import states_for
    from app.utils.operation_control import CatalogueOperationActive
    body = request.get_json(silent=True) or {}
    ids = body.get("ids", [])
    if not isinstance(ids, list) or len(ids) > 25 or any(type(i) is not int or i < 1 for i in ids):
        abort(400)
    after = body.get("after", 0)
    if type(after) is not int or after < 0:
        abort(400)
    query = Product.query.filter(Product.product_type.in_(["simple","variable"]), Product.catalogue_status=="active")
    all_scope = body.get("scope") == "all"
    products = (query.filter(Product.id > after) if all_scope else query.filter(Product.id.in_(ids))).order_by(Product.id).limit(25).all()
    try:
        result = refresh(products, force=body.get("force") is True)
    except CatalogueOperationActive:
        return jsonify(busy=True, message="Another catalogue operation is active; try again shortly."), 409
    except (ShopifyError, config.CommerceConfigError) as exc:
        return jsonify(error=str(exc), pending=[]), 400
    result["states"] = states_for(products)
    result["next_after"] = products[-1].id if all_scope and len(products) == 25 else None
    return jsonify(result), 200, {"Cache-Control": "no-store"}


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
    from sqlalchemy.exc import SQLAlchemyError
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
                inventory_only = request.form.get('inventory_only') == 'yes'
                customisations_only = request.form.get('customisations_only') == 'yes'
                if inventory_only and customisations_only:
                    raise ShopifyError('Choose only one limited review scope.')
                plan, token = service.preview(product, scope='customisations' if customisations_only else 'inventory' if inventory_only else 'full')
            elif request.form.get("action") == "confirm":
                review = request.form.get("review", "")
                from app.shopify_progress import receipt
                target = product_id or request.form.get("product_id",type=int)
                if not target: abort(400)
                prior = receipt(target,review)
                if prior["state"] != "waiting":
                    if request.headers.get("Accept") == "application/json":
                        return jsonify({**prior,"return_url":products_return(target)}), 200, {"Cache-Control":"no-store"}
                    return redirect(prior.get("history_url") or products_return(target))
                g.shopify_review_ticket = service.digest(review)
                from app.manual_publishing import accept
                accepted = accept(target,review,request.form.get("acknowledge") == "yes")
                result = receipt(target,review)
                if request.headers.get("Accept") == "application/json":
                    return jsonify({**result,"return_url":url_for('main.operation_detail',operation_id=accepted.id)}), 202, {"Cache-Control":"no-store"}
                return redirect(url_for('main.operation_detail',operation_id=accepted.id))
            else:
                abort(400)
        except (ShopifyError, config.CommerceConfigError, CatalogueOperationActive) as exc:
            error = str(exc)
            if request.headers.get("Accept") == "application/json":
                return jsonify(state="failed",message=error,return_url=products_return(product_id)),400,{"Cache-Control":"no-store"}
        except SQLAlchemyError:
            db.session.rollback()
            error="Operation receipt/database is temporarily unavailable. Inspect History and reconcile any uncertain stage; do not resubmit automatically."
            if request.headers.get("Accept") == "application/json":
                return jsonify(state="receipt_unavailable",message=error,return_url=products_return(product_id)),503,{"Cache-Control":"no-store"}
    products = Product.query.filter(Product.product_type.in_(["simple","variable"]),Product.catalogue_status=="active").order_by(Product.title).limit(500).all()
    identities_query = ShopifyProductIdentity.query
    if scoped_product:
        identities_query = identities_query.filter_by(product_id=product_id)
    identities = identities_query.order_by(ShopifyProductIdentity.updated_at.desc()).limit(30).all()
    from app.shopify_simple_fields import review_rows
    return render_template("shopify_drafts.html", form=form, plan=plan, token=token, error=error,
        review_rows=review_rows(plan) if plan else [],
        return_to=products_return(product_id),
        products=products, identities=identities, scoped_product=scoped_product,
        selected_id=product_id or request.form.get("product_id", type=int) or request.args.get("product_id", type=int)), 400 if error else 200, {"Cache-Control": "no-store"}


@commerce.route("/products/<int:product_id>/shopify/delete", methods=["GET", "POST"])
@login_required
def delete_shopify_product(product_id):
    from app import db, shopify_lifecycle as lifecycle
    from app.models import Product
    from app.utils.operation_control import CatalogueOperationActive
    if not current_user.is_admin:
        abort(403)
    product = db.get_or_404(Product, product_id)
    form, plan, token, error = FlaskForm(), None, None, None
    if request.method == "POST":
        if not form.validate_on_submit():
            abort(400)
        try:
            if request.form.get("action") == "review":
                plan, token = lifecycle.review(product)
            elif request.form.get("action") == "delete":
                row = lifecycle.confirm(request.form.get("review", ""), request.form.get("acknowledge") == "yes",
                    request.form.get("sku", ""), expected_product_id=product_id)
                return redirect(url_for("commerce.draft_identity", identity_id=row.id))
            else:
                abort(400)
        except (ShopifyError, config.CommerceConfigError, CatalogueOperationActive) as exc:
            error = str(exc)
    return render_template("shopify_delete.html", product=product, form=form, plan=plan, token=token, error=error), 400 if error else 200


@commerce.route("/products/<int:product_id>/shopify/native-dimensions", methods=["GET", "POST"])
@login_required
def native_dimensions(product_id):
    from app import db, shopify_native_dimensions as native
    from app.models import Product, ShopifyProductIdentity
    from app.utils.operation_control import CatalogueOperationActive
    from app.shopify_drafts import evidence as product_evidence
    if not current_user.is_admin:
        abort(403)
    product = db.get_or_404(Product, product_id)
    form, plan, token, error, saved = FlaskForm(), None, None, None, False
    if request.method == "POST":
        if not form.validate_on_submit():
            abort(400)
        try:
            action = request.form.get("action")
            if action == "preview":
                plan, token = native.check(product)
            elif action == "confirm":
                native.confirm(request.form.get("review", ""), request.form.get("acknowledge") == "yes", expected_product_id=product_id)
                saved = True
            elif action == "reconcile":
                from app.shopify_drafts import matching_identity, configuration
                adapter = ShopifyAdapter()
                shop = adapter.test_connection(configuration())
                row = matching_identity(shop["shop_id"], product)
                if not row:
                    raise ShopifyError("No retained Shopify product identity; no native reconciliation is possible.")
                native.reconcile(row, adapter)
                saved = True
            else:
                raise ShopifyError("Choose a supported native dimension action.")
        except (ShopifyError, config.CommerceConfigError, CatalogueOperationActive) as exc:
            error = str(exc)
    rows = ShopifyProductIdentity.query.filter_by(product_id=product_id).all()
    row = rows[0] if len(rows) == 1 else None
    return render_template("shopify_native_dimensions.html", product=product, row=row,
        form=form, plan=plan, token=token, error=error, saved=saved,
        desired=native.desired(product), state=native.local_status(product, row),
        evidence=product_evidence(row).get('native_dimensions') if row else None), 400 if error else 200, {"Cache-Control": "no-store"}


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
    resume_plan,resume_token=None,None
    if request.method == "POST":
        if not form.validate_on_submit():
            abort(400)
        try:
            approval = request.form.get("approval")
            if approval and request.form.get("acknowledge_mapping") != "yes":
                raise ShopifyError("Explicit approval of the displayed media mapping is required.")
            if request.form.get('action')=='review_creation_completion':
                from app.shopify_variables import preview
                from app.models import Product
                resume_plan,resume_token=preview(db.get_or_404(Product,row.product_id),resume_creation=True)
            elif row.state in {"deleting", "delete_uncertain"}:
                from app.shopify_lifecycle import reconcile
                reconcile(row)
            else:
                from app.shopify_notifications import complete
                if row.product_id and not approval and not request.form.get('candidate_id') and complete(row, service.evidence(row)):
                    # Routine drift inspection is not uncertain-mutation recovery.
                    # Reuse the same scoped, durable read-only task as Run Now;
                    # its comparison hook can reopen valid scanned Live work.
                    from app import scheduled_tasks
                    run_id = scheduled_tasks.enqueue(scheduled_tasks.KEY, manual=True, product_ids=[row.product_id])
                    return redirect(url_for('tasks.run_detail', run_id=run_id))
                service.reconcile(row, request.form.get("candidate_id") or None, approval=approval)
        except (ShopifyError, config.CommerceConfigError, CatalogueOperationActive) as exc:
            error = str(exc)
        except IntegrityError:
            db.session.rollback()
            error = "Remote ID is already reserved by another local mapping. Review both identities; no duplicate mapping was saved."
    data = service.evidence(row)
    from app.operations_views import product_authority, local_time
    return render_template("shopify_draft_identity.html", form=form, row=row, data=data,
        publisher_authority=product_authority(row.product_id), local_time=local_time,
        approval=service.recovery_token(row), error=error,resume_plan=resume_plan,resume_token=resume_token), 400 if error else 200, {"Cache-Control": "no-store"}


@commerce.route("/taxonomy/shopify", methods=["GET", "POST"])
@login_required
def taxonomy_mappings():
    from app import shopify_taxonomy as service
    from app import shopify_taxonomy_batch as batch
    from app.models import ShopifyCollectionIdentity, CatalogueOperation
    from app import db
    from app.utils.operation_control import CatalogueOperationActive
    from sqlalchemy.exc import IntegrityError
    from app.taxonomy_routes import workspace_context
    if not current_user.is_admin:
        abort(403)
    form, plan, token, error, saved = FlaskForm(), None, None, None, False
    batch_row, batch_plan, batch_token = None, None, None
    if request.method == "POST":
        if not form.validate_on_submit():
            abort(400)
        try:
            if request.form.get("action") == "batch_review":
                cursor = request.form.get("after", "0")
                if not cursor.isascii() or not cursor.isdigit() or len(cursor) > 12:
                    raise ShopifyError("Invalid product scope cursor. Reload Taxonomy Sync and review again.")
                batch_row, batch_plan, batch_token = batch.review(request.form.getlist("sources"), after=int(cursor))
            elif request.form.get("action") == "batch_confirm":
                batch_row = batch.approve(request.form.get("review", ""), request.form.get("acknowledge") == "yes")
            elif request.form.get("action") == "batch_recover":
                batch_row = db.session.get(CatalogueOperation, request.form.get("batch_id"))
                if not batch_row or batch_row.operation_type != "shopify_taxonomy_batch":
                    abort(404)
                batch.recover_batch(batch_row)
            elif request.form.get("action") == "review":
                plan, token = service.review_mapping(request.form.get("source", ""), request.form.get("mode", ""),
                    request.form.get("remote_id") or None, request.form.get("standard") or None)
            elif request.form.get("action") == "confirm":
                service.confirm_mapping(request.form.get("review", ""), request.form.get("acknowledge") == "yes")
                saved = True
            else:
                abort(400)
        except (ShopifyError, config.CommerceConfigError, CatalogueOperationActive) as exc:
            error = str(exc)
            action = request.form.get("action")
            action = action if action in {"batch_review", "batch_confirm", "batch_recover", "review", "confirm"} else "invalid"
            current_app.logger.warning("Shopify taxonomy request rejected: action=%s, type=%s", action, type(exc).__name__)
        except IntegrityError:
            db.session.rollback()
            error = "A Shopify identity is already reserved by another mapping. Inspect retained mappings and rebuild the review; do not retry creation."
    if request.method == "GET" and request.args.get("batch"):
        batch_row = db.session.get(CatalogueOperation, request.args["batch"])
        if not batch_row or batch_row.operation_type != "shopify_taxonomy_batch":
            abort(404)
    batch_data = json.loads(batch_row.scope) if batch_row else None
    return render_template("taxonomy/workspace.html", **workspace_context(active_tab="shopify"),
        batch_row=batch_row, batch_plan=batch_plan, batch_token=batch_token, batch_data=batch_data,
        execution=batch.execution_token(batch_row) if batch_row and batch_row.status == "approved" else None,
        plan=plan, token=token, error=error, saved=saved), 400 if error else 200, {"Cache-Control": "no-store"}


@commerce.route("/taxonomy/shopify/attributes/<int:identity_id>", methods=["GET", "POST"])
@login_required
def attribute_identity(identity_id):
    from app import db, shopify_attributes as attributes
    from app.models import ShopifyAttributeIdentity
    from app import shopify_drafts as drafts
    from app.utils.operation_control import operation_context, CatalogueOperationActive
    if not current_user.is_admin: abort(403)
    row=db.get_or_404(ShopifyAttributeIdentity,identity_id)
    form,error=FlaskForm(),None
    if request.method=="POST":
        if not form.validate_on_submit(): abort(400)
        try:
            with operation_context("shopify_status_refresh",{"mode":"attribute_reconciliation","identity_id":row.id}):
                configuration=drafts.configuration(); adapter=ShopifyAdapter(); shop=adapter.test_connection(configuration)
                if shop["shop_id"]!=row.store_id or not attributes.READ_SCOPES.issubset(shop["scopes"]):
                    raise ShopifyError("Restore this verified store and attribute read scopes before reconciliation.")
                attributes.reconcile(row,adapter,configuration)
        except (ShopifyError, config.CommerceConfigError, CatalogueOperationActive) as exc: error=str(exc)
    return render_template("taxonomy/attribute_identity.html",row=row,form=form,error=error,data=json.loads(row.evidence_json))


@commerce.route("/taxonomy/navigation/check", methods=["POST"])
@login_required
def check_navigation():
    from app import shopify_navigation
    from app.utils.operation_control import CatalogueOperationActive
    if not current_user.is_admin: abort(403)
    form=FlaskForm()
    if not form.validate_on_submit(): abort(400)
    try:
        after=request.form.get("after","0")
        if not after.isascii() or not after.isdigit() or len(after)>12:
            raise ShopifyError("Invalid navigation cursor.")
        cursor=shopify_navigation.check(after=int(after))
        return redirect(url_for("taxonomy.index",tab="navigation",after=cursor or 0,checked="yes"))
    except (ShopifyError,config.CommerceConfigError,CatalogueOperationActive) as exc:
        from flask import flash
        flash(str(exc),"warning")
        return redirect(url_for("taxonomy.index",tab="navigation"))


@commerce.route("/api/shopify/taxonomy/step", methods=["POST"])
@login_required
def taxonomy_step():
    if not current_user.is_admin:
        abort(403)
    from app import shopify_taxonomy_batch as batch
    from app.utils.operation_control import CatalogueOperationActive
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("execution"), str) or len(body["execution"]) > 4096:
        return jsonify(error="Invalid taxonomy execution request. Open the retained batch review; no write was attempted."), 400
    try:
        row = batch.execute_one(body["execution"])
        data = json.loads(row.scope)
        return jsonify(status=row.status, completed=len(data["results"]), total=len(data["plan"]["steps"]),
            excluded=data["plan"]["totals"]["excluded_products"], history=url_for("commerce.taxonomy_mappings", batch=row.id))
    except (ShopifyError, config.CommerceConfigError, CatalogueOperationActive) as exc:
        return jsonify(error=str(exc)), 409


@commerce.route('/shopify/publications/discover',methods=['POST'])
@login_required
def discover_publications():
    if not current_user.is_admin: abort(403)
    from app.shopify_publication import discover
    try: return jsonify(channels=discover())
    except (ShopifyError,config.CommerceConfigError) as exc: return jsonify(error=str(exc)),400


@commerce.route('/products/<int:product_id>/shopify/publication',methods=['GET','POST'])
@login_required
def publication_review(product_id):
    if not current_user.is_admin: abort(403)
    from app import db, shopify_drafts as drafts, shopify_publication as service
    from app.models import Product
    from app.utils.operation_control import CatalogueOperationActive
    product=db.get_or_404(Product,product_id)
    form=FlaskForm();plan=token=error=None
    if request.method=='POST':
        if not form.validate_on_submit(): abort(400)
        try:
            if request.form.get('action')=='preview': plan,token=service.preview(product)
            elif request.form.get('action')=='confirm':
                from app.shopify_progress import accept,finalise_unfinished
                review=request.form.get('review','')
                accepted=accept(product.id,review,request.form.get('acknowledge')=='yes')
                try: row=drafts.confirm(review,True,expected_product_id=product.id)
                finally: finalise_unfinished(accepted.id)
                return redirect(url_for('commerce.draft_identity',identity_id=row.id))
            else: abort(400)
        except (ShopifyError,config.CommerceConfigError,CatalogueOperationActive) as exc: error=str(exc)
    return render_template('shopify_stage_review.html',product=product,form=form,plan=plan,token=token,error=error,stage='publication'),400 if error else 200


@commerce.route('/products/<int:product_id>/shopify/sale',methods=['GET','POST'])
@login_required
def sale_review(product_id):
    if not current_user.is_admin: abort(403)
    from app import db,shopify_sales as service
    from app.models import Product,ShopifySaleSchedule
    from app.utils.operation_control import CatalogueOperationActive
    product=db.get_or_404(Product,product_id);form=FlaskForm();plan=token=error=None
    if request.method=='POST':
        if not form.validate_on_submit(): abort(400)
        try:
            if request.form.get('action') in {'preview','confirm'}:
                raise ShopifyError('Open Review Sale in the Product Editor. Save current JSON and complete its scan before reviewing or authorising.')
            elif request.form.get('action')=='pause':
                row=db.get_or_404(ShopifySaleSchedule,request.form.get('schedule_id',''))
                if row.product_id!=product.id or request.form.get('acknowledge')!='yes': abort(400)
                service.pause(row)
                return redirect(url_for('commerce.sale_review',product_id=product.id))
            else: abort(400)
        except (ShopifyError,ValueError,config.CommerceConfigError,CatalogueOperationActive) as exc: error=str(exc)
    return render_template('shopify_stage_review.html',product=product,form=form,plan=plan,token=token,error=error,stage='sale',sale=service.summary(product)),400 if error else 200


@commerce.route('/products/<int:product_id>/shopify/sale/editor',methods=['POST'])
@login_required
def sale_editor_review(product_id):
    """Admin/CSRF saved-editor handoff. Status/Preview perform no Shopify writes."""
    if not current_user.is_admin: abort(403)
    from app import db, sale_editor as service
    from app.models import Product
    from app.utils.operation_control import CatalogueOperationActive
    from sqlalchemy.exc import SQLAlchemyError
    product=db.get_or_404(Product,product_id)
    payload=request.get_json(silent=True)
    if not isinstance(payload,dict) or payload.get('action') not in {'status','preview','confirm'}:
        return jsonify(ready=False,error='Choose a saved-editor sale action.'),400
    try:
        handler={'status':service.status,'preview':service.review,'confirm':service.authorise}[payload['action']]
        return jsonify(handler(product,payload)),200,{'Cache-Control':'no-store'}
    except (ValueError,ShopifyError,CatalogueOperationActive) as exc:
        return jsonify(ready=False,error=str(exc)),409,{'Cache-Control':'no-store'}
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify(ready=False,error='Database temporarily unavailable. Inspect saved schedule status before retrying an authorisation.'),503,{'Cache-Control':'no-store'}
    except OSError:
        return jsonify(ready=False,error='Saved source is unavailable. Restore the catalogue and complete its scan before Review Sale.'),409,{'Cache-Control':'no-store'}


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
