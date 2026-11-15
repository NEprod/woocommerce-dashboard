"""Authenticated, local Production & Operations screens and stock confirmations."""
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO
import json
import re
from uuid import uuid4

from flask import (Blueprint, Response, abort, current_app, flash, jsonify, redirect,
                   render_template, request, url_for)
from flask_login import current_user, login_required
from itsdangerous import BadData, URLSafeTimedSerializer
from PIL import Image, UnidentifiedImageError
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError, OperationalError

from app import db
from . import service as stock
from .models import Identity, Item, Location, Transaction

inventory = Blueprint("inventory", __name__, url_prefix="/production")
MOVE_NAMES = {"opening":"Opening stock", "receipt":"Purchase receipt", "production_consumption":"Material used",
              "production_output":"Good components made", "transfer":"Location transfer",
              "adjustment":"Count adjustment", "reversal":"Reversal"}
ITEM_TEXT = ("name", "category", "description", "specification", "supplier", "supplier_sku", "supplier_url",
             "filament_type", "filament_colour", "unit", "pack_unit", "yield_qty")
ITEM_NUMBERS = ("pack_qty", "pack_price", "minimum", "reorder", "sheet_length", "sheet_width", "sheet_thickness",
                "spool_grams")


@inventory.before_request
@login_required
def administrator():
    if not current_user.is_admin:
        abort(403)


@inventory.after_request
def private(response):
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@inventory.context_processor
def view_helpers():
    return dict(kinds=stock.KINDS, steps=stock.STEPS, units=stock.UNITS, move_names=MOVE_NAMES,
                qty=stock.quantity_text, pounds=stock.money_text, breadcrumbs=location_path)


def location_path(row):
    names = []
    while row is not None:
        names.append(row.name)
        row = row.parent
    return " / ".join(reversed(names))


def _record(model, record_id):
    return db.get_or_404(model, record_id)


def _page(statement):
    return db.paginate(statement, per_page=40, max_per_page=40, error_out=False)


def _image(upload):
    if not upload or not upload.filename:
        return None
    content = upload.stream.read(2 * 1024 * 1024 + 1)
    if len(content) > 2 * 1024 * 1024:
        raise ValueError("Choose an image up to 2 MB.")
    try:
        with Image.open(BytesIO(content), formats=("JPEG", "PNG", "WEBP")) as source:
            if source.width * source.height > 12_000_000 or getattr(source, "is_animated", False):
                raise ValueError("Choose a still image of at most 12 megapixels.")
            source.thumbnail((640, 640))
            pic = Image.new("RGB", source.size, "white")
            layer = source.convert("RGBA")
            pic.paste(layer, mask=layer.getchannel("A"))
            output = BytesIO()
            pic.save(output, "JPEG", quality=82)
            return output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValueError("Choose a readable JPEG, PNG or WebP image.") from None


@inventory.get("/")
def home():
    return render_template("inventory/home.html")


@inventory.get("/items/<group>")
def items(group):
    if group not in {"materials", "components"}:
        abort(404)
    statement = select(Item).join(Identity, Identity.id == Item.id).where(
        Item.kind == "material" if group == "materials" else Item.kind.in_(("purchased", "manufactured")))
    search = request.args.get("q", "").strip()[:120]
    state = request.args.get("state", "active")
    kind = request.args.get("kind", "")
    if search:
        statement = statement.where(or_(*(col.icontains(search, autoescape=True) for col in
                                         (Item.name, Item.category, Identity.code, Item.supplier_sku))))
    if state in {"active", "archived"}:
        statement = statement.where(Item.active.is_(state == "active"))
    if group == "components" and kind in {"purchased", "manufactured"}:
        statement = statement.where(Item.kind == kind)
    page = _page(statement.order_by(Item.name, Item.id))
    return render_template("inventory/items.html", group=group, page=page, search=search, state=state, kind=kind,
                           balances={row.id: stock.balance(row.id) for row in page.items})


