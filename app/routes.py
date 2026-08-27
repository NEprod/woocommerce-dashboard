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
    send_file,
    abort,
)
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.exceptions import BadRequest
from werkzeug.security import check_password_hash, generate_password_hash
from sqlalchemy.orm import joinedload, selectinload

from app import db, csrf
from app.forms import (
    LoginForm,
    SetupAdminForm,
    ForgotPasswordForm,
    ResetPasswordForm,
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
from app.utils.token_utils import generate_reset_token, verify_reset_token
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
from app.image_preparation import (
    browse_intake,
    configured_intake_root,
    grouping_preview,
    intake_readiness,
    rename_preview,
    resolve_intake_image_token,
)

main = Blueprint("main", __name__)


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


def _intake_page_context(relative=""):
    readiness = intake_readiness()
    browser = None
    error = None
    if readiness["readable"]:
        try:
            browser = browse_intake(configured_intake_root(), relative)
        except ValueError:
            error = "The selected intake folder is invalid, unavailable, or unsafe."
    return readiness, browser, error


# ---------- Catalogue Intake read-only previews ----------


@main.route("/image-preparation")
@login_required
def image_preparation():
    relative = request.args.get("path", "")[:1024]
    readiness, browser, error = _intake_page_context(relative)
    return render_template(
        "image_preparation/index.html",
        readiness=readiness,
        browser=browser,
        intake_error=error,
    ), (400 if error else 200)


@main.route("/image-preparation/group")
@login_required
def image_preparation_group():
    relative = request.args.get("path", "")[:1024]
    readiness, browser, error = _intake_page_context(relative)
    preview = None
    if browser and not error:
        try:
            preview = grouping_preview(configured_intake_root(), relative)
        except ValueError:
            error = "The grouping preview could not safely inspect this intake folder."
    return render_template(
        "image_preparation/group.html",
        readiness=readiness,
        browser=browser,
        preview=preview,
        intake_error=error,
    ), (400 if error else 200)


@main.route("/image-preparation/rename")
@login_required
def image_preparation_rename():
    relative = request.args.get("path", "")[:1024]
    entered_prefix = request.args.get("prefix", "")[:128]
    readiness, browser, error = _intake_page_context(relative)
    preview = None
    prefix_error = None
    if browser and entered_prefix and not error:
        try:
            preview = rename_preview(
                configured_intake_root(), relative, entered_prefix
            )
        except ValueError as preview_error:
            prefix_error = str(preview_error)
    return render_template(
        "image_preparation/rename.html",
        readiness=readiness,
        browser=browser,
        preview=preview,
        entered_prefix=entered_prefix,
        prefix_error=prefix_error,
        intake_error=error,
    ), (400 if error else 200)


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
    return render_template("dashboard.html", dashboard=build_dashboard_data())


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
            "label": METADATA_ISSUE_DEFINITIONS[issue_key]["label"],
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
    return render_template("product_detail.html", workspace=product_workspace(product))


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
    return jsonify(result.to_dict()), 200 if result.valid else 400


def _deep_merge(dst, src):
    """
    Recursively merge src into dst (dst mutated and returned).
    Scalars/arrays in src overwrite dst, dicts merge by key.
    """
    if not isinstance(dst, dict) or not isinstance(src, dict):
        return src
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v
    return dst


def _prune(o):
    """Remove empty strings/containers and None."""
    if isinstance(o, dict):
        out = {}
        for k, v in o.items():
            pv = _prune(v)
            if pv not in ("", None, [], {}):
                out[k] = pv
        return out
    if isinstance(o, list):
        out = []
        for v in o:
            pv = _prune(v)
            if pv not in ("", None, [], {}):
                out.append(pv)
        return out
    return o


@main.route("/edit_products/<sku>/save", methods=["POST"])
@login_required
@csrf.exempt
def product_save_json(sku):
    """
    Editor POST target.
    Body: { kind: "shared"|"override", data: {...} }

    - Loads and validates existing JSON from disk (if present)
    - Deep‑merges new data into existing (so required fields like collection_type survive)
    - Prunes empties
    - Validates before acquiring the operation lock or creating side effects
    - Writes a timestamped .bak backup
    - Writes atomically
    - Uses existing override-update or shared-collection refresh orchestration
    """
    p = Product.query.filter_by(sku=sku).first_or_404()
    try:
        payload = request.get_json(force=True)
    except BadRequest:
        return (
            jsonify(
                {
                    "error": "invalid_json",
                    "errors": [
                        {
                            "path": "$",
                            "code": "invalid_json",
                            "message": "The submitted content is not valid JSON.",
                        }
                    ],
                    "submitted_content": request.get_data(
                        cache=True, as_text=True
                    ),
                }
            ),
            400,
        )
    if not isinstance(payload, dict):
        return jsonify({"error": "request body must be a JSON object"}), 400
    kind = (payload.get("kind") or "").lower()
    new_data = payload.get("data", {})
    replace_document = payload.get("replace") is True

    if kind not in ("shared", "override"):
        return jsonify({"error": "invalid kind"}), 400

    active_operation = get_active_operation()
    if active_operation is not None:
        return (
            jsonify(
                {
                    "error": "catalogue_operation_active",
                    "message": (
                        f"Another catalogue operation ({active_operation['operation_type']}) "
                        "is already active."
                    ),
                    "active_operation": active_operation,
                }
            ),
            409,
        )

    submitted_validation = validate_product_info(new_data, "override")
    if not submitted_validation.valid:
        result = submitted_validation.to_dict()
        result.update(
            {
                "error": "metadata_validation_failed",
                "submitted_data": new_data,
            }
        )
        return jsonify(result), 400

    asset = (
        ProductAsset.query.filter_by(product_id=p.id, kind="info", label=kind)
        .order_by(ProductAsset.id.desc())
        .first()
    )
    if not asset or not asset.path:
        return jsonify({"error": f"{kind} JSON not present for this product"}), 400

    source = metadata_source(p, kind)
    target = str(source["path"]) if source["path"] else asset.path
    folder = os.path.dirname(target)

    settings = Settings.query.first()
    catalogue_root = os.path.realpath(settings.product_folder or "") if settings else ""
    target_real = os.path.realpath(target)
    try:
        target_allowed = (
            catalogue_root
            and os.path.commonpath([catalogue_root, target_real]) == catalogue_root
        )
    except ValueError:
        target_allowed = False
    if not target_allowed:
        return jsonify({"error": "metadata source path not allowed"}), 403

    cleanup_metadata_temporaries(
        target, operation_active=lambda: get_active_operation() is not None
    )

    # Load existing JSON (if any)
    existing = {}
    if os.path.exists(target):
        try:
            with open(target, "r", encoding="utf-8") as f:
                existing = json.load(f)
            if not isinstance(existing, dict):
                raise ValueError("existing metadata root is not an object")
        except Exception:
            return (
                jsonify(
                    {
                        "error": "existing_metadata_invalid",
                        "errors": [
                            {
                                "path": "$",
                                "code": "existing_metadata_invalid",
                                "message": (
                                    "The existing product_info.json must be corrected "
                                    "before it can be saved through the editor."
                                ),
                            }
                        ],
                        "submitted_data": new_data,
                    }
                ),
                400,
            )

    # Merge + prune
    merged = new_data.copy() if replace_document else _deep_merge(existing.copy(), new_data)
    merged = _prune(merged)
    validation = validate_product_info(
        merged, "collection" if kind == "shared" else "override"
    )
    if not validation.valid:
        result = validation.to_dict()
        result.update(
            {
                "error": "metadata_validation_failed",
                "submitted_data": new_data,
            }
        )
        return jsonify(result), 400

    try:
        collection_relpath = None
        operation_scope = {"sku": p.sku, "kind": kind}
        if kind == "shared":
            settings = Settings.query.first()
            catalogue_root = (
                os.path.realpath(settings.product_folder or "") if settings else ""
            )
            collection_folder = os.path.realpath(folder)
            try:
                inside_catalogue = (
                    catalogue_root
                    and os.path.commonpath([catalogue_root, collection_folder])
                    == catalogue_root
                )
            except ValueError:
                inside_catalogue = False
            if not inside_catalogue:
                return jsonify({"error": "shared collection path not allowed"}), 403
            collection_relpath = os.path.relpath(
                collection_folder, catalogue_root
            ).replace(os.sep, "/")
            operation_scope.update(
                {
                    "scope_kind": "collection",
                    "collection_relpath": collection_relpath,
                    "exhaustive": True,
                }
            )
        operation = acquire_catalogue_operation(
            "shared_collection_update" if kind == "shared" else "product_update",
            operation_scope,
        )
    except CatalogueOperationActive as error:
        return _operation_conflict(error)

    try:
        os.makedirs(folder, exist_ok=True)

        # Backup
        try:
            if os.path.exists(target):
                create_metadata_backup(target)
        except Exception:
            pass

        # Atomic write
        atomic_write_text(
            target, json.dumps(merged, ensure_ascii=False, indent=4)
        )

        # Product overrides retain ordinary marker selection. Shared metadata is
        # handled by an explicit exhaustive collection refresh instead.
        if kind == "override":
            try:
                with open(os.path.join(folder, ".update"), "w") as f:
                    f.write("1")
            except Exception:
                pass

        # Kick update scan
        run_id = uuid.uuid4().hex
        start_scan(
            current_app._get_current_object(),
            run_id,
            scan_mode="shared_collection" if kind == "shared" else "update",
            operation_id=operation.id,
            scope=operation_scope,
            collection_relpath=collection_relpath,
        )
        try:
            collection_name = collection_display_name(p.collection) if p.collection else None
            if kind == "override":
                notify_override_created(p.sku, product=p.title, collection=collection_name)
            else:
                affected = Product.query.filter_by(collection_id=p.collection_id).count()
                notify_editor_saved("collection metadata", p.sku, collection=collection_name, affected=affected)
        except Exception:
            current_app.logger.warning("Discord metadata notification failed safely")
    except Exception as error:
        finish_catalogue_operation(operation.id, status="failed", error=error)
        return jsonify({"error": "failed to save product metadata"}), 500

    return jsonify(
        {
            "ok": True,
            "run_id": run_id,
            "warnings": [issue.to_dict() for issue in validation.warnings],
        }
    )


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
@csrf.exempt
def api_delete_override(product_id):
    """
    Deletes the override product_info.json (if present) for a product,
    removes its ProductAsset record(s), drops a .update file in that folder,
    and kicks an 'update' scan. Returns { run_id }.
    """
    p = Product.query.get_or_404(product_id)

    asset = (
        ProductAsset.query.filter_by(product_id=p.id, kind="info", label="override")
        .order_by(ProductAsset.id.desc())
        .first()
    )
    if not asset or not asset.path:
        return jsonify({"error": "override not present"}), 400

    settings = Settings.query.first()
    root = (settings.product_folder or "").rstrip("/")
    real_path = os.path.realpath(asset.path)
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
@csrf.exempt
def api_override_create(product_id):
    """
    Create a new override product_info.json in a subfolder under the collection root.
    Body: { rel: "sub/folder" }
    - Writes empty {} JSON if not present
    - Registers ProductAsset(kind='info', label='override')
    - Touches .update
    - Kicks update scan
    Returns: { ok, run_id, edit_url }
    """
    p = Product.query.get_or_404(product_id)
    data = request.get_json(force=True) or {}
    rel = (data.get("rel") or "").strip()

    root = _collection_root_for_product(product_id)
    if not root or not os.path.isdir(root):
        return jsonify({"error": "collection root not found"}), 400

    abs_path = os.path.realpath(os.path.join(root, rel))
    root_real = os.path.realpath(root)
    if not abs_path.startswith(root_real + os.sep) and abs_path != root_real:
        return jsonify({"error": "invalid folder"}), 400
    if not os.path.isdir(abs_path):
        return jsonify({"error": "folder does not exist"}), 400

    existing_override = (
        ProductAsset.query.filter_by(
            product_id=p.id, kind="info", label="override"
        )
        .order_by(ProductAsset.id.desc())
        .first()
    )
    if existing_override and existing_override.path:
        existing_path = os.path.realpath(existing_override.path)
        try:
            existing_is_safe = os.path.commonpath(
                [root_real, existing_path]
            ) == root_real
        except ValueError:
            existing_is_safe = False
        if existing_is_safe and os.path.isfile(existing_path):
            return jsonify(
                {
                    "ok": True,
                    "created": False,
                    "run_id": None,
                    "edit_url": url_for(
                        "main.product_edit", product_id=p.id, label="override"
                    ),
                }
            )

    try:
        operation = acquire_catalogue_operation(
            "product_update", {"sku": p.sku, "kind": "override_create"}
        )
    except CatalogueOperationActive as error:
        return _operation_conflict(error)

    json_path = os.path.join(abs_path, "product_info.json")
    try:
        if not os.path.exists(json_path):
            os.makedirs(abs_path, exist_ok=True)
            atomic_write_json(json_path, {})

        ProductAsset.query.filter_by(
            product_id=p.id, kind="info", label="override"
        ).delete()
        db.session.add(
            ProductAsset(
                product_id=p.id,
                variation_id=None,
                path=json_path,
                kind="info",
                label="override",
                is_primary=False,
            )
        )
        db.session.commit()

        # Touch .update
        try:
            atomic_write_text(os.path.join(abs_path, ".update"), "1")
        except Exception:
            pass

        # Kick update scan
        run_id = uuid.uuid4().hex
        start_scan(
            current_app._get_current_object(),
            run_id,
            scan_mode="update",
            operation_id=operation.id,
        )
        try:
            notify_override_created(
                p.sku,
                product=p.title,
                collection=collection_display_name(p.collection) if p.collection else None,
            )
        except Exception:
            current_app.logger.warning("Discord override notification failed safely")
        edit_url = url_for("main.product_edit", product_id=p.id, label="override")
    except Exception as error:
        db.session.rollback()
        finish_catalogue_operation(operation.id, status="failed", error=error)
        return jsonify({"error": "failed to create override"}), 500

    return jsonify(
        {"ok": True, "created": True, "run_id": run_id, "edit_url": edit_url}
    )


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
    s = Settings.query.first()
    setup_state = detect_setup_state()
    return render_template(
        "setup/initial_scan.html", settings=s, setup_state=setup_state
    )


@main.route("/initial-scan/start", methods=["POST"])
@login_required
@csrf.exempt
def initial_scan_start():
    """
    Start a scan. We do NOT ping Discord from here to avoid double 'start' notifications,
    because scan_runner already sends the start + completion notifications.
    """
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
    # placeholder, take user to next step if you like
    return redirect(url_for("main.web_sync_page"))


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
    )


