# app/routes.py
import os
import json
import re
import uuid
from datetime import datetime

from flask import (
    Blueprint,
    render_template,
    redirect,
    url_for,
    flash,
    request,
    jsonify,
    Response,
    current_app,
    session,
    send_file,
    abort,
)
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.exceptions import BadRequest, NotFound
from werkzeug.security import check_password_hash, generate_password_hash
from sqlalchemy.orm import joinedload, selectinload

from app import db, csrf
from app import onboarding
from app.forms import (
    LoginForm,
    SetupAdminForm,
    InitialSettingsForm,
)
from app.models import (
    User,
    Settings,
    CatalogueOperation,
    Collection,
    Product,
    ProductAsset,
    Variation,
)
from app.product_relationships import (
    RelationshipValidationError,
    apply_mutual_cross_sells,
    apply_update as apply_relationship_update,
    preview_mutual_cross_sells,
    preview_update as preview_relationship_update,
    relationship_workspace,
    search_products as search_relationship_products,
)
from app.relationships_workspace import (
    build_relationship_browser,
    family_search,
    mutual_proposal,
    parse_relationship_filters,
    verify_mutual_proposal,
)
from app.utils.scan_runner import (
    start_scan,
    stream_lines,
    get_progress,
    _runs,
)
from app.utils.operation_live import persisted_live_state, persisted_log_page
from app.utils.operation_control import (
    CatalogueOperationActive,
    acquire_catalogue_operation,
    finish_catalogue_operation,
    get_active_operation,
)
from app.utils.reconstruction import detect_setup_state, run_reconstruction
from app.product_info import (
    EXAMPLE_NAMES,
    FIELD_INVENTORY,
    TEMPLATE_NAMES,
    load_example,
    load_schema,
    load_template,
    validate_product_info,
)
from app.dashboard import (
    METADATA_ISSUE_DEFINITIONS,
    build_dashboard_data,
)
from app.catalogue_views import saved_read_view
from app.products_browser import (
    build_products_data,
    build_variation_data,
    parse_products_filters,
)
from app.utils.atomic_files import atomic_write_json, atomic_write_text
from app.utils.backup_retention import create_metadata_backup
from app.utils.temporary_cleanup import cleanup_metadata_temporaries
from app.catalogue_images import (
    product_image_diagnostics,
    resolve_product_catalogue_image,
    resolve_variation_catalogue_image,
    variation_image_diagnostics,
)
from app.metadata_workspace import (
    affected_products_page,
    editor_workspace,
    metadata_source,
    product_workspace,
    variation_page,
)
from app.collections_workspace import (
    build_collection_detail,
    build_collections_browser,
    parse_collection_filters,
    parse_product_pagination,
)
from app.collection_identity import collection_display_name
from app.utils.discord import (
    notify_editor_saved,
    notify_override_created,
    notify_override_removed,
)
from app.operations_workspace import (
    SCAN_MODES,
    operation_detail_workspace,
    operation_view,
    operations_browser,
    parse_operation_filters,
    scanner_readiness,
)
from app.woocommerce_connection import (
    WooConnectionError,
    build_woocommerce_workspace,
    execute_connection_test,
)
from app.woo_publish_preview import (
    LinkCandidateError, PreviewError, cached_plan, generate_publish_plan,
    link_candidate_identity, operation_summary as woo_preview_operation_summary,
    plan_is_stale, prepare_link_candidate_review, preview_landing, scope_estimate,
    WooUnlinkError, prepare_woo_unlink_review, unlink_woo_identity,
)
from app.woo_controlled_publish import (
    ControlledPublishError,
    prepare_publish_confirmation,
    resume_confirmation,
    start_publish_operation,
)
from app.image_preparation import (
    configured_intake_root,
    resolve_intake_image_token,
)

main = Blueprint("main", __name__)

from app.customisations import CustomisationDefinitionError


@main.app_errorhandler(CustomisationDefinitionError)
def customisation_definition_error(error):
    if request.is_json:
        return jsonify(error=str(error)), 409
    return render_template('customisation_invalid.html', error=str(error)), 409


def _operation_conflict(error):
    active = error.active
    return (
        jsonify(
            {
                "error": "catalogue_operation_active",
                "message": (
                    f"Another catalogue operation ({active['operation_type']}) "
                    "is already active."
                ),
                "active_operation": active,
            }
        ),
        409,
    )


def _catalogue_summary_counts():
    """Read-only projection totals used by setup completion presentation."""
    return {
        "collections": Collection.query.count(),
        "products": Product.query.count(),
        "variations": Variation.query.count(),
    }


# ---------- Catalogue Intake: preview, explicit preparation and registration ----------


# Intake remains at its existing entry point; each preparation stage is optional.
from app import intake_direct_ui


@main.route("/image-preparation")
@login_required
def image_preparation():
    return intake_direct_ui.index()


@main.route("/image-preparation/stage/<stage>")
@login_required
def image_preparation_direct_stage(stage):
    if stage not in intake_direct_ui.service.STAGES: abort(404)
    return intake_direct_ui.page(stage)


@main.route("/image-preparation/result/<operation_id>")
@login_required
def image_preparation_direct_result(operation_id):
    return intake_direct_ui.result(operation_id)


@main.route("/image-preparation/result/<operation_id>/continue", methods=["POST"])
@login_required
def image_preparation_direct_continue(operation_id):
    return intake_direct_ui.recover(operation_id)


def _intake_stage(stage):
    @login_required
    def page():
        return intake_direct_ui.page(stage)
    @login_required
    def confirm():
        return intake_direct_ui.confirm(stage)
    return page, confirm


# Bookmarked URLs and historical links now use the same direct-file plan.
for _stage, _names in {
    "group": ("image_preparation_group",),
    "folders": ("image_preparation_folders", "image_preparation_folders_edit"),
    "rename": ("image_preparation_rename",),
    "metadata": ("image_preparation_metadata", "image_preparation_metadata_edit"),
    "handoff": ("image_preparation_handoff", "image_preparation_handoff_review"),
}.items():
    _page, _confirm = _intake_stage(_stage)
    for _name in _names:
        _suffix = _name.removeprefix("image_preparation_").replace("_edit", "/edit").replace("_review", "/review")
        main.add_url_rule("/image-preparation/" + _suffix, endpoint=_name, view_func=_page)
    main.add_url_rule("/image-preparation/" + _stage + "/preview",
        endpoint="image_preparation_" + _stage + "_preview", view_func=_page, methods=["POST"])
    main.add_url_rule("/image-preparation/" + _stage + "/confirm",
        endpoint="image_preparation_" + _stage + "_confirm", view_func=_confirm, methods=["POST"])


@main.route("/image-preparation/import-structured", methods=["GET", "POST"])
@main.route("/image-preparation/import-structured/preview", methods=["POST"])
@main.route("/image-preparation/import-structured/confirm", methods=["POST"])
@login_required
def image_preparation_import_structured():
    return redirect(url_for("main.image_preparation_folders_edit", path=request.values.get("path", "")))


@main.route("/image-preparation/next/<token>")
@login_required
def image_preparation_next(token):
    # Historical operation links stay read-only and are revalidated; the new
    # independently accessible stages do not depend on this legacy guidance.
    from app.intake_navigation import decode_navigation_token, navigation_destination, prepared_result_navigation
    value = decode_navigation_token(token)
    navigation = None
    if value:
        try:
            navigation = prepared_result_navigation(configured_intake_root(), value['result'])
        except (OSError, ValueError):
            pass
    return redirect(navigation_destination(navigation) or url_for('main.image_preparation'))


@main.route("/image-preparation/handoff/catalogue")
@login_required
def image_preparation_handoff_catalogue():
    return redirect(url_for("main.products"))


@main.route("/intake-images/<token>")
@login_required
def intake_image(token):
    image_path = resolve_intake_image_token(token)
    if image_path is None:
        abort(404)
    response = send_file(image_path, conditional=True, max_age=3600)
    response.cache_control.public = False
    response.cache_control.private = True
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response

# ---------- Dashboard ----------


@main.route("/")
def dashboard():
    if not User.query.first():
        return redirect(url_for("main.setup"))
    if not current_user.is_authenticated:
        return redirect(url_for("main.login"))
    if onboarding.pending():
        return redirect(url_for("main.initial_settings"))
    from app.orders_workspace import overview
    response=current_app.make_response(render_template("dashboard.html", dashboard=build_dashboard_data(),
        orders_overview=overview(include_test=request.args.get('include_test')=='1') if current_user.is_admin else None))
    response.headers['Cache-Control']='no-store, private'
    return response


# ---------- Products page + API ----------