@inventory.get("/item/<int:record_id>")
def item(record_id):
    row = _record(Item, record_id)
    estimate = stock.recipe_estimate(row) if row.kind == "manufactured" else stock.rate(row)
    return render_template("inventory/item.html", row=row, balances=stock.item_balances(row.id),
                           total=stock.balance(row.id), has_image=row.image is not None,
                           estimate=None if estimate is None else int((estimate * stock.SCALE).to_integral_value(rounding=ROUND_HALF_UP)))


@inventory.route("/item/new/<kind>", methods=["GET", "POST"], defaults={"record_id": None})
@inventory.route("/item/<int:record_id>/edit", methods=["GET", "POST"], defaults={"kind": None})
def edit_item(record_id, kind):
    row = _record(Item, record_id) if record_id is not None else None
    kind = row.kind if row else kind
    if kind not in stock.KINDS:
        abort(404)
    error = ""
    if request.method == "POST":
        values = request.form.to_dict()
        columns = [request.form.getlist(key) for key in ("material_id", "material_qty", "material_unit")]
        if len({len(column) for column in columns}) != 1 or len(columns[0]) > 30:
            abort(400, "Send complete recipe lines with at most 30 source materials.")
        packed = list(zip(*columns))
        if values.get("action") == "add-line" and kind == "manufactured":
            if len(packed) < 30:
                packed.append(("", "", ""))
        else:
            try:
                image = _image(request.files.get("image"))
                stock.writer_lock()
                if row:
                    db.session.refresh(row)
                saved = stock.save_item(kind, values, row=row, requirements=[line for line in packed if line[0] or line[1]],
                                        image=image, remove_image=values.get("remove_image") == "yes")
                db.session.commit()
                flash("Inventory item saved. Stock is unchanged.", "success")
                return redirect(url_for("inventory.item", record_id=saved.id), code=303)
            except ValueError as exc:
                db.session.rollback()
                error = str(exc)
    elif row:
        values = {key: getattr(row, key) if getattr(row, key) is not None else "" for key in ITEM_TEXT}
        values.update({key: stock.quantity_text(getattr(row, key)) for key in ITEM_NUMBERS})
        values.update(version=row.version, active="yes" if row.active else "", code=row.identity.code)
        packed = [(str(r.material_id), stock.quantity_text(r.qty), r.unit) for r in row.materials]
    else:
        values, packed = {"active":"yes", "unit":"piece" if kind != "material" else "g"}, []
    raw_materials = _item_choices("material", include_archived=bool(row and not row.active)) if kind == "manufactured" else ()
    return render_template("inventory/edit_item.html", row=row, kind=kind, values=values, error=error,
                           materials=raw_materials, requirements=packed or [("", "", "")],
                           has_image=row is not None and row.image is not None), 400 if error else 200


@inventory.get("/item/<int:record_id>/image.jpg")
def item_image(record_id):
    content = _record(Item, record_id).image
    if content is None:
        abort(404)
    return Response(content, mimetype="image/jpeg", headers={"X-Content-Type-Options":"nosniff"})


@inventory.get("/item/barcode-check")
def item_barcode_check():
    """Check an editor scan without saving or changing an identity."""
    try:
        assigned = stock.lookup(request.args.get("code"))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    if assigned is not None:
        return jsonify(error=stock.item_barcode_conflict(assigned)), 409
    return jsonify(available=True)


@inventory.get("/locations")
def locations():
    search = request.args.get("q", "").strip()[:120]
    state = request.args.get("state", "active")
    statement = select(Location).join(Identity, Identity.id == Location.id)
    if search:
        statement = statement.where(or_(Location.name.icontains(search, autoescape=True), Identity.code.icontains(search, autoescape=True)))
    if state in {"active", "archived"}:
        statement = statement.where(Location.active.is_(state == "active"))
    return render_template("inventory/locations.html", page=_page(statement.order_by(Location.name, Location.id)),
                           search=search, state=state)


