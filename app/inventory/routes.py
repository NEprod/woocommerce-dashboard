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
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError, OperationalError
from werkzeug.exceptions import RequestEntityTooLarge

from app import db
from . import service as stock
from .models import BomScenario, CostingSettings, Equipment, Identity, Item, Location, ProductCostingProfile, ProductionBatch, Transaction

inventory = Blueprint("inventory", __name__, url_prefix="/production")
MOVE_NAMES = {"opening":"Opening stock", "receipt":"Purchase receipt", "production_consumption":"Material used",
              "production_output":"Good components made", "transfer":"Location transfer",
              "adjustment":"Count adjustment", "reversal":"Reversal", "use":"Used / removed stock"}
ITEM_TEXT = ("name", "category", "description", "specification", "supplier", "supplier_sku", "supplier_url",
             "filament_type", "filament_colour", "unit", "pack_unit", "yield_qty", "default_location_id")
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


@inventory.errorhandler(RequestEntityTooLarge)
def upload_too_large(error):
    if request.endpoint == 'inventory.edit_item':
        return render_template('inventory/upload_error.html'), 413
    return error


@inventory.context_processor
def view_helpers():
    return dict(kinds=stock.KINDS, steps=stock.STEPS, units=stock.UNITS, move_names=MOVE_NAMES,
                qty=stock.quantity_text, pounds=stock.money_text, breadcrumbs=location_path,
                unassigned_id=stock.UNASSIGNED_ID)


def location_path(row):
    names = []
    while row is not None:
        # Disambiguate a familiar legacy label without using it as identity.
        name = row.name
        if row.id != stock.UNASSIGNED_ID and name.strip().casefold() == "unassigned":
            name += f" (location · {row.identity.code})"
        names.append(name)
        row = row.parent
    return " / ".join(reversed(names))


def _record(model, record_id):
    return db.get_or_404(model, record_id)


def _page(statement):
    return db.paginate(statement, per_page=40, max_per_page=40, error_out=False)


def _image(upload):
    from .images import optimise
    return optimise(upload)


@inventory.get("/")
def home():
    return render_template("inventory/home.html")


@inventory.route("/costing/settings", methods=["GET", "POST"])
def costing_settings():
    from .profitability import DEFAULTS, decimal
    row = db.session.get(CostingSettings, 1)
    values = {**DEFAULTS, **(json.loads(row.document) if row else {})}
    error = ""
    if request.method == "POST":
        try:
            stock.writer_lock()
            if request.form.get("action") == "fee_profile":
                row = db.session.get(CostingSettings, 1)
                if row:
                    db.session.refresh(row)
                config = json.loads(row.document) if row else {}
                profiles = config.get('fee_profiles', [])
                profile_id = request.form.get('profile_id')
                if request.form.get('settings_version') and int(request.form['settings_version']) != (row.version if row else 0):
                    raise ValueError('Fee profiles changed in another session. Reload before saving.')
                existing_profile = next((p for p in profiles if p['id'] == profile_id), None) if profile_id else None
                if profile_id and not existing_profile:
                    raise ValueError('The selected fee profile no longer exists.')
                if not profile_id and len(profiles) >= 30:
                    raise ValueError('At most 30 payment fee profiles are supported.')
                replacement = dict(id=profile_id or uuid4().hex, name=stock.clean(request.form.get('name'), 'Fee profile name', 160, required=True),
                                     percent=str(decimal(request.form.get('percent'), 'Total percentage fees', maximum='99.99')),
                                     fixed=str(decimal(request.form.get('fixed'), 'Fixed transaction charge')),
                                     notes=stock.clean(request.form.get('notes'), 'Notes', 4000))
                if existing_profile is not None:
                    existing_profile.update(replacement)
                else:
                    profiles.append(replacement)
                config['fee_profiles'] = profiles
                if row:
                    row.document, row.version = json.dumps(config), row.version + 1
                else:
                    db.session.add(CostingSettings(id=1, document=json.dumps(config), version=1))
            elif request.form.get("action") == "equipment":
                watts = request.form.get("watts", "").strip()
                source = request.form.get("source", "estimated")
                if source not in {"estimated", "specification", "measured"}:
                    raise ValueError("Choose a supported measurement status.")
                selected = _record(Equipment, int(request.form['equipment_id'])) if request.form.get('equipment_id') else Equipment(version=0)
                if request.form.get('equipment_id'):
                    db.session.refresh(selected)
                    if selected.version != int(request.form.get('equipment_version', '0')):
                        raise ValueError('Equipment changed in another session. Reload before saving.')
                selected.name = stock.clean(request.form.get("name"), "Equipment name", 160, required=True)
                selected.category = stock.clean(request.form.get('category'), 'Equipment type / category', 100)
                selected.make_model = stock.clean(request.form.get('make_model'), 'Make / model', 160)
                selected.profile = stock.clean(request.form.get("profile"), "Operating profile", 160)
                selected.watts = str(decimal(watts, "Average watts")) if watts else None
                selected.source, selected.notes = source, stock.clean(request.form.get("notes"), "Notes", 4000)
                selected.active = request.form.get('archived') != 'yes'
                selected.version += 1
                db.session.add(selected)
            else:
                row = db.session.get(CostingSettings, 1)
                if row:
                    db.session.refresh(row)
                version = int(request.form.get("version", "0"))
                if version != (row.version if row else 0):
                    raise ValueError("Settings changed in another session. Reload before saving.")
                saved = json.loads(row.document) if row else {}
                for key in DEFAULTS:
                    if key == 'print_waste_rate' and key not in request.form:
                        continue  # Older clients must not overwrite the new optional default.
                    value = request.form.get(key, "").strip()
                    saved[key] = None if not value and key in {"tariff", "labour_rate"} else str(decimal(value, key,
                        maximum="99.99" if key == "fee_percent" else "100" if key == "print_waste_rate" else "1000000000"))
                if row:
                    row.document, row.version = json.dumps(saved), row.version + 1
                else:
                    db.session.add(CostingSettings(id=1, document=json.dumps(saved), version=1))
            db.session.commit()
            flash("Production & Costing settings saved. Stock and historical costs are unchanged.", "success")
            return redirect(url_for("inventory.costing_settings"), code=303)
        except (ValueError, IntegrityError, OperationalError) as exc:
            db.session.rollback()
            error = str(exc) if isinstance(exc, ValueError) else "Settings could not be saved; reload and retry."
    return render_template("inventory/costing_settings.html", values=values, version=row.version if row else 0,
                           equipment=Equipment.query.order_by(Equipment.name).all(), error=error,
                           selected_equipment=db.session.get(Equipment, request.args.get('equipment', type=int)) if request.args.get('equipment') else None), 400 if error else 200