@main.route("/products")
@login_required
def products():
    try:
        filters = parse_products_filters(request.args)
    except ValueError as error:
        raise BadRequest(str(error)) from error
    issue_key = filters["issue"]
    issue_filter = None
    if issue_key:
        issue_filter = {
            "key": issue_key,
            "label": "Needs attention: description, images or SEO metadata" if issue_key == "all" else METADATA_ISSUE_DEFINITIONS[issue_key]["label"],
        }
    collections = [
        {"id": collection.id, "name": collection_display_name(collection)}
        for collection in Collection.query.order_by(Collection.id.asc()).all()
    ]
    collections.sort(key=lambda item: (item["name"].casefold(), item["id"]))
    return render_template(
        "edit_products.html",
        issue_filter=issue_filter,
        products_filters=filters,
        product_collections=collections,
    )


@main.route("/api/edit_products")
@login_required
def api_products():
    try:
        filters = parse_products_filters(request.args)
    except ValueError as error:
        raise BadRequest(str(error)) from error
    payload = build_products_data(filters)
    issue_key = filters["issue"]
    payload["filter"] = (
        {
            "key": issue_key,
            "label": METADATA_ISSUE_DEFINITIONS[issue_key]["label"],
        }
        if issue_key
        else None
    )
    if issue_key:
        for item in payload["items"]:
            item["issue"] = {
                **payload["filter"],
                "entity_type": "parent_product",
                "variation_sku": None,
                "variation_attributes": [],
            }
    return jsonify(payload)


@main.route('/api/products/pricing')
@login_required
def api_product_pricing():
    """Bounded local presentation revalidation at a known sale boundary. No API writes/reads."""
    from app.sale_pricing import catalogue
    from app.shopify_products import states_for
    try:
        ids = [int(value) for value in request.args.get('ids', '').split(',')]
        if not 1 <= len(ids) <= 100 or len(set(ids)) != len(ids) or any(i < 1 for i in ids):
            raise ValueError()
    except ValueError:
        return jsonify(error='Choose one to 100 distinct local products.'), 400
    from sqlalchemy.orm import selectinload
    from app.models import Variation
    products = Product.query.filter(Product.id.in_(ids)).options(
        selectinload(Product.variations).selectinload(Variation.attributes),
        selectinload(Product.variations).selectinload(Variation.images),
        selectinload(Product.images), selectinload(Product.assets), selectinload(Product.tags)).all()
    states = states_for(products)
    return jsonify(items=[{'id':p.id, 'price':catalogue(p), 'shopify':states[p.id]} for p in products]), 200, {'Cache-Control':'no-store'}


@main.route("/api/products/<int:product_id>/variations")
@login_required
def api_product_variations(product_id):
    product = (
        Product.query.options(
            selectinload(Product.assets),
            selectinload(Product.images),
        )
        .filter_by(id=product_id, product_type="variable")
        .first_or_404()
    )
    return jsonify(
        build_variation_data(
            product,
            include_all=request.args.get("all") == "1",
        )
    )


@main.route("/products/<int:product_id>")
@login_required
def product_detail(product_id):
    product = (
        Product.query.options(
            joinedload(Product.collection),
            selectinload(Product.assets),
            selectinload(Product.images),
            selectinload(Product.categories),
            selectinload(Product.tags),
            selectinload(Product.attributes),
        )
        .filter_by(id=product_id)
        .first_or_404()
    )
    from app.shopify_products import states_for
    shopify = states_for([product])[product.id]
    workspace = product_workspace(product)
    from app.publishing import projected_publishing_intent
    workspace["publishing_intent"].update(projected_publishing_intent(shopify["intent_value"]))
    if shopify["intent_value"] is None:
        workspace["publishing_intent"].update(source="Not authored or invalid", inheritance="Resolution required")
    from app.operations_views import product_authority, local_time
    return render_template("product_detail.html", workspace=workspace, shopify=shopify,
        publisher_authority=product_authority(product.id), local_time=local_time)


def _relationship_payload():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise RelationshipValidationError("A valid JSON request is required.")
    return payload


def _relationship_error(error):
    return jsonify({"ok": False, "error": str(error), "details": error.details}), 422


@main.route("/api/products/<int:product_id>/relationship-search")
@login_required
def api_product_relationship_search(product_id):
    product = db.get_or_404(Product, product_id)
    return jsonify(
        {
            "ok": True,
            "query": (request.args.get("q") or "")[:120],
            "items": search_relationship_products(product.id, request.args.get("q")),
        }
    )


@main.route("/api/products/<int:product_id>/relationships")
@login_required
def api_product_relationships(product_id):
    product = db.get_or_404(Product, product_id)
    return jsonify({"ok": True, "relationships": relationship_workspace(product)})


@main.route("/api/products/<int:product_id>/relationships/preview", methods=["POST"])
@login_required
def api_product_relationship_preview(product_id):
    product = db.get_or_404(Product, product_id)
    try:
        payload = _relationship_payload()
        preview = preview_relationship_update(
            product,
            payload.get("relationship_type"),
            payload.get("target_skus"),
            mode=payload.get("mode", "replace"),
        )
    except RelationshipValidationError as error:
        return _relationship_error(error)
    return jsonify({"ok": True, "preview": preview})


@main.route("/api/products/<int:product_id>/relationships/confirm", methods=["POST"])
@login_required
def api_product_relationship_confirm(product_id):
    product = db.get_or_404(Product, product_id)
    try:
        payload = _relationship_payload()
        if payload.get("confirm") is not True:
            raise RelationshipValidationError("Explicit confirmation is required.")
        result = apply_relationship_update(
            product,
            payload.get("relationship_type"),
            payload.get("target_skus"),
            mode=payload.get("mode", "replace"),
        )
    except RelationshipValidationError as error:
        return _relationship_error(error)
    except CatalogueOperationActive as error:
        return _operation_conflict(error)
    return jsonify({"ok": True, **result})


@main.route("/api/product-relationships/mutual-cross-sells/preview", methods=["POST"])
@login_required
def api_mutual_cross_sell_preview():
    try:
        payload = _relationship_payload()
        preview = preview_mutual_cross_sells(payload.get("product_skus"))
    except RelationshipValidationError as error:
        return _relationship_error(error)
    return jsonify({"ok": True, "preview": preview})


@main.route("/api/product-relationships/mutual-cross-sells/confirm", methods=["POST"])
@login_required
def api_mutual_cross_sell_confirm():
    try:
        payload = _relationship_payload()
        if payload.get("confirm") is not True:
            raise RelationshipValidationError("Explicit confirmation is required.")
        result = apply_mutual_cross_sells(payload.get("product_skus"))
    except RelationshipValidationError as error:
        return _relationship_error(error)
    except CatalogueOperationActive as error:
        return _operation_conflict(error)
    return jsonify({"ok": True, **result})


@main.route("/relationships")
@login_required
def relationships():
    try:
        filters = parse_relationship_filters(request.args)
    except ValueError as error:
        raise BadRequest(str(error)) from error
    data = build_relationship_browser(filters)
    collections = [
        {"id": row.id, "name": collection_display_name(row)}
        for row in Collection.query.order_by(Collection.name.asc(), Collection.id.asc()).all()
    ]
    return render_template("relationships.html", workspace=data, collections=collections)


@main.route("/relationships/mutual")
@login_required
def relationships_mutual():
    query = (request.args.get("q") or "").strip()[:191]
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError as error:
        raise BadRequest("Invalid family-search page") from error
    results = family_search(query, page=page) if query else None
    return render_template("relationships_mutual.html", search_query=query, search_results=results)


@main.route("/relationships/mutual/preview", methods=["POST"])
@login_required
def relationships_mutual_preview():
    try:
        payload = _relationship_payload()
        preview = mutual_proposal(payload.get("product_skus"))
    except RelationshipValidationError as error:
        return _relationship_error(error)
    return jsonify({"ok": True, "preview": preview})


@main.route("/relationships/mutual/confirm", methods=["POST"])
@login_required
def relationships_mutual_confirm():
    try:
        payload = _relationship_payload()
        if payload.get("acknowledged") is not True:
            raise RelationshipValidationError("Explicit acknowledgement is required.")
        preview = verify_mutual_proposal(
            payload.get("product_skus"), payload.get("proposal_digest")
        )
        result = apply_mutual_cross_sells(
            preview["selected_skus"], verified_preview=preview
        )
    except RelationshipValidationError as error:
        return _relationship_error(error)
    except CatalogueOperationActive as error:
        return _operation_conflict(error)
    return jsonify({"ok": True, **result})


@main.route("/api/products/<int:product_id>/detail-variations")
@login_required
def api_product_detail_variations(product_id):
    product = Product.query.filter_by(id=product_id, product_type="variable").first_or_404()
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError as error:
        raise BadRequest("Invalid variation page") from error
    return jsonify(variation_page(product.id, page=page, per_page=24))