@inventory.get("/location/<int:record_id>")
def location(record_id):
    row = _record(Location, record_id)
    children = db.session.scalars(select(Location).where(Location.parent_id == row.id).order_by(Location.name)).all()
    return render_template("inventory/location.html", row=row, balances=stock.location_balances(row.id), children=children)


@inventory.route("/location/new", methods=["GET", "POST"], defaults={"record_id": None})
@inventory.route("/location/<int:record_id>/edit", methods=["GET", "POST"])
def edit_location(record_id):
    row = _record(Location, record_id) if record_id is not None else None
    error = ""
    if request.method == "POST":
        values = request.form.to_dict()
        try:
            stock.writer_lock()
            if row:
                db.session.refresh(row)
            saved = stock.save_location(values, row=row)
            db.session.commit()
            flash("Location saved. Stock is unchanged.", "success")
            return redirect(url_for("inventory.location", record_id=saved.id), code=303)
        except ValueError as exc:
            db.session.rollback()
            error = str(exc)
    elif row:
        values = dict(name=row.name, description=row.description, code=row.identity.code, version=row.version,
                      parent_id=str(row.parent_id or ""), active="yes" if row.active else "")
    else:
        values = dict(active="yes")
    choices = _location_choices(include_archived=bool(row and not row.active))
    return render_template("inventory/edit_location.html", row=row, values=values, choices=choices, error=error), 400 if error else 200


@inventory.get("/lookup")
def lookup():
    code = request.args.get("code", "")[:80]
    error = ""
    if "code" in request.args:
        try:
            identity = stock.lookup(code)
            if identity:
                return redirect(url_for(f"inventory.{identity.kind}", record_id=identity.id))
            error = "No item or location has that barcode. Check the label, or search Materials, Components or Locations."
        except ValueError as exc:
            error = str(exc)
    return render_template("inventory/lookup.html", code=code, error=error), 400 if error else 200


def _item_choices(kind=None, *, include_archived=False):
    statement = select(Item)
    if kind:
        statement = statement.where(Item.kind.in_(kind if isinstance(kind, tuple) else (kind,)))
    if not include_archived:
        statement = statement.where(Item.active.is_(True))
    return db.session.scalars(statement.order_by(Item.name, Item.id)).all()


def _location_choices(*, include_archived=False):
    statement = select(Location)
    if not include_archived:
        statement = statement.where(Location.active.is_(True))
    return sorted(db.session.scalars(statement).all(), key=lambda loc: location_path(loc).casefold())


def _signer():
    return URLSafeTimedSerializer(current_app.secret_key, salt="tlc-workshop-confirmation-v1")


