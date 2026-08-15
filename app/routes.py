# app/routes.py
import os
import json
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
)
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.exceptions import BadRequest
from werkzeug.security import check_password_hash, generate_password_hash
from sqlalchemy.orm import aliased

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
    Product,
    ProductAsset,
)
from app.utils.token_utils import generate_reset_token, verify_reset_token
from app.utils.scan_runner import start_scan, stream_lines, get_progress, _runs
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

# ---------- Dashboard ----------


@main.route("/")
def dashboard():
    if not User.query.first():
        return redirect(url_for("main.setup"))
    if not current_user.is_authenticated:
        return redirect(url_for("main.login"))
    return render_template("dashboard.html")


# ---------- Products page + API ----------


@main.route("/products")
@login_required
def products():
    # The table UI fetches from /api/products
    return render_template("products.html")


@main.route("/api/edit_products")
@login_required
def api_products():
    """
    Returns products with flags + paths for shared/override info JSON
    and a derived collection name based on the JSON file path.
    """
    Shared = aliased(ProductAsset)
    Override = aliased(ProductAsset)

    q = (
        db.session.query(
            Product.id,
            Product.sku,
            Product.title,
            Product.product_type,
            Product.collection_type,
            Shared.id.label("shared_id"),
            Shared.path.label("shared_path"),
            Override.id.label("override_id"),
            Override.path.label("override_path"),
        )
        .outerjoin(
            Shared,
            (Shared.product_id == Product.id)
            & (Shared.kind == "info")
            & (Shared.label == "shared"),
        )
        .outerjoin(
            Override,
            (Override.product_id == Product.id)
            & (Override.kind == "info")
            & (Override.label == "override"),
        )
        .order_by(Product.sku.asc())
    )

    rows = []
    for r in q.all():
        chosen_path = r.shared_path or r.override_path or ""
        collection_name = ""
        if chosen_path:
            try:
                collection_name = os.path.basename(os.path.dirname(chosen_path))
            except Exception:
                collection_name = ""

        rows.append(
            {
                "id": r.id,
                "sku": r.sku,
                "title": r.title,
                "type": "variable" if r.product_type == "variable" else "simple",
                "collection": collection_name,
                "shared_present": bool(r.shared_id),
                "override_present": bool(r.override_id),
                "shared_path": r.shared_path or "",
                "override_path": r.override_path or "",
            }
        )

    return jsonify({"items": rows})


# ---------- Editor routes used by Products page ----------
@main.route("/edit_products")
@login_required
def edit_products():
    # The table UI fetches from /api/products
    return render_template("edit_products.html")


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

    p = db.get_or_404(Product, product_id)
    asset = (
        ProductAsset.query.filter_by(product_id=p.id, kind="info", label=label)
        .order_by(ProductAsset.id.desc())
        .first()
    )
    json_path = asset.path if (asset and asset.path) else ""
    return render_template("editor.html", product=p, kind=label, json_path=json_path)


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

    if kind not in ("shared", "override"):
        return jsonify({"error": "invalid kind"}), 400

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

    target = asset.path
    folder = os.path.dirname(target)

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
    merged = _deep_merge(existing.copy(), new_data)
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
        ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        try:
            if os.path.exists(target):
                with open(target, "rb") as rf, open(
                    f"{target}.bak.{ts}", "wb"
                ) as wf:
                    wf.write(rf.read())
        except Exception:
            pass

        # Atomic write
        tmp = f"{target}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=4)
        os.replace(tmp, target)

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
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump({}, f, ensure_ascii=False, indent=2)

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
            with open(os.path.join(abs_path, ".update"), "w") as f:
                f.write("1")
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
        edit_url = url_for("main.product_edit", product_id=p.id, label="override")
    except Exception as error:
        db.session.rollback()
        finish_catalogue_operation(operation.id, status="failed", error=error)
        return jsonify({"error": "failed to create override"}), 500

    return jsonify({"ok": True, "run_id": run_id, "edit_url": edit_url})


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
    return jsonify(get_progress(run_id))


@main.route("/initial-scan/done/<run_id>")
@login_required
def initial_scan_done(run_id):
    # placeholder, take user to next step if you like
    return redirect(url_for("main.web_sync_page"))


@main.route("/web-sync", methods=["GET"])
@login_required
def web_sync_page():
    return render_template("setup/web_sync.html")


# ---------- Other pages ----------


@main.route("/scanner")
@login_required
def scanner():
    return render_template("scanner.html")


@main.route("/sync")
@login_required
def sync():
    return render_template("sync.html")


@main.route("/orders")
@login_required
def orders():
    return render_template("orders.html")


@main.route("/pos")
@login_required
def pos():
    return render_template("pos.html")


@main.route("/tools")
@login_required
def tools():
    return render_template("tools.html")


@main.route("/site")
@login_required
def site_manager():
    return render_template("site.html")


@main.route("/settings")
@login_required
def settings():
    return render_template("settings.html")


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
    return render_template("auth/login.html")


@main.route("/folder-picker")
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