@main.route("/collections/<int:collection_id>/metadata")
@login_required
def collection_metadata_edit(collection_id):
    if not current_user.is_admin:
        abort(403)
    collection = db.get_or_404(Collection, collection_id)
    product = (
        Product.query.options(joinedload(Product.collection))
        .filter_by(collection_id=collection.id)
        .order_by(Product.title.asc(), Product.id.asc())
        .first()
    )
    if product is None:
        abort(404)
    return render_template(
        "metadata_editor.html", workspace=editor_workspace(product, "shared")
    )


@main.route("/api/collections/<int:collection_id>/affected-products")
@login_required
def api_collection_affected_products(collection_id):
    collection = db.get_or_404(Collection, collection_id)
    try:
        page = max(1, int(request.args.get("page", "1")))
        per_page = int(request.args.get("per_page", "12"))
    except ValueError as error:
        raise BadRequest("Invalid affected-product pagination") from error
    if per_page not in {1, 6, 12, 24}:
        per_page = 12
    return jsonify(affected_products_page(collection, page, per_page))


@main.route("/catalogue-images/products/<int:product_id>")
@login_required
def catalogue_product_image(product_id):
    """Serve one projected product's primary source image from the catalogue."""

    product = (
        Product.query.options(
            selectinload(Product.images),
            selectinload(Product.variations).selectinload(Variation.images),
            selectinload(Product.variations).selectinload(Variation.attributes),
        )
        .filter_by(id=product_id)
        .first_or_404()
    )
    image_path = resolve_product_catalogue_image(product)
    if image_path is None:
        abort(404)
    return _catalogue_image_response(image_path)


@main.route("/catalogue-images/products/<int:product_id>/gallery/<int:index>")
@login_required
def catalogue_product_gallery_image(product_id, index):
    product = (
        Product.query.options(
            selectinload(Product.images),
            selectinload(Product.variations).selectinload(Variation.images),
            selectinload(Product.variations).selectinload(Variation.attributes),
        )
        .filter_by(id=product_id)
        .first_or_404()
    )
    rows = product_image_diagnostics(product)
    if index < 0 or index >= len(rows) or not rows[index].get("path"):
        abort(404)
    return _catalogue_image_response(rows[index]["path"])


@main.route("/catalogue-images/variations/<int:variation_id>")
@login_required
def catalogue_variation_image(variation_id):
    """Serve a variation-specific source image with parent fallback."""

    variation = (
        Variation.query.options(
            selectinload(Variation.images),
            selectinload(Variation.attributes),
            selectinload(Variation.product).selectinload(Product.images),
        )
        .filter_by(id=variation_id)
        .first_or_404()
    )
    image_path = resolve_variation_catalogue_image(variation)
    if image_path is None:
        abort(404)
    return _catalogue_image_response(image_path)


@main.route("/catalogue-images/variations/<int:variation_id>/gallery/<int:index>")
@login_required
def catalogue_variation_gallery_image(variation_id, index):
    variation = (
        Variation.query.options(
            selectinload(Variation.images),
            selectinload(Variation.attributes),
            selectinload(Variation.product).selectinload(Product.images),
        )
        .filter_by(id=variation_id)
        .first_or_404()
    )
    rows = variation_image_diagnostics(variation)
    if index < 0 or index >= len(rows) or not rows[index].get("path"):
        abort(404)
    return _catalogue_image_response(rows[index]["path"])


def _catalogue_image_response(image_path):
    """Apply the same private, sniff-resistant policy to catalogue images."""

    response = send_file(image_path, conditional=True, max_age=3600)
    response.cache_control.public = False
    response.cache_control.private = True
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


# ---------- Editor routes used by Products page ----------
@main.route("/edit_products")
@login_required
def edit_products():
    # Preserve the established compatibility route while keeping one canonical
    # URL-backed Products browser state.
    return redirect(url_for("main.products", **request.args))


@main.route("/edit_products/<int:product_id>/edit/<label>")
@login_required
def product_edit(product_id, label):
    """
    Open the web JSON editor (shared|override) for a product based on stored ProductAsset.
    Renders 'editor.html'.
    """
    if not current_user.is_admin:
        abort(403)
    label = (label or "").lower()
    if label not in ("shared", "override"):
        return "invalid label", 400

    p = (
        Product.query.options(
            joinedload(Product.collection),
            selectinload(Product.images),
            selectinload(Product.variations).selectinload(Variation.images),
            selectinload(Product.variations).selectinload(Variation.attributes),
        )
        .filter_by(id=product_id)
        .first_or_404()
    )
    workspace = editor_workspace(p, label)
    if label == "override" and not workspace["can_override"]:
        flash("This product has one shared JSON owner. Edit its shared metadata here.", "info")
        return redirect(url_for("main.product_edit", product_id=p.id, label="shared"))
    return render_template("metadata_editor.html", workspace=workspace)


@main.route("/api/metadata/validate", methods=["POST"])
@login_required
@csrf.exempt
def metadata_validate():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "invalid_json", "valid": False}), 400
    kind = payload.get("kind")
    data = payload.get("data")
    if kind not in {"shared", "override"}:
        return jsonify({"error": "invalid kind", "valid": False}), 400
    result = validate_product_info(
        data, "collection" if kind == "shared" else "override"
    )
    response = result.to_dict()
    if result.valid and payload.get('product_id') is not None:
        if not current_user.is_admin:
            abort(403)
        if type(payload['product_id']) is not int or payload['product_id'] < 1:
            return jsonify(error='Invalid product context.', valid=False), 400
        from app.metadata_workspace import metadata_source, title_preview
        product = Product.query.filter_by(id=payload['product_id']).first_or_404()
        shared = data if kind == 'shared' else metadata_source(product, 'shared')['data']
        override = ({} if product.collection_type == 'Single Variable' else
                    data if kind == 'override' else metadata_source(product, 'override')['data'])
        response['title_preview'] = title_preview(product, shared, override)
    return jsonify(response), 200 if result.valid else 400


@main.route("/edit_products/<sku>/save", methods=["POST"])
@login_required
def product_save_json(sku):
    """Apply a signed local patch, then hand off to existing scanner ingestion."""
    from app.metadata_save import prepare_request, verify_revision, review_token, override_collection_relpath
    from app.metadata_edit import MetadataEditConflict
    if not current_user.is_admin:
        abort(403)
    p = Product.query.filter_by(sku=sku).first_or_404()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(error="invalid_json", message="Submit a metadata object."), 400
    kind = payload.get("kind")
    if not isinstance(kind, str) or kind not in {"shared", "override"}:
        return jsonify(error="invalid_kind"), 400
    active = get_active_operation()
    if active is not None:
        return jsonify(error="catalogue_operation_active", message="Another catalogue operation is active. Wait for completion before saving.", active_operation=active), 409
    try:
        target, edit = prepare_request(p, kind, payload)
    except MetadataEditConflict as error:
        return jsonify(error="metadata_conflict", message=str(error)), 409
    except (OSError, ValueError, RuntimeError) as error:
        message = str(error) if isinstance(error, ValueError) else "Metadata source unavailable; restore the catalogue mount."
        return jsonify(error="metadata_validation_failed", message=message), 400
    if not edit.validation.valid:
        return jsonify(error="metadata_validation_failed", **edit.validation.to_dict()), 400
    retry_scan = payload.get("retry_scan") is True
    if not edit.changed and not retry_scan:
        return jsonify(ok=True, unchanged=True, run_id=None, revision=review_token(p, kind),
                       authored=edit.data, warnings=[i.to_dict() for i in edit.validation.warnings])
    affected = Product.query.filter_by(collection_id=p.collection_id).count() if p.collection_id else 1
    if kind == "shared" and affected > 1 and payload.get("acknowledge_shared") is not True:
        return jsonify(error="shared_confirmation_required", message=f"Confirm the shared scope affecting up to {affected} products."), 400
    scope = {"sku": p.sku, "kind": kind, "source": target.name, "local_save": True}
    try:
        if kind == "shared":
            from app.metadata_workspace import _catalogue_root
            collection_relpath = target.parent.relative_to(_catalogue_root()).as_posix()
        else:
            collection_relpath = override_collection_relpath(p, target)
    except (OSError, ValueError, RuntimeError) as error:
        return jsonify(error="metadata_scan_scope_invalid", message=str(error)), 400
    scope.update(scope_kind="collection", collection_relpath=collection_relpath, exhaustive=True)
    try:
        operation = acquire_catalogue_operation("shared_collection_update", scope)
    except CatalogueOperationActive as error:
        return _operation_conflict(error)
    json_saved = False
    try:
        # Validate again inside the existing operation lock, including effective
        # children. No stale browser draft is allowed to overwrite a newer file.
        locked_target, edit = prepare_request(p, kind, payload)
        if locked_target != target:
            raise MetadataEditConflict("Metadata owner changed. Reload before saving.")
        if kind == "override" and override_collection_relpath(p, locked_target) != collection_relpath:
            raise MetadataEditConflict("Containing collection changed. Reload before saving.")
        if not edit.validation.valid:
            raise ValueError("Metadata changed or is invalid; reload and validate before saving.")
        if target.exists() and edit.changed:
            create_metadata_backup(target)  # mandatory, never silently bypassed
        verify_revision(p, kind, payload.get("revision"))
        if edit.changed:
            atomic_write_text(target, edit.text, before_replace=lambda: verify_revision(p, kind, payload.get("revision")))
        json_saved = True
        if kind == "override":
            if not ProductAsset.query.filter_by(product_id=p.id, kind="info", label="override").first():
                from app.metadata_workspace import _catalogue_root
                db.session.add(ProductAsset(product_id=p.id, kind="info", label="override",
                    path=str(target), source_relpath=target.relative_to(_catalogue_root()).as_posix(), is_primary=False))
                db.session.commit()
            atomic_write_text(target.parent / ".update", "1")
        run_id = uuid.uuid4().hex
        start_scan(current_app._get_current_object(), run_id,
            scan_mode="shared_collection",
            operation_id=operation.id, scope=scope, collection_relpath=collection_relpath)
    except Exception as error:
        db.session.rollback()
        finish_catalogue_operation(operation.id, status="failed",
            error="Local JSON saved; scanner/projection handoff requires recovery." if json_saved else "Local metadata was not saved.",
            operation_summary={"json_saved":json_saved, "scan_pending":json_saved, "source":scope["source"]})
        retry_revision = None
        if json_saved:
            try:
                retry_revision = review_token(p, kind)
            except (OSError, ValueError):
                pass
        return jsonify(error="metadata_saved_scan_pending" if json_saved else "metadata_save_failed",
            message=("JSON saved, but scanner/projection has not completed. Open Scanner and run the affected update; do not publish until ingestion succeeds."
                     if json_saved else str(error) if isinstance(error, MetadataEditConflict) else "Source was not changed. Check catalogue/backup permissions and retry."),
            json_saved=json_saved, operation_id=operation.id, scan_pending=json_saved,
            revision=retry_revision,
            scanner_url=url_for("main.scanner")), 409 if isinstance(error, MetadataEditConflict) else 500
    try:
        if edit.changed:
            notify_editor_saved("collection metadata" if kind == "shared" else "product override",
                p.sku, collection=collection_display_name(p.collection) if p.collection else None,
                affected=affected if kind == "shared" else 1)
    except Exception:
        current_app.logger.warning("Discord metadata notification failed safely")
    return jsonify(ok=True, json_saved=True, scan_pending=True, run_id=run_id, operation_id=operation.id,
                   revision=review_token(p, kind), authored=edit.data,
                   warnings=[i.to_dict() for i in edit.validation.warnings])