@main.route("/scanner/start", methods=["POST"])
@login_required
@csrf.exempt
def scanner_start():
    payload = request.get_json(silent=True) or request.form.to_dict()
    mode = payload.get("mode")
    if mode not in {item["key"] for item in SCAN_MODES}:
        return jsonify({"error": "unsupported_scan_mode", "message": "That scan mode is not supported."}), 400
    confirmed = payload.get("confirm_operation") is True or payload.get("confirm_operation") == "true"
    if not confirmed:
        return jsonify({"error": "confirmation_required", "message": "Confirm the selected scan before it starts."}), 400
    if mode == "full" and not (payload.get("confirm_full_regeneration") is True or payload.get("confirm_full_regeneration") == "true"):
        return jsonify({"error": "full_confirmation_required", "message": "Full scan requires the additional catalogue-wide confirmation."}), 400
    readiness = scanner_readiness()
    if readiness["active"]:
        return _operation_conflict(CatalogueOperationActive(readiness["active"]))
    if not readiness["mounts_ready"]:
        failures = [check["label"] for check in readiness["checks"] if not check["ok"]]
        return jsonify({"error": "scanner_not_ready", "message": "Required scanner storage or database checks did not pass.", "failures": failures}), 409
    run_id = uuid.uuid4().hex
    scope = {"scan_mode": mode, "initiating_source": "scanner_workspace"}
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
    )


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
        "terminal": view["status"] in {"succeeded", "partial", "failed", "interrupted"},
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
    return _render_planned(
        "Woo Sync",
        "Future",
        "WooCommerce connection and publishing workflows are planned for a future phase.",
    )