@inventory.route("/bom", methods=["GET", "POST"], defaults={"scenario_id": None})
@inventory.route("/bom/<int:scenario_id>", methods=["GET", "POST"])
@inventory.route("/product/<int:product_id>/bom", methods=["GET", "POST"], defaults={"scenario_id": None})
def bom_planner(scenario_id, product_id=None):
    from .profitability import estimate
    from .product_bom import product_costs
    from app.models import Product
    scenario = _record(BomScenario, scenario_id) if scenario_id else None
    product_id = scenario.product_id if scenario and scenario.product_id else product_id
    product = _record(Product, product_id) if product_id else None
    try:
        costs = product_costs(product) if product else {}
    except (ValueError, OSError):
        costs = {}
    sku = scenario.variant_sku if scenario and scenario.variant_sku else request.args.get('sku', product.sku if product else '')
    projection = costs.get(sku) if product else None
    product_lines = [dict(item_id=line['item_id'], quantity=line['quantity'], unit=line['unit'])
                     for line in (projection or {}).get('rows', []) if not line['excluded']]
    document = json.loads(scenario.document) if scenario else dict(lines=[], equipment=[])
    if document.get('kind') == 'bundle':
        if request.method == 'POST':
            abort(410, 'The duplicate bundle editor is retired. Saved data is retained unchanged.')
        return render_template('inventory/retired_bundle.html', scenario=scenario, document=document)
    if product and not scenario:
        profile = db.session.get(ProductCostingProfile, (product.id, sku))
        if profile:
            document = json.loads(profile.document)
        else:
            from app.sale_pricing import catalogue as local_prices
            prices = local_prices(product)
            selected_price = next((v['price'] for v in prices['variants'] if v['sku'] == sku), None)
            document['selling_price'] = str(selected_price or product.regular_price or '0')
    name, error, result = scenario.name if scenario else "", "", None
    if product:
        document['lines'] = product_lines
        name = name or f'{product.title} · {sku}'
    if request.method == "POST":
        try:
            name = stock.clean(request.form.get("name"), "Scenario name", 160, required=True)
            columns = [request.form.getlist(key) for key in ("item_id", "quantity", "unit")]
            if len({len(c) for c in columns}) != 1:
                raise ValueError("Send complete BOM lines.")
            equipment_columns = [request.form.getlist(key) for key in ("equipment_id", "minutes")]
            if len({len(c) for c in equipment_columns}) != 1:
                raise ValueError("Send complete equipment usage lines.")
            document = dict(lines=[dict(item_id=int(i), quantity=q, unit=u) for i, q, u in zip(*columns) if i],
                            equipment=[dict(equipment_id=int(i), minutes=m) for i, m in zip(*equipment_columns) if i],
                            **{key: request.form.get(key) or "0" for key in ("labour_minutes", "other")},
                            selling_price=request.form.get('selling_price',''),
                            price_rounding=request.form.get('price_rounding','exact'))
            print_flags = request.form.getlist('print_material')
            if print_flags:
                if len(print_flags) != len(columns[0]):
                    raise ValueError('Send one print-material selection per inventory line.')
                for line, flag in zip(document['lines'], [flag for identity,flag in zip(columns[0],print_flags) if identity]):
                    line['print_material'] = flag
            manual = [request.form.getlist(k) for k in ('manual_name','manual_quantity','manual_unit','manual_cost')]
            if len({len(c) for c in manual}) != 1:
                raise ValueError('Send complete hypothetical material rows.')
            extras = {key: request.form.getlist('manual_' + key) for key in
                      ('cost_mode','purchase_cost','purchase_quantity','purchase_unit','conversion_factor','print_material')}
            if any(column and len(column) != len(manual[0]) for column in extras.values()):
                raise ValueError('Send complete hypothetical purchase details for each material row.')
            for index, (n,q,u,c) in enumerate(zip(*manual)):
                if n.strip():
                    line = dict(source='manual',name=n,quantity=q,unit=u,unit_cost=c)
                    line.update({key: column[index] for key,column in extras.items() if column})
                    document['lines'].append(line)
            pack_fields = ('name','quantity','unit','cost','cost_mode','purchase_cost','purchase_quantity','purchase_unit','conversion_factor','item_id')
            packs = {key: request.form.getlist('pack_' + key) for key in pack_fields}
            if any(packs.values()) and len({len(column) for column in packs.values()}) != 1:
                raise ValueError('Send complete packaging rows.')
            for index in range(len(packs['name'])):
                entry = {key: values[index] for key,values in packs.items()}
                if entry['item_id']:
                    selected = db.session.get(Item, int(entry['item_id']))
                    if not selected or selected.kind != 'purchased':
                        raise ValueError('Select a purchased packaging component.')
                    document['lines'].append(dict(item_id=selected.id, quantity=entry['quantity'], unit=entry['unit'], purpose='packaging'))
                elif entry['name'].strip():
                    entry['unit_cost'] = entry.pop('cost')
                    entry.pop('item_id')
                    document['lines'].append(dict(entry, source='manual', purpose='packaging'))
            for index, usage in enumerate(document['equipment']):
                for field in ('timing_mode','usable_units','allocation','share_percent'):
                    entered = request.form.getlist(field)
                    if entered:
                        if len(entered) != len(equipment_columns[0]):
                            raise ValueError('Send one allocation configuration per selected equipment line.')
                        selected_index = [i for i,value in enumerate(equipment_columns[0]) if value][index]
                        usage[field] = entered[selected_index]
            for key in ("buffer", "markup", "labour_rate", "fee_profile", "print_waste_rate"):
                if request.form.get(key, "").strip():
                    document[key] = request.form[key].strip()
            if product:
                document['lines'] = product_lines
            settings = db.session.get(CostingSettings, 1)
            config = json.loads(settings.document) if settings else {}
            result = estimate(document, config, direct_materials=not bool(product))
            if product and (not projection or projection.get('state') != 'complete'):
                raise ValueError('The existing product BOM must be configured and complete before saving finished-product profitability.')
            if request.form.get("action") in {"save", "duplicate"}:
                if not product:
                    document['_snapshot'] = dict(result=result, settings=config,
                        equipment=[dict(id=e.id,name=e.name,profile=e.profile,watts=e.watts,source=e.source)
                                   for e in Equipment.query.filter(Equipment.id.in_([u['equipment_id'] for u in document['equipment']])).all()])
                stock.writer_lock()
                if scenario and request.form.get("action") != "duplicate":
                    db.session.refresh(scenario)
                    if int(request.form.get("version", "0")) != scenario.version:
                        raise ValueError("This scenario changed in another session. Reload before saving.")
                    scenario.name, scenario.document, scenario.version = name, json.dumps(document), scenario.version + 1
                else:
                    scenario = BomScenario(name=name, document=json.dumps(document), version=1,
                                           product_id=product_id, variant_sku=sku if product else None)
                    db.session.add(scenario)
                if product and request.form.get('action') == 'save':
                    profile = db.session.get(ProductCostingProfile, (product.id, sku))
                    assumptions = {k: v for k, v in document.items() if k != 'lines'}
                    if profile:
                        profile.document = json.dumps(assumptions)
                    else:
                        db.session.add(ProductCostingProfile(product_id=product.id, sku=sku, document=json.dumps(assumptions)))
                db.session.commit()
                flash("Local BOM scenario saved. No stock, catalogue product or Shopify price changed.", "success")
                return redirect(url_for("inventory.bom_planner", scenario_id=scenario.id), code=303)
        except (ValueError, KeyError, IntegrityError, OperationalError) as exc:
            db.session.rollback()
            error = str(exc) if isinstance(exc, ValueError) else "Could not calculate or save this scenario. Check all inputs."
    elif scenario:
        settings = db.session.get(CostingSettings, 1)
        config = json.loads(settings.document) if settings else {}
        if not product and document.get('_snapshot'):
            result = document['_snapshot']['result']
        else:
            result = estimate(document, config, direct_materials=not bool(product))
            if not product:
                result['historical_context_missing'] = True
    from .profitability import DEFAULTS
    settings = db.session.get(CostingSettings, 1)
    defaults = {**DEFAULTS, **(json.loads(settings.document) if settings else {})}
    return render_template("inventory/bom_planner.html", scenario=scenario, name=name, document=document, defaults=defaults,
                           result=result, error=error, items=_item_choices(include_archived=True), equipment=Equipment.query.order_by(Equipment.name).all(),
                           product=product, sku=sku, costs=costs,
                           fee_profiles=json.loads(db.session.get(CostingSettings, 1).document).get('fee_profiles', []) if db.session.get(CostingSettings, 1) else [],
                           scenarios=[s for s in BomScenario.query.order_by(BomScenario.name).limit(100).all()
                                      if json.loads(s.document).get('kind') != 'bundle' and not json.loads(s.document).get('_archived')]), 400 if error else 200