@main.route("/metadata-reference")
@login_required
def metadata_reference():
    return render_template(
        "metadata_reference.html",
        fields=FIELD_INVENTORY,
        examples={name: load_example(name) for name in EXAMPLE_NAMES},
        template_names=TEMPLATE_NAMES,
    )


@main.route("/api/metadata-reference/template/<name>")
@login_required
def metadata_reference_template(name):
    try:
        data = load_template(name)
    except KeyError:
        return jsonify({"error": "unknown metadata template"}), 404
    return jsonify({"name": name, "data": data})


@main.route("/api/metadata-reference/schema/<name>")
@login_required
def metadata_reference_schema(name):
    try:
        return jsonify(load_schema(name))
    except KeyError:
        return jsonify({"error": "unknown metadata schema"}), 404


@main.route("/api/metadata-reference/example/<name>")
@login_required
def metadata_reference_example(name):
    try:
        return jsonify({"name": name, "data": load_example(name)})
    except KeyError:
        return jsonify({"error": "unknown metadata example"}), 404


@main.route("/api/delete-override/<int:product_id>", methods=["POST"])
@login_required
def api_delete_override(product_id):
    """
    Deletes the override product_info.json (if present) for a product,
    removes its ProductAsset record(s), drops a .update file in that folder,
    and kicks an 'update' scan. Returns { run_id }.
    """
    p = Product.query.get_or_404(product_id)
    if not current_user.is_admin:
        abort(403)
    from app.metadata_save import verify_revision, source
    from app.metadata_edit import MetadataEditConflict
    try:
        verify_revision(p, "override", (request.get_json(silent=True) or {}).get("revision"))
        owned_path, _, _ = source(p, "override")
    except (ValueError, OSError) as error:
        return jsonify(error="metadata_conflict", message="Reload the product override editor before removing its source."), 409

    asset = (
        ProductAsset.query.filter_by(product_id=p.id, kind="info", label="override")
        .order_by(ProductAsset.id.desc())
        .first()
    )
    if not asset or not asset.path:
        return jsonify({"error": "override not present"}), 400

    settings = Settings.query.first()
    root = (settings.product_folder or "").rstrip("/")
    real_path = str(owned_path)
    if not root or not real_path.startswith(os.path.realpath(root) + os.sep):
        return jsonify({"error": "path not allowed"}), 403

    folder = os.path.dirname(real_path)

    try:
        operation = acquire_catalogue_operation(
            "product_update", {"sku": p.sku, "kind": "override_delete"}
        )
    except CatalogueOperationActive as error:
        return _operation_conflict(error)

    try:
        if os.path.exists(real_path):
            verify_revision(p, "override", request.get_json()["revision"])
            create_metadata_backup(real_path)
            os.remove(real_path)

        ProductAsset.query.filter_by(
            product_id=p.id, kind="info", label="override"
        ).delete()
        db.session.commit()

        try:
            with open(os.path.join(folder, ".update"), "w") as f:
                f.write("1")
        except Exception:
            pass

        run_id = uuid.uuid4().hex
        start_scan(
            current_app._get_current_object(),
            run_id,
            scan_mode="update",
            operation_id=operation.id,
        )
        try:
            notify_override_removed(
                p.sku,
                product=p.title,
                collection=collection_display_name(p.collection) if p.collection else None,
            )
        except Exception:
            current_app.logger.warning("Discord override notification failed safely")
    except Exception as error:
        db.session.rollback()
        finish_catalogue_operation(operation.id, status="failed", error=error)
        return jsonify({"error": "failed to delete override"}), 500

    return jsonify({"ok": True, "run_id": run_id})


# ---------- Override folder picker (restricted to collection root) ----------


def _collection_root_for_product(product_id: int):
    """
    Determine the collection root for a product: use the parent directory
    of the shared (preferred) or override product_info.json.
    """
    p = Product.query.get(product_id)
    if not p:
        return None
    shared = (
        ProductAsset.query.filter_by(product_id=p.id, kind="info", label="shared")
        .order_by(ProductAsset.id.desc())
        .first()
    )
    override = (
        ProductAsset.query.filter_by(product_id=p.id, kind="info", label="override")
        .order_by(ProductAsset.id.desc())
        .first()
    )
    base_path = None
    if shared and shared.path:
        base_path = os.path.dirname(shared.path)
    elif override and override.path:
        base_path = os.path.dirname(override.path)
    return base_path