@inventory.route("/stock/<kind>", methods=["GET", "POST"])
def workflow(kind):
    if kind not in stock.STEPS:
        abort(404)
    error = ""
    values = dict(request.args) if request.method == "GET" else request.form.to_dict()
    if request.method == "POST" and "approval" in values:
        start = {"kind":kind}
        try:
            signed = _signer().loads(values["approval"], max_age=1200)
            if signed["owner"] != current_user.get_id() or signed["kind"] != kind:
                raise BadData("Wrong operator or operation")
            start.update({key:signed["data"][key] for key in ("item_id", "original") if key in signed["data"]})
            actor = current_user.get_id()
            tx = stock.post(kind, signed["data"], signed["request_id"], signed["digest"], actor=actor,
                            actor_label=current_user.username)
            db.session.commit()
            flash("Stock transaction recorded.", "success")
            return redirect(url_for("inventory.transaction", transaction_id=tx.id), code=303)
        except (BadData, ValueError, IntegrityError, OperationalError) as exc:
            db.session.rollback()
            message = str(exc) if isinstance(exc, ValueError) else (
                "This approval expired or belongs to a different operator. Build a fresh preview." if isinstance(exc, BadData)
                else "Stock could not be recorded. Check Stock History before starting a new preview.")
            return render_template("inventory/blocked.html", message=message, start=start), 409
    if request.method == "POST":
        try:
            nonce = values.get("request_id", "")
            if not re.fullmatch(r"[a-f0-9]{32}", nonce):
                raise ValueError("Open a new stock form before reviewing.")
            fields = {k:v for k,v in values.items() if k in {"item_id", "original", "qty", "source", "destination", "reason", "entry_unit", "cost"}
                      or re.fullmatch(r"(actual|source)_[1-9][0-9]{0,12}", k)}
            change = stock.plan(kind, fields)
            signed = _signer().dumps(dict(owner=current_user.get_id(), kind=kind, data=fields,
                                          request_id=nonce, digest=stock.digest(change)))
            return render_template("inventory/preview.html", change=change, approval=signed)
        except ValueError as exc:
            error = str(exc)
    available_kinds = ("manufactured",) if kind == "production" else (("material", "purchased") if kind == "receive" else None)
    row, original = None, None
    if kind == "reversal":
        original_id = values.get("original", "")
        if not re.fullmatch(r"[a-f0-9]{32}", original_id):
            abort(404)
        original = _record(Transaction, original_id)
    elif values.get("item_id"):
        try:
            row = stock.item_for(values["item_id"])
            if available_kinds and row.kind not in available_kinds:
                raise ValueError("Choose an item suitable for this stock operation.")
        except ValueError as exc:
            row, error = None, str(exc)
    counts = {loc.id: stock.quantity_text(n) for loc,n in stock.item_balances(row.id)} if row else {}
    material_counts = {req.material_id: {loc.id:stock.quantity_text(n) for loc,n in stock.item_balances(req.material_id)}
                       for req in row.materials} if row and kind == "production" else {}
    return render_template("inventory/workflow.html", kind=kind, row=row, error=error, original=original,
                           values=values, nonce=uuid4().hex, choices=_item_choices(available_kinds) if not row and not original else (),
                           locations=_location_choices() if row else (), counts=counts, material_counts=material_counts), 400 if error else 200


@inventory.get("/history")
def history():
    kind = request.args.get("kind", "")
    item_id = request.args.get("item_id", type=int)
    statement = select(Transaction)
    if kind in stock.STEPS:
        statement = statement.where(Transaction.kind == kind)
    if item_id:
        statement = statement.where(or_(Transaction.item_id == item_id, Transaction.moves.any(item_id=item_id)))
    return render_template("inventory/history.html", page=_page(statement.order_by(Transaction.created_at.desc(), Transaction.id)),
                           kind=kind, selected_item=db.session.get(Item, item_id) if item_id else None)


@inventory.get("/history/<transaction_id>")
def transaction(transaction_id):
    tx = _record(Transaction, transaction_id)
    details = json.loads(tx.details)
    reversed_by = db.session.scalar(select(Transaction.id).where(Transaction.reverses_id == tx.id))
    return render_template("inventory/transaction.html", tx=tx, details=details, reversed_by=reversed_by,
                           unit_cost=int(Decimal(details["unit_cost"]).to_integral_value(rounding=ROUND_HALF_UP)) if "unit_cost" in details else None)


@inventory.get("/label/<int:record_id>")
def label(record_id):
    identity = _record(Identity, record_id)
    row = _record(Item if identity.kind == "item" else Location, identity.id)
    size = request.args.get("size", "100")
    return render_template("inventory/label.html", row=row, size=size if size in {"75", "100", "125"} else "100")


@inventory.get("/label/<int:record_id>/<style>.svg")
def label_art(record_id, style):
    code = _record(Identity, record_id).code
    output = BytesIO()
    if style == "code128":
        from barcode import Code128
        from barcode.writer import SVGWriter
        Code128(code, writer=SVGWriter()).write(output, {"write_text":False, "module_width":0.29, "quiet_zone":4})
    elif style == "qr":
        import qrcode
        from qrcode.image.svg import SvgPathFillImage
        qrcode.make(code, image_factory=SvgPathFillImage).save(output)
    else:
        abort(404)
    return Response(output.getvalue(), mimetype="image/svg+xml", headers={"X-Content-Type-Options":"nosniff"})