@inventory.post('/bom/<int:scenario_id>/archive')
def archive_bom_scenario(scenario_id):
    stock.writer_lock()
    scenario = _record(BomScenario, scenario_id)
    document = json.loads(scenario.document)
    if scenario.product_id or document.get('kind') == 'bundle' or request.form.get('confirmed') != 'yes':
        abort(400)
    if request.form.get('version') != str(scenario.version):
        abort(409, 'Scenario changed; reload before deleting from the list.')
    document['_archived'] = True
    scenario.document, scenario.version = json.dumps(document), scenario.version + 1
    db.session.commit()
    flash('Scenario removed from saved list; historical data retained.', 'success')
    return redirect(url_for('inventory.bom_planner'), code=303)


@inventory.post('/bom/process-preview')
def process_preview():
    from .profitability import process_cost, decimal, material_money
    request.max_content_length = 16384
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error='Send production process fields as an object.'), 400
    try:
        hours = decimal(body.get('runtime_hours'), 'Hours')
        minutes = decimal(body.get('runtime_minutes'), 'Minutes')
        if hours != hours.to_integral_value() or minutes >= 60:
            raise ValueError('Enter whole non-negative hours and minutes from 0 to less than 60.')
        line = {**body, 'minutes': str(hours * 60 + minutes)}
        settings = db.session.get(CostingSettings, 1)
        result = process_cost(line, json.loads(settings.document) if settings else {})
        return jsonify(minutes=line['minutes'], total_minutes=str(result['total_minutes']),
                       attributable_minutes=str(result['attributable_minutes']),
                       electricity=material_money(result['electricity']),
                       missing=result['missing'], provisional=result['provisional'])
    except (ValueError, KeyError) as exc:
        return jsonify(error=str(exc)), 400