@main.route("/sync")
@login_required
def sync():
    return redirect(url_for("main.woo_sync"))


@main.route("/orders")
@login_required
def orders():
    return _render_planned(
        "Orders",
        "Future",
        "Order management will be introduced only after a verified WooCommerce integration.",
    )


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
        new_admin = User(
            email=form.email.data.strip(),
            username=form.username.data.strip(),
            password=generate_password_hash(form.password.data.strip()),
            is_admin=True,
        )
        db.session.add(new_admin)
        db.session.commit()
        login_user(new_admin)
        flash("Admin user created and logged in.", "success")
        return redirect(url_for("main.initial_settings"))
    return render_template("setup/setup.html", form=form)


@main.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    form = ForgotPasswordForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.strip()).first()
        if user:
            token = generate_reset_token(user.email)
            reset_url = url_for("main.reset_password", token=token, _external=True)
            print(f"Send this reset link to user: {reset_url}")
            flash(
                "If that email exists in our system, a reset link has been sent.",
                "info",
            )
        return redirect(url_for("main.login"))
    return render_template("auth/forgot_password.html", form=form)


@main.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    email = verify_reset_token(token)
    if not email:
        flash("Reset link is invalid or expired.", "danger")
        return redirect(url_for("main.forgot_password"))
    form = ResetPasswordForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=email).first()
        if user:
            user.password = generate_password_hash(form.password.data.strip())
            db.session.commit()
            flash("Your password has been updated.", "success")
            return redirect(url_for("main.login"))
    return render_template("auth/reset_password.html", form=form)


@main.route("/initial-settings", methods=["GET", "POST"])
@login_required
def initial_settings():
    form = InitialSettingsForm()
    if form.validate_on_submit():
        settings = Settings.query.first()
        if not settings:
            settings = Settings()
            db.session.add(settings)
        settings.product_folder = form.product_folder.data.strip()
        settings.output_folder = form.output_folder.data.strip()
        settings.url_prefix = form.url_prefix.data.strip()
        db.session.commit()
        flash("Initial settings saved.", "success")
        return redirect(url_for("main.initial_scan_page"))
    return render_template("setup/initial_settings.html", form=form)


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