@main.route("/api/override/folders/<int:product_id>")
@login_required
def api_override_folders(product_id):
    """
    List subfolders under the product's collection root. Accepts ?rel=<relative_subpath>.
    """
    root = _collection_root_for_product(product_id)
    if not root or not os.path.isdir(root):
        return jsonify({"error": "collection root not found"}), 400

    rel = request.args.get("rel", "").strip()
    path = os.path.normpath(os.path.join(root, rel)) if rel else root
    root_real = os.path.realpath(root)
    path_real = os.path.realpath(path)
    if not path_real.startswith(root_real + os.sep) and path_real != root_real:
        path_real = root_real

    try:
        folders = sorted(
            [
                name
                for name in os.listdir(path_real)
                if os.path.isdir(os.path.join(path_real, name))
                and not name.startswith(".")
            ]
        )
        rel_out = os.path.relpath(path_real, root_real)
        if rel_out == ".":
            rel_out = ""
        return jsonify(
            {"root": root_real, "path": path_real, "rel": rel_out, "folders": folders}
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@main.route("/api/override/create/<int:product_id>", methods=["POST"])
@login_required
def api_override_create(product_id):
    """Compatibility handoff to an unsaved draft, never an arbitrary folder write."""
    from app.metadata_save import source
    if not current_user.is_admin:
        abort(403)
    p = db.get_or_404(Product, product_id)
    try:
        target, _, _ = source(p, "override")
        supplied = (request.get_json(silent=True) or {}).get("rel")
        root = _collection_root_for_product(product_id)
        if supplied is not None and (not root or os.path.realpath(os.path.join(root, supplied)) != str(target.parent)):
            raise ValueError("Choose this product's own folder. Shared metadata is edited separately.")
    except (ValueError, OSError) as error:
        return jsonify(error="invalid_metadata_owner", message=str(error) if isinstance(error, ValueError) else "Restore the catalogue mount."), 400
    return jsonify(ok=True, created=False, draft=not target.exists(), run_id=None,
                   edit_url=url_for("main.product_edit", product_id=p.id, label="override"))


# ---------- Raw file open (debug / preview) ----------


@main.route("/assets/info/<int:product_id>/<label>")
@login_required
def open_info_asset(product_id, label):
    """
    Serves the shared/override product_info.json from disk for a given product.
    Only allows files under Settings.product_folder.
    """
    label = (label or "").lower()
    if label not in ("shared", "override"):
        return "invalid label", 400

    p = Product.query.get_or_404(product_id)
    asset = (
        ProductAsset.query.filter_by(product_id=p.id, kind="info", label=label)
        .order_by(ProductAsset.id.desc())
        .first()
    )
    if not asset or not asset.path:
        return "not found", 404

    settings = Settings.query.first()
    root = (settings.product_folder or "").rstrip("/")
    real_path = os.path.realpath(asset.path)
    if not root or not real_path.startswith(os.path.realpath(root) + os.sep):
        return "blocked", 403

    try:
        return send_file(real_path, mimetype="application/json")
    except Exception as e:
        return f"cannot open file: {e}", 500


# ---------- Scanner (shared SSE endpoints used for both initial + update scans) ----------


@main.route("/initial-scan", methods=["GET"])
@login_required
def initial_scan_page():
    return redirect(url_for("main.scanner", initial="1"))


@main.route("/initial-scan/start", methods=["POST"])
@login_required
@csrf.exempt
def initial_scan_start():
    """
    Start a scan. We do NOT ping Discord from here to avoid double 'start' notifications,
    because scan_runner already sends the start + completion notifications.
    """
    if onboarding.pending():
        return scanner_start()
    payload = request.get_json(silent=True) or {}
    run_id = uuid.uuid4().hex
    scan_mode = payload.get("mode", "append")
    active_operation = get_active_operation()
    if active_operation:
        return _operation_conflict(CatalogueOperationActive(active_operation))
    if scan_mode not in {"append", "update", "full"}:
        return jsonify({"error": "unsupported scan mode"}), 400
    setup_state = detect_setup_state()
    if not setup_state.safe_to_run:
        return (
            jsonify(
                {
                    "error": "catalogue_state_ambiguous",
                    "message": setup_state.message,
                    "failures": list(setup_state.errors),
                }
            ),
            409,
        )
    if scan_mode == "full" and payload.get("confirm_full_regeneration") is not True:
        return (
            jsonify(
                {
                    "error": "full_regeneration_confirmation_required",
                    "message": (
                        "Full regeneration may replace catalogue SKU identities. "
                        "Explicit confirmation is required."
                    ),
                }
            ),
            400,
        )
    try:
        start_scan(current_app._get_current_object(), run_id, scan_mode=scan_mode)
    except CatalogueOperationActive as error:
        return _operation_conflict(error)
    return jsonify({"run_id": run_id})


@main.route("/catalogue/reconstruct", methods=["POST"])
@login_required
@csrf.exempt
def catalogue_reconstruct():
    setup_scope = None
    if onboarding.pending():
        csrf.protect()
        if not onboarding.readiness()["ready"] or request.form.get("confirm_reconstruction") != "yes":
            abort(409, "Recheck setup readiness and explicitly confirm reconstruction.")
        setup_state = detect_setup_state()
        if not setup_state.safe_to_run or setup_state.code == "new_catalogue":
            abort(409, "A new catalogue requires its initial scan; reconstruction is for existing identities.")
        setup_scope = onboarding.scan_scope()
    try:
        result = run_reconstruction()
    except CatalogueOperationActive as error:
        return _operation_conflict(error)
    payload = {
        "operation_id": result.operation_id,
        "status": result.status,
        "collections": result.collections,
        "products": result.products,
        "markers": result.markers,
        "backup": (
            f"backups/{result.backup_path.name}" if result.backup_path else None
        ),
        "products_missing": result.products_missing,
        "products_restored": result.products_restored,
        "variations_missing": result.variations_missing,
        "variations_restored": result.variations_restored,
        "recovery_required": result.recovery_required,
        "error": result.error,
    }
    catalogue = _catalogue_summary_counts()
    operation_row = db.session.get(CatalogueOperation, result.operation_id)
    if setup_scope:
        scope = json.loads(operation_row.scope or "{}")
        scope.update(setup_scope)
        operation_row.scope = json.dumps(scope)
        db.session.commit()
        return redirect(url_for("main.operation_detail", operation_id=result.operation_id))
    elapsed_seconds = 0
    if operation_row and operation_row.started_at:
        operation_finished = operation_row.finished_at or datetime.now()
        elapsed_seconds = max(
            0, int((operation_finished - operation_row.started_at).total_seconds())
        )
    failures = 0 if result.status == "succeeded" else 1
    payload["catalogue"] = catalogue
    payload["progress"] = {
        "operation": {
            "id": result.operation_id,
            "type": "reconstruction",
            "status": result.status,
            "stage": "completed" if result.status == "succeeded" else result.status,
            "current_item": None,
            "scope": {"scope_kind": "catalogue", "identity_mode": "preserve"},
        },
        "progress": {
            "completed": result.collections,
            "total": result.collections,
            "percent": 100,
            "unit": "collections",
        },
        "timing": {
            "started_at": (
                operation_row.started_at.isoformat() if operation_row else None
            ),
            "finished_at": (
                operation_row.finished_at.isoformat()
                if operation_row and operation_row.finished_at
                else None
            ),
            "elapsed_seconds": elapsed_seconds,
        },
        "counts": {
            "collections": result.collections,
            "products": result.products,
            "variations": catalogue["variations"],
            "warnings": int(result.recovery_required),
            "failures": failures,
        },
        "catalogue": catalogue,
    }
    return jsonify(payload), (200 if result.status == "succeeded" else 409)


@main.route("/initial-scan/stream/<run_id>")
@login_required
def initial_scan_stream(run_id):
    if run_id not in _runs:
        return "Unknown run_id", 404
    return Response(stream_lines(run_id), mimetype="text/event-stream")


@main.route("/initial-scan/progress/<run_id>")
@login_required
def initial_scan_progress(run_id):
    if run_id not in _runs:
        return jsonify({"error": "unknown run"}), 404
    payload = get_progress(run_id)
    if payload["status"] in {"done", "error"}:
        payload["catalogue"] = _catalogue_summary_counts()
    return jsonify(payload)


@main.route("/initial-scan/done/<run_id>")
@login_required
def initial_scan_done(run_id):
    # Compatibility navigation never marks first-run setup complete.
    return redirect(url_for("main.scanner"))


@main.route("/web-sync", methods=["GET"])
@login_required
def web_sync_page():
    return redirect(url_for("main.woo_sync"))


# ---------- Application workspaces ----------


def _render_planned(title, section, description, *, primary=None):
    primary_label, primary_url = primary or (None, None)
    return render_template(
        "planned.html",
        title=title,
        section=section,
        description=description,
        primary_label=primary_label,
        primary_url=primary_url,
    )


@main.route("/collections")
@login_required
def collections():
    try:
        filters = parse_collection_filters(request.args)
    except ValueError as error:
        raise BadRequest(str(error)) from error
    return render_template("collections.html", workspace=build_collections_browser(filters))


@main.route("/collections/<int:collection_id>")
@login_required
def collection_detail(collection_id):
    collection = db.get_or_404(Collection, collection_id)
    try:
        pagination = parse_product_pagination(request.args)
    except ValueError as error:
        raise BadRequest(str(error)) from error
    return render_template(
        "collection_detail.html",
        workspace=build_collection_detail(collection, pagination),
    )


@main.route("/scanner")
@login_required
def scanner():
    initial_setup = onboarding.pending()
    if initial_setup and not onboarding.readiness()["ready"]:
        return redirect(url_for("main.initial_settings"))
    initial_review = initial_setup or request.args.get("initial") == "1"
    setup_state = detect_setup_state() if initial_review else None
    recent = (
        CatalogueOperation.query.filter(
            CatalogueOperation.operation_type.in_({"append", "product_update", "full"})
        )
        .order_by(CatalogueOperation.started_at.desc(), CatalogueOperation.id.desc())
        .limit(5)
        .all()
    )
    return render_template(
        "scanner.html",
        modes=SCAN_MODES,
        readiness=scanner_readiness(),
        recent_operations=[operation_view(row) for row in recent],
        selected_mode=request.args.get("mode", "append"),
        retry_of=request.args.get("retry_of", "")[:32],
        initial_setup=initial_review,
        setup_state=setup_state,
    )


@main.route("/scanner/start", methods=["POST"])
@login_required
@csrf.exempt
def scanner_start():
    payload = request.get_json(silent=True) or request.form.to_dict()
    initial_setup = onboarding.pending()
    if initial_setup:
        csrf.protect()
        if not onboarding.readiness()["ready"]:
            return jsonify({"message": "Complete configuration and local Taxonomy readiness before the initial scan."}), 409
    mode = payload.get("mode")
    if mode not in {item["key"] for item in SCAN_MODES}:
        return jsonify({"error": "unsupported_scan_mode", "message": "That scan mode is not supported."}), 400
    confirmed = payload.get("confirm_operation") is True or payload.get("confirm_operation") == "true"
    if not confirmed:
        return jsonify({"error": "confirmation_required", "message": "Confirm the selected scan before it starts."}), 400
    if mode == "full" and not (payload.get("confirm_full_regeneration") is True or payload.get("confirm_full_regeneration") == "true"):
        return jsonify({"error": "full_confirmation_required", "message": "Full scan requires the additional catalogue-wide confirmation."}), 400
    if initial_setup or payload.get("initial_review") is True:
        setup_state = detect_setup_state()
        if not setup_state.safe_to_run or mode == "update" or (setup_state.recommended_action == "reconstruction" and mode != "full"):
            try:
                from app.utils.discord import notify_operation_attention
                notify_operation_attention("scanner", "blocked", error="; ".join(setup_state.errors) or setup_state.message,
                                           summary={"woo_writes": 0, "stage": "Initial identity preflight"})
            except Exception:
                current_app.logger.warning("Discord scanner preflight notification failed safely")
            return jsonify({"message": "The initial scan requires the detected identity-safe action. Review reconstruction or explicitly confirm full regeneration."}), 409
    readiness = scanner_readiness()
    if readiness["active"]:
        return _operation_conflict(CatalogueOperationActive(readiness["active"]))
    if not readiness["mounts_ready"]:
        failures = [check["label"] for check in readiness["checks"] if not check["ok"]]
        try:
            from app.utils.discord import notify_operation_attention
            notify_operation_attention("scanner", "blocked", error="; ".join(failures), summary={"woo_writes": 0, "stage": "Storage/database readiness"})
        except Exception:
            current_app.logger.warning("Discord scanner readiness notification failed safely")
        return jsonify({"error": "scanner_not_ready", "message": "Required scanner storage or database checks did not pass.", "failures": failures}), 409
    run_id = uuid.uuid4().hex
    scope = {"scan_mode": mode, "initiating_source": "scanner_workspace"}
    if initial_setup:
        scope.update(onboarding.scan_scope())
    retry_of = (payload.get("retry_of") or "")[:32]
    if retry_of:
        original = db.session.get(CatalogueOperation, retry_of)
        expected_mode = {"append": "append", "product_update": "update", "full": "full"}.get(original.operation_type) if original else None
        if expected_mode != mode or original.status not in {"failed", "partial", "interrupted"}:
            return jsonify({"error": "unsafe_retry", "message": "This operation cannot be retried through the Scanner workspace."}), 409
        scope["retry_of"] = original.id
    try:
        operation_id = start_scan(current_app._get_current_object(), run_id, scan_mode=mode, scope=scope)
    except CatalogueOperationActive as error:
        return _operation_conflict(error)
    operation_id = str(operation_id)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", operation_id):
        current_app.logger.error("Scanner created an operation with an invalid route identifier")
        return jsonify({
            "ok": True,
            "operation_id": operation_id,
            "destination": None,
            "message": "Scan started successfully. Open the active operation from Scanner or Dashboard.",
        }), 202
    return jsonify({
        "ok": True,
        "operation_id": operation_id,
        "destination": url_for("main.operation_detail", operation_id=operation_id),
    }), 202


@main.route("/operations")
@login_required
def operations():
    try:
        filters = parse_operation_filters(request.args)
    except ValueError as error:
        raise BadRequest(str(error)) from error
    return render_template("operations.html", workspace=operations_browser(filters))


@main.route('/operations/issues')
@login_required
@saved_read_view
def operation_issues_page():
    """Read-only current projection, shared exactly with dashboard counters."""
    from app.operation_reporting import issue_projection, CATEGORIES
    category = request.args.get('category', 'failures')
    if category not in CATEGORIES:
        abort(400)
    try:
        page = max(1, int(request.args.get('page', 1)))
    except (ValueError, TypeError):
        abort(400)
    data = issue_projection()
    items = [i for i in data['items'] if i['category'] == category]
    total = len(items)
    search = request.args.get('q', '')[:100].strip()
    if search:
        items = [i for i in items if search.casefold() in (str(i.get('entity', ''))+' '+str(i.get('identifier', ''))+' '+i['cause']).casefold()]
    filtered = len(items)
    pages = max(1, (filtered+24)//25)
    page = min(page, pages)
    return render_template('operation_issues.html', issues=items[(page-1)*25:page*25],
        total=total, filtered=filtered, page=page, pages=pages, search=search,
        category=category, heading=CATEGORIES[category][0], unit=CATEGORIES[category][1]), 200, {'Cache-Control': 'no-store, private'}


@main.route("/operations/<operation_id>")
@login_required
def operation_detail(operation_id):
    operation = db.session.get(CatalogueOperation, operation_id)
    if operation is None:
        abort(404)
    try:
        item_page = max(1, int(request.args.get("item_page", "1")))
    except ValueError as error:
        raise BadRequest("Invalid operation-item page") from error
    return render_template(
        "operation_detail.html",
        workspace=operation_detail_workspace(
            operation,
            item_page=item_page,
            item_status=request.args.get("item_status", "")[:32],
        ),
        initial_setup=onboarding.belongs(operation),
        setup_can_complete=onboarding.can_complete(operation),
    )


@main.route("/operations/<operation_id>/discord/<event>/retry", methods=["POST"])
@login_required
def operation_discord_retry(operation_id, event):
    if not current_user.is_admin:
        abort(403)
    operation = db.session.get(CatalogueOperation, operation_id)
    if operation is None:
        abort(404)
    from app.shopify_notifications import deliver, events_view
    if not any(item["event"] == event and item["retryable"] for item in events_view(operation)):
        abort(409, "Notification is not eligible for retry.")
    try:
        deliver(operation_id, event, retry=True, acknowledge_uncertain=request.form.get("acknowledge_uncertain") == "yes")
    except ValueError as error:
        raise BadRequest(str(error)) from error
    return redirect(url_for("main.operation_detail", operation_id=operation_id))


@main.route("/operations/<operation_id>/intake-result")
@login_required
def operation_intake_result(operation_id):
    operation = db.session.get(CatalogueOperation, operation_id)
    if operation is None:
        abort(404)
    workspace = operation_detail_workspace(operation)
    if workspace["intake"] is None:
        abort(404)
    response = Response(
        render_template("operations/_intake_result.html", workspace=workspace),
        mimetype="text/html",
    )
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@main.route("/operations/<operation_id>/woo-publish-result")
@login_required
def operation_woo_publish_result(operation_id):
    operation = db.session.get(CatalogueOperation, operation_id)
    if operation is None:
        abort(404)
    workspace = operation_detail_workspace(operation)
    if workspace["woo_controlled_publish"] is None:
        abort(404)
    response = Response(
        render_template("operations/_woo_publish_result.html", workspace=workspace),
        mimetype="text/html",
    )
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@main.route("/api/operations/<operation_id>/status")
@login_required
def operation_status(operation_id):
    operation = db.session.get(CatalogueOperation, operation_id)
    if operation is None:
        return jsonify({"error": "operation_not_found"}), 404
    view = operation_view(operation)
    live = view.get("live") or {}
    live_payload = {key: value for key, value in live.items() if key != "logs"}
    response = jsonify({
        "operation": {
            "id": view["id"], "status": view["status"], "status_label": view["status_label"],
            "type": view["type"], "type_label": view["type_label"],
            "started_at": view["started_at"].isoformat() if view["started_at"] else None,
            "finished_at": view["finished_at"].isoformat() if view["finished_at"] else None,
            "duration": view["duration"], "recovery_state": view["recovery_state"],
            "attempted": view["attempted"], "succeeded": view["succeeded"],
            "failed": view["failed"], "missing": view["missing"], "restored": view["restored"],
            "warning_count": view["warning_count"], "error_count": view["error_count"],
        },
        "live": live_payload,
        "last_activity": live.get("heartbeat_at"),
        "summary": view.get("summary") or {},
        "warning_summary": view.get("warning_summary") or [],
        "warning_entries": view.get("warning_entries") or [],
        "discord": view["discord"],
        "terminal": view["status"] in {"succeeded", "partial", "failed", "interrupted", "stalled", "recovery_required", "attention"},
    })
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@main.route("/api/operations/<operation_id>/logs")
@login_required
def operation_logs(operation_id):
    operation = db.session.get(CatalogueOperation, operation_id)
    if operation is None:
        return jsonify({"error": "operation_not_found"}), 404
    try:
        page = int(request.args.get("page", "1"))
        per_page = int(request.args.get("per_page", "50"))
    except ValueError as error:
        raise BadRequest("Invalid log pagination") from error
    try:
        after = int(request.args["after"]) if "after" in request.args else None
    except ValueError as error:
        raise BadRequest("Invalid log cursor") from error
    if persisted_live_state(operation) is None and after is None:
        from app.utils.scan_runner import operation_log_page
        payload = operation_log_page(
            operation_id, page=page, per_page=per_page,
            severity=request.args.get("severity", "")[:16], search=request.args.get("q", "")[:100],
        )
    else:
        payload = persisted_log_page(
            operation, page=page, per_page=per_page, after=after,
            severity=request.args.get("severity", "")[:16], search=request.args.get("q", "")[:100],
        )
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response


@main.route("/woo-sync")
@login_required
def woo_sync():
    from app.woo_sync_workspace import workspace
    return render_template("woo_sync.html", workspace=workspace(request.args))


@main.route("/woo-sync/state/<int:product_id>", methods=["POST"])
@login_required
def woo_live_refresh(product_id):
    from app.woo_live_state import refresh_product
    from app.woo_publish_preview import _product_query
    product = _product_query().filter_by(id=product_id, catalogue_status="active").first_or_404()
    try:
        state = refresh_product(product, force=request.form.get("force") == "1")
    except (ValueError, WooConnectionError):
        return jsonify(error="Woo state is unavailable. Review Integration health."), 409
    response = jsonify(state)
    response.headers["Cache-Control"] = "no-store"
    return response


@main.route("/sync")
@login_required
def sync():
    return redirect(url_for("main.woo_sync"))


@main.route("/orders")
@login_required
def orders():
    return redirect(url_for('orders.index'))


@main.route("/website-automation")
@login_required
def website_automation():
    return _render_planned(
        "Website Automation",
        "Future",
        "Website automation is intentionally outside the current local catalogue workflow.",
    )


@main.route("/analytics")
@login_required
def analytics():
    return _render_planned(
        "Analytics",
        "Future",
        "Analytics will be added when authoritative business data becomes available.",
    )


@main.route("/pos")
@login_required
def pos():
    return _render_planned(
        "Point of Sale",
        "Future",
        "Point-of-sale functionality is not part of the current catalogue release.",
    )


@main.route("/tools")
@login_required
def tools():
    return redirect(url_for("main.settings"))


@main.route("/site")
@login_required
def site_manager():
    return redirect(url_for("main.website_automation"))


@main.route("/settings")
@login_required
def settings():
    from app.settings_workspace import build_settings_workspace

    return render_template("settings.html", workspace=build_settings_workspace())


@main.route("/woocommerce")
@login_required
def woocommerce():
    return render_template(
        "woocommerce.html", workspace=build_woocommerce_workspace()
    )


@main.route("/woocommerce/test", methods=["POST"])
@login_required
def woocommerce_test():
    try:
        operation_id = execute_connection_test()
    except CatalogueOperationActive as error:
        flash("Another operation is already active. Follow it before testing again.", "warning")
        return redirect(url_for("main.operation_detail", operation_id=error.active["id"]))
    return redirect(url_for("main.operation_detail", operation_id=operation_id))


def _woo_preview_scope(payload):
    kind = (payload.get("scope_kind") or payload.get("kind") or "").strip()
    scope = {"kind": kind}
    if kind == "product": scope["product_id"] = payload.get("product_id")
    elif kind == "collection": scope["collection_id"] = payload.get("collection_id")
    elif kind == "selected":
        values = payload.getlist("product_ids") if hasattr(payload, "getlist") else payload.get("product_ids", [])
        scope["product_ids"] = values
    return scope


@main.route("/woocommerce/preview")
@login_required
def woocommerce_preview():
    return render_template("woocommerce_preview.html", workspace=preview_landing())


@main.route("/woocommerce/preview/estimate", methods=["POST"])
@login_required
def woocommerce_preview_estimate():
    try:
        estimate = scope_estimate(_woo_preview_scope(request.form))
    except (PreviewError, TypeError, ValueError) as error:
        flash(str(error), "danger")
        return redirect(url_for("main.woocommerce_preview"))
    return render_template("woocommerce_preview_estimate.html", estimate=estimate, scope=_woo_preview_scope(request.form))


@main.route("/woocommerce/preview/generate", methods=["POST"])
@login_required
def woocommerce_preview_generate():
    try:
        plan = generate_publish_plan(_woo_preview_scope(request.form), confirm_large=request.form.get("confirm_large") == "yes")
    except CatalogueOperationActive as error:
        flash("Another operation is active. Follow it before generating a preview.", "warning")
        return redirect(url_for("main.operation_detail", operation_id=error.active["id"]))
    except (PreviewError, WooConnectionError, TypeError, ValueError) as error:
        from app.woo_publish_preview import resolve_scope
        from app.utils.discord import notify_operation_attention
        try:
            affected = resolve_scope(_woo_preview_scope(request.form))
        except (ValueError, TypeError):
            affected = []
        if getattr(error, "details", {}).get("readiness") == "blocked":
            try:
                notify_operation_attention("woo_publish_preview", "blocked", error=str(error),
                    summary={"scope": ", ".join(f"{p.title} ({p.sku})" for p in affected[:5]), "woo_writes": 0})
            except Exception:
                current_app.logger.warning("Discord blocked-preview notification failed safely")
        return render_template("woocommerce_preview_blocked.html", error=str(error), products=affected), 409
    return redirect(url_for("main.woocommerce_preview_operation", operation_id=plan["operation_id"]))


@main.route("/woocommerce/preview/operations/<operation_id>")
@login_required
def woocommerce_preview_operation(operation_id):
    operation = CatalogueOperation.query.filter_by(id=operation_id, operation_type="woo_publish_preview").first_or_404()
    plan = cached_plan(operation_id)
    action_filter = (request.args.get("action") or "").strip()
    valid_actions = {"create", "link_candidate", "update", "no_change", "blocked", "recovery_required"}
    if action_filter not in valid_actions:
        action_filter = ""
    displayed_products = [item for item in plan["products"] if not action_filter or item["action"] == action_filter] if plan else []
    linked_success = session.pop("woo_link_success_operation", None) == operation.id
    unlinked_success = session.pop("woo_unlink_success_operation", None) == operation.id
    return render_template(
        "woocommerce_preview_operation.html",
        operation=operation,
        summary=woo_preview_operation_summary(operation),
        plan=plan,
        displayed_products=displayed_products,
        action_filter=action_filter,
        stale=plan_is_stale(plan) if plan else None,
        linked_success=linked_success,
        unlinked_success=unlinked_success,
    )


@main.route("/woocommerce/preview/products/<int:product_id>")
@login_required
def woocommerce_preview_product(product_id):
    operation_id = (request.args.get("operation_id") or "")[:32]
    operation = CatalogueOperation.query.filter_by(id=operation_id, operation_type="woo_publish_preview").first_or_404()
    plan = cached_plan(operation.id)
    if not plan: raise NotFound("Detailed preview expired; generate a fresh preview.")
    product_plan = next((item for item in plan["products"] if item["product_id"] == product_id), None)
    if not product_plan: raise NotFound("Product is not part of this preview.")
    return render_template("woocommerce_preview_product.html", operation=operation, plan=plan, product_plan=product_plan, stale=plan_is_stale(plan))


@main.route("/woocommerce/preview/link/<int:product_id>")
@login_required
def woocommerce_link_candidate_review(product_id):
    operation_id = (request.args.get("operation_id") or "")[:32]
    preview_digest = (request.args.get("preview_digest") or "")[:128]
    try:
        review = prepare_link_candidate_review(operation_id, preview_digest, product_id)
    except LinkCandidateError as error:
        flash(str(error), "danger")
        return redirect(url_for("main.woocommerce_preview_operation", operation_id=operation_id))
    return render_template("woocommerce_link_candidate_review.html", review=review)


@main.route("/woocommerce/preview/link/<int:product_id>", methods=["POST"])
@login_required
def woocommerce_link_candidate_confirm(product_id):
    operation_id = (request.form.get("preview_operation_id") or "")[:32]
    try:
        if request.form.get("acknowledge_identity") != "yes":
            raise LinkCandidateError("Explicit identity acknowledgement is required before linking.", category="acknowledgement_required")
        link_candidate_identity(
            operation_id,
            request.form.get("preview_digest"),
            product_id,
            request.form.get("candidate_woo_id"),
        )
    except (LinkCandidateError, WooConnectionError, TypeError, ValueError) as error:
        flash(str(error), "danger")
        return redirect(url_for(
            "main.woocommerce_link_candidate_review", product_id=product_id,
            operation_id=operation_id,
            preview_digest=(request.form.get("preview_digest") or "")[:128],
        ))
    session["woo_link_success_operation"] = operation_id
    return redirect(url_for("main.woocommerce_preview_operation", operation_id=operation_id))


@main.route("/woocommerce/preview/unlink/<int:product_id>")
@login_required
def woocommerce_unlink_review(product_id):
    operation_id = (request.args.get("operation_id") or "")[:32]
    preview_digest = (request.args.get("preview_digest") or "")[:128]
    try:
        review = prepare_woo_unlink_review(operation_id, preview_digest, product_id)
    except WooUnlinkError as error:
        flash(str(error), "danger")
        return redirect(url_for("main.woocommerce_preview_operation", operation_id=operation_id))
    return render_template("woocommerce_unlink_review.html", review=review)


@main.route("/woocommerce/preview/unlink/<int:product_id>", methods=["POST"])
@login_required
def woocommerce_unlink_confirm(product_id):
    operation_id = (request.form.get("preview_operation_id") or "")[:32]
    try:
        if request.form.get("acknowledge_unlink") != "yes":
            raise WooUnlinkError("Explicit acknowledgement is required before unlinking.", category="acknowledgement_required")
        unlink_woo_identity(
            operation_id, request.form.get("preview_digest"), product_id,
            request.form.get("woo_id"),
        )
    except (WooUnlinkError, TypeError, ValueError) as error:
        flash(str(error), "danger")
        return redirect(url_for(
            "main.woocommerce_unlink_review", product_id=product_id,
            operation_id=operation_id,
            preview_digest=(request.form.get("preview_digest") or "")[:128],
        ))
    session["woo_unlink_success_operation"] = operation_id
    return redirect(url_for("main.woocommerce_preview_operation", operation_id=operation_id))


@main.route("/api/woocommerce/preview/products/<int:product_id>")
@login_required
def api_woocommerce_preview_product(product_id):
    operation_id = (request.args.get("operation_id") or "")[:32]
    operation = CatalogueOperation.query.filter_by(id=operation_id, operation_type="woo_publish_preview").first_or_404()
    plan = cached_plan(operation.id)
    product_plan = next((item for item in (plan or {}).get("products", []) if item["product_id"] == product_id), None)
    if not product_plan: return jsonify({"ok": False, "error": "Detailed preview is unavailable; generate a fresh preview."}), 404
    return jsonify({"ok": True, "product": product_plan, "preview_digest": plan["digest"], "stale": plan_is_stale(plan)})


def _publish_product_ids(payload):
    values = payload.getlist("product_ids") if hasattr(payload, "getlist") else payload.get("product_ids", [])
    return values if isinstance(values, list) else [values]


@main.route("/woocommerce/publish/confirm", methods=["POST"])
@login_required
def woocommerce_publish_confirm():
    try:
        confirmation = prepare_publish_confirmation(
            request.form.get("preview_operation_id"),
            request.form.get("preview_digest"),
            _publish_product_ids(request.form),
        )
    except (ControlledPublishError, WooConnectionError, TypeError, ValueError) as error:
        flash(str(error), "danger")
        operation_id = (request.form.get("preview_operation_id") or "")[:32]
        return redirect(url_for("main.woocommerce_preview_operation", operation_id=operation_id))
    return render_template("woocommerce_publish_confirm.html", confirmation=confirmation)


@main.route("/woocommerce/publish/start", methods=["POST"])
@login_required
def woocommerce_publish_start():
    operation_id = (request.form.get("preview_operation_id") or "")[:32]
    try:
        if request.form.get("acknowledge_write") != "yes":
            raise ControlledPublishError("Explicit WooCommerce write acknowledgement is required.")
        retry_of = (request.form.get("retry_of") or "")[:32]
        confirmation = (
            resume_confirmation(retry_of)
            if retry_of
            else prepare_publish_confirmation(
                operation_id,
                request.form.get("preview_digest"),
                _publish_product_ids(request.form),
            )
        )
        if confirmation["requires_live_acknowledgement"] and request.form.get("acknowledge_live") != "yes":
            raise ControlledPublishError("Published products require the customer-visibility acknowledgement.")
        publish_operation_id = start_publish_operation(confirmation, app=current_app._get_current_object())
    except CatalogueOperationActive as error:
        flash("Another operation is active. Follow it before publishing.", "warning")
        return redirect(url_for("main.operation_detail", operation_id=error.active["id"]))
    except (ControlledPublishError, WooConnectionError, TypeError, ValueError) as error:
        flash(str(error), "danger")
        return redirect(url_for("main.woocommerce_preview_operation", operation_id=operation_id))
    return redirect(url_for("main.operation_detail", operation_id=publish_operation_id))


@main.route("/woocommerce/publish/operations/<operation_id>/resume", methods=["POST"])
@login_required
def woocommerce_publish_resume(operation_id):
    try:
        confirmation = resume_confirmation(operation_id)
    except (ControlledPublishError, WooConnectionError, TypeError, ValueError) as error:
        flash(str(error), "danger")
        return redirect(url_for("main.operation_detail", operation_id=operation_id))
    return render_template("woocommerce_publish_confirm.html", confirmation=confirmation, retry_of=operation_id)


# ---------- Auth ----------


@main.route("/login", methods=["GET", "POST"])
def login():
    if not User.query.first():
        return redirect(url_for("main.setup"))
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if user and check_password_hash(user.password, form.password.data):
            login_user(user, remember=form.remember.data)
            flash("Logged in successfully.", "success")
            return redirect(url_for("main.dashboard"))
        else:
            flash("Invalid username or password.", "danger")
    return render_template("auth/login.html", form=form)


@main.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("main.login"))


# ---------- Setup flow ----------


@main.route("/setup", methods=["GET", "POST"])
def setup():
    if User.query.first():
        return redirect(url_for("main.login"))
    form = SetupAdminForm()
    if form.validate_on_submit():
        from app.commerce_config import initialize_platform, CommerceConfigError
        try:
            initialize_platform(form.commerce_platform.data)
        except CommerceConfigError as error:
            flash(str(error), "danger")
            return render_template("setup/setup.html", form=form), 409
        new_admin = User(
            email=form.email.data.strip(),
            username=form.username.data.strip(),
            password=generate_password_hash(form.password.data.strip()),
            is_admin=True,
        )
        db.session.add(new_admin)
        onboarding.begin()
        db.session.commit()
        login_user(new_admin)
        flash("Admin user created and logged in.", "success")
        return redirect(url_for("main.initial_settings"))
    return render_template("setup/setup.html", form=form)


@main.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    # No account lookup, token generation or email delivery in this release.
    return render_template("auth/forgot_password.html")


@main.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    # Previously issued links are inert; never inspect or echo their token.
    return redirect(url_for("main.forgot_password"))


@main.route("/initial-settings", methods=["GET", "POST"])
@login_required
def initial_settings():
    form = InitialSettingsForm()
    settings = Settings.query.first()
    for key in onboarding.FIELDS:
        if onboarding.owned(key):
            form._fields.pop(key, None)
            if request.method == "POST" and key in request.form:
                abort(409, "Deployment-owned values cannot be changed here.")
        elif request.method == "GET" and settings:
            value = getattr(settings, key)
            form[key].data = "" if key == "url_prefix" and not onboarding.valid_url(value) else value
    if form.validate_on_submit():
        if not settings:
            settings = Settings()
            db.session.add(settings)
        for key in onboarding.FIELDS:
            if not onboarding.owned(key):
                value = form[key].data.strip()
                if key == "url_prefix" and not onboarding.valid_url(value):
                    form[key].errors.append("Use an HTTP(S) image base without credentials, query or fragment.")
                    form[key].data = ""
                    return render_template("setup/initial_settings.html", form=form, readiness=onboarding.readiness()), 400
                setattr(settings, key, value)
        db.session.commit()
        if onboarding.readiness()["ready"]:
            return redirect(url_for("main.scanner"))
        flash("Configuration saved. Resolve the readiness checks before continuing.", "warning")
    return render_template("setup/initial_settings.html", form=form, readiness=onboarding.readiness())


@main.route("/setup/complete", methods=["POST"])
@login_required
def complete_setup():
    try:
        onboarding.complete(request.form.get("operation_id", ""))
    except ValueError as error:
        abort(409, str(error))
    flash("Initial catalogue scan completed. Your dashboard is ready.", "success")
    return redirect(url_for("main.dashboard"))


@main.route("/signup", methods=["GET", "POST"])
def signup():
    return redirect(url_for("main.login" if User.query.first() else "main.setup"))


@main.route("/folder-picker")
@login_required
def folder_picker():
    path = request.args.get("path", "/")
    try:
        folders = sorted(
            [
                os.path.join(path, f)
                for f in os.listdir(path)
                if os.path.isdir(os.path.join(path, f))
            ]
        )
        return jsonify({"folders": folders, "current_path": path})
    except Exception as e:
        return jsonify({"folders": [], "error": str(e)})