@inventory.post('/bom/hypothetical-preview')
def hypothetical_preview():
    from .profitability import hypothetical_cost, material_money
    request.max_content_length = 16384
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error='Send hypothetical material fields as an object.'), 400
    try:
        if body.get('item_id'):
            from .profitability import estimate
            selected = db.session.get(Item, int(body['item_id']))
            if not selected or (body.get('purpose') != 'material' and selected.kind != 'purchased'):
                raise ValueError('Select a purchased packaging component.')
            result = estimate(dict(lines=[dict(item_id=selected.id, quantity=body.get('quantity'), unit=body.get('unit'), purpose='packaging' if body.get('purpose') != 'material' else 'material')]),
                              direct_materials=True, selling_fees=False)
            amount = result['lines'][0]['cost'] if result['lines'] else None
            return jsonify(normalized_unit=body.get('unit'), normalized_unit_cost=None, amount=amount, display_amount=amount, inventory=True)
        result = hypothetical_cost(body)
        return jsonify(normalized_unit=result['normalized_unit'],
                       normalized_unit_cost=None if result['normalized_cost'] is None else format(result['normalized_cost'], 'f'),
                       amount=None if result['amount'] is None else format(result['amount'], 'f'),
                       display_amount=material_money(result['amount']))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@inventory.route('/bundles', methods=['GET', 'POST'])
def bundle_planner():
    if request.method == 'POST':
        abort(410, 'The duplicate bundle editor is retired. No data was changed.')
    return redirect(url_for('promotions.index'), code=302)


@inventory.route("/batches/create", methods=["GET", "POST"])
def create_batches():
    """Reserve batch barcodes only; no receipt or material consumption."""
    error = ""
    if request.method == "POST":
        try:
            count = int(request.form.get("count", ""))
            expected = request.form.get("planned_good", "").strip()
            planned_good = int(expected) if expected else None
            rows = stock.reserve_batches(request.form.get("item_id"), count,
                                         request.form.get("request_id", ""), planned_good=planned_good)
            db.session.commit()
            flash(f"{len(rows)} batch labels reserved. No stock has changed.", "success")
            return redirect(url_for("inventory.batch_detail", batch_id=rows[0].id), code=303)
        except (ValueError, TypeError, IntegrityError, OperationalError) as exc:
            db.session.rollback()
            error = str(exc) if isinstance(exc, (ValueError, TypeError)) else "Batch identities could not be reserved. Inspect existing batches before retrying."
    choices = _item_choices("manufactured")
    return render_template("inventory/create_batches.html", choices=choices, error=error,
                           recommendations={item.id: stock.production_recommendation(item) for item in choices},
                           values=request.form, nonce=request.form.get("request_id") or uuid4().hex), 400 if error else 200


@inventory.get("/batch/<int:batch_id>")
def batch_detail(batch_id):
    row = _record(ProductionBatch, batch_id)
    siblings = db.session.scalars(select(ProductionBatch).where(
        ProductionBatch.allocation_id == row.allocation_id).order_by(ProductionBatch.allocation_ordinal)).all()
    return render_template("inventory/batch.html", row=row, siblings=siblings,
                           available=stock.batch_available(row.id), total=stock.balance(row.component_id))


@inventory.route("/batch/<int:batch_id>/<action>", methods=["GET", "POST"])
def batch_operation(batch_id, action):
    from types import SimpleNamespace
    if action not in {"complete", "remove"}:
        abort(404)
    row = _record(ProductionBatch, batch_id)
    recipe = [SimpleNamespace(material_id=r['material_id'], material=db.session.get(Item, r['material_id']),
                              qty=stock.number(r['quantity'], 'Planned material'), unit=r['unit'])
              for r in json.loads(row.planned_recipe)]
    error, approval = "", None
    values = request.form.to_dict() if request.method == "POST" else {
        "good": str(row.planned_good), "failed": "0",
        "location": str(row.component.default_location_id or ''),
        **{f"actual_{r.material_id}": stock.quantity_text(r.qty) for r in recipe}}
    nonce = values.get("request_id") or uuid4().hex
    if request.method == "POST":
        try:
            if values.get("approval"):
                signed = _signer().loads(values["approval"], max_age=1200)
                if (signed.get("owner"), signed.get("batch_id"), signed.get("action")) != (current_user.get_id(), batch_id, action):
                    raise BadData("Wrong batch or operator")
                data = signed["data"]
                if action == "complete":
                    usage = {int(k): tuple(v) for k, v in data["usage"].items()}
                    tx = stock.complete_batch(batch_id, data["good"], data["failed"], usage,
                                              data["location"], signed["request_id"], actor=current_user.get_id(),
                                              actor_label=current_user.username)
                else:
                    tx = stock.remove_from_batch(batch_id, data["quantity"], data["location"], data["reason"],
                                                 signed["request_id"], actor=current_user.get_id(), actor_label=current_user.username)
                db.session.commit()
                flash("Batch stock transaction recorded. Inspect the retained receipt below.", "success")
                return redirect(url_for("inventory.transaction", transaction_id=tx.id), code=303)
            if not re.fullmatch(r"[a-f0-9]{32}", nonce):
                raise ValueError("Open a fresh batch form before reviewing.")
            data = {"location": int(values["location"]) if values.get("location") else None}
            if action == "complete":
                data.update(good=int(values.get("good", "")), failed=int(values.get("failed", "")),
                            usage={r.material_id: (values.get(f"actual_{r.material_id}", ""),
                                                  int(values[f"source_{r.material_id}"])) for r in recipe})
            else:
                data.update(quantity=values.get("quantity", ""), reason=values.get("reason", ""))
            approval = _signer().dumps(dict(owner=current_user.get_id(), batch_id=batch_id, action=action,
                                            data=data, request_id=nonce))
        except (ValueError, KeyError, BadData, IntegrityError, OperationalError) as exc:
            db.session.rollback()
            error = str(exc) if isinstance(exc, ValueError) else "Could not record the batch operation. Inspect its receipt before retrying."
    return render_template("inventory/batch_operation.html", row=row, action=action, values=values,
                           nonce=nonce, approval=approval, error=error, locations=_location_choices(),
                           recipe=recipe, available=stock.batch_available(row.id)), 400 if error else 200


@inventory.get("/batch/<int:batch_id>/label")
def batch_label(batch_id):
    return render_template("inventory/batch_label.html", row=_record(ProductionBatch, batch_id))


@inventory.get("/batch/<int:batch_id>/barcode.svg")
def batch_barcode(batch_id):
    from barcode import Code128
    from barcode.writer import SVGWriter
    output = BytesIO()
    Code128(_record(ProductionBatch, batch_id).code, writer=SVGWriter()).write(output, {"write_text": False})
    return Response(output.getvalue(), mimetype="image/svg+xml")


@inventory.get("/items/<group>")
def items(group):
    if group not in {"materials", "components", "catalogue"}:
        abort(404)
    statement = select(Item).join(Identity, Identity.id == Item.id)
    if group != 'catalogue':
        statement = statement.where(Item.kind == "material" if group == "materials" else Item.kind.in_(("purchased", "manufactured")))
    search = request.args.get("q", "").strip()[:120]
    state = request.args.get("state", "active")
    kind = request.args.get("kind", "")
    if search:
        statement = statement.where(or_(*(col.icontains(search, autoescape=True) for col in
                                         (Item.name, Item.category, Identity.code, Item.supplier_sku))))
    if state in {"active", "archived"}:
        statement = statement.where(Item.active.is_(state == "active"))
    if group in {"components", "catalogue"} and kind in {"purchased", "manufactured", "material"}:
        statement = statement.where(Item.kind == kind)
    page = _page(statement.order_by(Item.name, Item.id))
    return render_template("inventory/items.html", group=group, page=page, search=search, state=state, kind=kind,
                           balances={row.id: stock.balance(row.id) for row in page.items})


@inventory.get('/catalogue')
def catalogue():
    return items('catalogue')


@inventory.get("/item/<int:record_id>")
def item(record_id):
    row = _record(Item, record_id)
    estimate = stock.recipe_estimate(row) if row.kind == "manufactured" else stock.rate(row)
    recipe_breakdown = None
    if row.kind == 'manufactured':
        from .profitability import component_breakdown
        from .catalogue_bom import settings
        recipe_breakdown = component_breakdown(row, settings())
    return render_template("inventory/item.html", row=row, balances=stock.item_balances(row.id),
                           total=stock.balance(row.id), has_image=row.image is not None, recipe_breakdown=recipe_breakdown,
                           estimate=None if estimate is None else int((estimate * stock.SCALE).to_integral_value(rounding=ROUND_HALF_UP)))


@inventory.route("/item/new/<kind>", methods=["GET", "POST"], defaults={"record_id": None})
@inventory.route("/item/<int:record_id>/edit", methods=["GET", "POST"], defaults={"kind": None})
def edit_item(record_id, kind):
    row = _record(Item, record_id) if record_id is not None else None
    kind = row.kind if row else kind
    if kind not in stock.KINDS:
        abort(404)
    error, error_status = "", 400
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
                old_code = row.identity.code if row else None
                if old_code and 'code' in values and stock.identifier(values['code']) != old_code:
                    from .item_maintenance import audit
                    audit('barcode_correction', row, current_user, before=old_code, after=stock.identifier(values['code']))
                    db.session.flush()  # The database correction fence requires this audit in the same transaction.
                saved = stock.save_item(kind, values, row=row, requirements=[line for line in packed if line[0] or line[1]],
                                        image=image, remove_image=values.get("remove_image") == "yes")
                db.session.commit()
                flash("Inventory item saved. Stock is unchanged.", "success")
                return redirect(url_for("inventory.item", record_id=saved.id), code=303)
            except ValueError as exc:
                db.session.rollback()
                error = str(exc)
            except (IntegrityError, OperationalError, OSError):
                db.session.rollback()
                error = 'The item or image could not be stored. The previous saved record and image are unchanged. Check persistent appdata space and permissions, then retry.'
                error_status = 503
    elif row:
        values = {key: getattr(row, key) if getattr(row, key) is not None else "" for key in ITEM_TEXT}
        values.update({key: stock.quantity_text(getattr(row, key)) for key in ITEM_NUMBERS})
        values.update(version=row.version, active="yes" if row.active else "", code=row.identity.code)
        packed = [(str(r.material_id), stock.quantity_text(r.qty), r.unit) for r in row.materials]
        if kind == 'manufactured':
            packed = [(str(r.material_id), stock.recipe_piece_text(r.qty, row.yield_qty), r.unit)
                      for r in row.materials]
    else:
        values, packed = {"active":"yes", "unit":"piece" if kind != "material" else "g"}, []
    raw_materials = _item_choices("material", include_archived=bool(row and not row.active)) if kind == "manufactured" else ()
    recipe_originals = {str(r.material_id): {'material': str(r.material_id), 'piece': stock.recipe_piece_text(r.qty, row.yield_qty),
        'total': stock.quantity_text(r.qty), 'unit': r.unit, 'good': row.yield_qty}
        for r in row.materials} if row and kind == 'manufactured' else {}
    from .images import HEIF_SUPPORTED
    return render_template("inventory/edit_item.html", row=row, kind=kind, values=values, error=error, heif_supported=HEIF_SUPPORTED,
                           materials=raw_materials, requirements=packed or [("", "", "")], recipe_originals=recipe_originals,
                           locations=_location_choices(),
                           has_image=row is not None and row.image is not None), error_status if error else 200


@inventory.post('/item/<int:record_id>/delete')
def delete_item(record_id):
    from .item_maintenance import delete_unused, audit
    row = _record(Item, record_id)
    group = 'materials' if row.kind == 'material' else 'components'
    try:
        stock.writer_lock()
        db.session.refresh(row)
        audit('delete_unused', row, current_user, before=row.identity.code)
        delete_unused(row, request.form)
        db.session.commit()
    except (ValueError, IntegrityError, OperationalError) as exc:
        db.session.rollback()
        flash(str(exc) if isinstance(exc, ValueError) else 'Deletion could not be completed safely. No record was removed.', 'error')
        return redirect(url_for('inventory.edit_item', record_id=record_id), code=303)
    flash('Unused item deleted. Its internal ID and barcode remain reserved. No stock or historical records were removed.', 'success')
    return redirect(url_for('inventory.items', group=group), code=303)


@inventory.get("/item/<int:record_id>/image.jpg")
def item_image(record_id):
    content = _record(Item, record_id).image
    if content is None:
        abort(404)
    from .images import stored_mime
    return Response(content, mimetype=stored_mime(content), headers={"X-Content-Type-Options":"nosniff"})


@inventory.post('/item/purchase-cost-preview')
def purchase_cost_preview():
    request.max_content_length = 16384
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error='Send purchase fields as an object.'), 400
    try:
        quantity = stock.number(data.get('pack_qty'), 'Purchase pack quantity', positive=True)
        price = stock.cost_number(data.get('pack_price'), 'Purchase pack price')
        unit = data.get('unit')
        rate = stock.purchase_unit_rate(quantity, data.get('pack_unit') or unit, price, unit)
        from .profitability import material_money
        return jsonify(unit=unit, unit_cost=None if rate is None else format(rate,'f'), display_cost=material_money(rate))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@inventory.get("/item/barcode-check")
def item_barcode_check():
    """Check an editor scan without saving or changing an identity."""
    try:
        batch = db.session.scalar(select(ProductionBatch).where(
            ProductionBatch.code == str(request.args.get('code', '')).strip().upper()))
        if batch:
            return jsonify(error=f'Barcode {batch.code} is reserved for a production batch.'), 409
        assigned = stock.lookup(request.args.get("code"))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    if assigned is not None and not (assigned.kind == 'item' and str(assigned.id) == request.args.get('item_id')):
        return jsonify(error=stock.item_barcode_conflict(assigned)), 409
    return jsonify(available=True)


@inventory.get("/locations")
def locations():
    search = request.args.get("q", "").strip()[:120]
    state = request.args.get("state", "active")
    statement = select(Location).join(Identity, Identity.id == Location.id).where(Location.id != stock.UNASSIGNED_ID)
    if search:
        statement = statement.where(or_(Location.name.icontains(search, autoescape=True), Identity.code.icontains(search, autoescape=True)))
    if state in {"active", "archived"}:
        statement = statement.where(Location.active.is_(state == "active"))
    return render_template("inventory/locations.html", page=_page(statement.order_by(Location.name, Location.id)),
                           search=search, state=state)


@inventory.get("/location/<int:record_id>")
def location(record_id):
    if record_id == stock.UNASSIGNED_ID:
        abort(404)
    row = _record(Location, record_id)
    children = db.session.scalars(select(Location).where(Location.parent_id == row.id).order_by(Location.name)).all()
    return render_template("inventory/location.html", row=row, balances=stock.location_balances(row.id), children=children)


@inventory.route("/location/new", methods=["GET", "POST"], defaults={"record_id": None})
@inventory.route("/location/<int:record_id>/edit", methods=["GET", "POST"])
def edit_location(record_id):
    if record_id == stock.UNASSIGNED_ID:
        abort(404)
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
            batch = db.session.scalar(select(ProductionBatch).where(ProductionBatch.code == code.strip().upper()))
            if batch:
                return redirect(url_for("inventory.batch_detail", batch_id=batch.id))
            identity = stock.lookup(code)
            if identity and identity.kind == 'item' and db.session.get(Item, identity.id) is None:
                raise ValueError('This barcode belongs to a deleted unused item and remains reserved. Choose another item.')
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
    statement = select(Location).where(Location.id != stock.UNASSIGNED_ID)
    if not include_archived:
        statement = statement.where(Location.active.is_(True))
    return sorted(db.session.scalars(statement).all(), key=lambda loc: location_path(loc).casefold())


def _signer():
    return URLSafeTimedSerializer(current_app.secret_key, salt="tlc-workshop-confirmation-v1")


def _location_scan_signer():
    return URLSafeTimedSerializer(current_app.secret_key, salt="tlc-workshop-location-scan-v1")


def _scan_binding(kind, item_id, nonce):
    return dict(owner=current_user.get_id(), kind=kind, item_id=item_id, request_id=nonce)


@inventory.post("/stock/<kind>/scan-location")
def scan_location(kind):
    if kind not in {"receive", "move"}:
        abort(404)
    try:
        nonce = request.form.get("request_id", "")
        if not re.fullmatch(r"[a-f0-9]{32}", nonce):
            raise ValueError("Open a new stock form before scanning.")
        item = stock.item_for(request.form.get("item_id"))
        if kind == "receive" and item.kind == "manufactured":
            raise ValueError("Use Complete Production for a manufactured component.")
        identity = stock.lookup(request.form.get("code"))
        if identity is None:
            raise ValueError("Unknown location barcode. Check the label; no location was created.")
        if identity.kind != "location":
            raise ValueError("This is an item barcode. Scan a storage location barcode instead.")
        location = stock.location_for(identity.id)
        token = _location_scan_signer().dumps(dict(_scan_binding(kind, str(item.id), nonce), destination=str(location.id)))
        return jsonify(destination=str(location.id), label=location_path(location), proof=token)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


def _attach_scan_evidence(kind, fields, nonce, token):
    if not (kind == "receive" and fields.get("unassigned") != "yes"
            or kind == "move" and fields.get("source") == stock.UNASSIGNED):
        return
    try:
        scanned = _location_scan_signer().loads(token, max_age=1200)
    except BadData:
        scanned = None
    expected = dict(_scan_binding(kind, fields.get("item_id"), nonce), destination=fields.get("destination"))
    if scanned != expected:
        raise ValueError("Scan the selected physical destination before reviewing this transaction.")
    fields["verified_destination"] = scanned["destination"]


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
            fields = {k:v for k,v in values.items() if k in {"item_id", "original", "qty", "source", "destination", "reason", "entry_unit", "cost", "unassigned"}
                      or re.fullmatch(r"(actual|source)_[1-9][0-9]{0,12}", k)}
            _attach_scan_evidence(kind, fields, nonce, values.get("location_proof", ""))
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
    suggestions = sorted(stock.receipt_locations(row.id), key=lambda pair: (pair[1] <= 0, location_path(pair[0]).casefold())) if row and kind == "receive" else ()
    material_counts = {req.material_id: {loc.id:stock.quantity_text(n) for loc,n in stock.item_balances(req.material_id)}
                       for req in row.materials} if row and kind == "production" else {}
    return render_template("inventory/workflow.html", kind=kind, row=row, error=error, original=original,
                           values=values, nonce=uuid4().hex, choices=_item_choices(available_kinds) if not row and not original else (),
                           locations=_location_choices() if row else (), counts=counts, material_counts=material_counts,
                           suggestions=suggestions, total=stock.balance(row.id) if row else None), 400 if error else 200


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
    if record_id == stock.UNASSIGNED_ID:
        abort(404)
    identity = _record(Identity, record_id)
    row = _record(Item if identity.kind == "item" else Location, identity.id)
    size = request.args.get("size", "100")
    return render_template("inventory/label.html", row=row, size=size if size in {"75", "100", "125"} else "100")


@inventory.get('/item/<int:record_id>/stock-card')
def stock_card(record_id):
    """Ephemeral print view of the saved item, never an identity or stock action."""
    row = _record(Item, record_id)
    from .stock_cards import barcode_geometry
    return render_template('inventory/stock_card.html', row=row,
                           barcode=barcode_geometry(row.identity.code if row.identity else None),
                           location=db.session.get(Location, row.default_location_id) if row.default_location_id else None,
                           has_image=row.image is not None)


@inventory.get("/label/<int:record_id>/<style>.svg")
def label_art(record_id, style):
    if record_id == stock.UNASSIGNED_ID:
        abort(404)
    code = _record(Identity, record_id).code
    output = BytesIO()
    if style == "code128":
        from barcode import Code128
        from barcode.writer import SVGWriter
        options = {"write_text":False, "module_width":0.29, "quiet_zone":4}
        if request.args.get('layout') == 'stock-card':
            from .stock_cards import barcode_geometry
            geometry = barcode_geometry(code)
            if not geometry['fits']:
                abort(422, geometry['warning'])
            options = {key: value for key,value in geometry.items() if key not in {'fits','warning'}}
        Code128(code, writer=SVGWriter()).write(output, options)
    elif style == "qr":
        import qrcode
        from qrcode.image.svg import SvgPathFillImage
        qrcode.make(code, image_factory=SvgPathFillImage).save(output)
    else:
        abort(404)
    return Response(output.getvalue(), mimetype="image/svg+xml", headers={"X-Content-Type-Options":"nosniff"})
