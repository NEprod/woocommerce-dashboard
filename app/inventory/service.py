"""Decimal-safe workshop stock planning and posting within SQLite."""
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal, ROUND_HALF_UP, localcontext
from hashlib import sha256
import json
import re
from urllib.parse import urlsplit

from sqlalchemy import func, select, text
from sqlalchemy.orm import aliased
from app import db
from .models import Identity, Item, Location, Movement, RecipeMaterial, Transaction

SCALE = 1_000_000
UNASSIGNED_ID = 0
UNASSIGNED = "unassigned"
MAX_QTY = 10**14          # 100 million stock units
MAX_COST = 10**15         # £1 billion per recorded transaction
KINDS = {"material": "Material", "purchased": "Purchased component", "manufactured": "Manufactured component"}
STEPS = {"opening": "Opening Stock", "receive": "Receive Stock", "production": "Complete Production",
         "move": "Move Stock", "count": "Stock Check", "reversal": "Reverse Transaction"}
UNITS = {"piece": ("piece", 1), "sheet": ("sheet", 1), "g": ("mass", 1), "kg": ("mass", 1000),
         "ml": ("volume", 1), "l": ("volume", 1000), "mm": ("length", 1), "m": ("length", 1000)}
CODE = re.compile(r"[A-Z0-9][A-Z0-9-]{0,23}\Z", re.ASCII | re.IGNORECASE)


def quantity_text(value):
    if value is None:
        return ""
    return format(Decimal(value) / SCALE, "f")


def money_text(value):
    if value is None:
        return "Not known"
    amount = (Decimal(value) / SCALE).quantize(Decimal(".000001")).normalize()
    return "£" + format(amount, ".2f" if amount.as_tuple().exponent >= -2 else "f")


def number(raw, label, *, optional=False, positive=False, bound=MAX_QTY):
    value = str(raw if raw is not None else "").strip()
    if optional and not value:
        return None
    if not re.fullmatch(r"\d{1,12}(\.\d{1,6})?",value):
        raise ValueError(f"{label}: enter a non-negative number with up to six decimal places.")
    scaled = int(Decimal(value)*SCALE)
    if scaled > bound or positive and not scaled:
        raise ValueError(f"{label}: enter a quantity within range{' and above zero' if positive else ''}.")
    return scaled


def cost_number(raw, label="Total cost", *, optional=True):
    return number(raw, label, optional=optional, bound=MAX_COST)


def converted(qty, source, target):
    if source not in UNITS or target not in UNITS or UNITS[source][0] != UNITS[target][0]:
        raise ValueError(f"Cannot convert {source or 'missing unit'} to {target}. Use a compatible unit.")
    value, remainder = divmod(qty*UNITS[source][1], UNITS[target][1])
    if remainder or value > MAX_QTY:
        raise ValueError("Converted quantity is outside the supported range or precision.")
    if target == "piece" and value % SCALE:
        raise ValueError("Pieces must be whole numbers.")
    return value


def clean(value, label, limit, *, required=False):
    value = str(value if value is not None else "").strip()
    if len(value)>limit or required and not value:
        raise ValueError(f"{label}: {'enter 1 to' if required else 'use at most'} {limit} characters.")
    return value


def identifier(raw):
    value = str(raw or "").strip()
    if not CODE.fullmatch(value):
        raise ValueError("Use a stable code of 1–24 letters, digits or hyphens, starting with a letter or digit.")
    return value.upper()


def lookup(raw):
    return db.session.scalar(select(Identity).where(Identity.code == identifier(raw)))


def item_barcode_conflict(identity):
    owner = db.session.get(Location if identity.kind == "location" else Item, identity.id)
    if owner is not None:
        label = "location" if identity.kind == "location" else KINDS[owner.kind].lower()
        return f"Barcode {identity.code} is already assigned to {label} “{owner.name}”. Choose a different code."
    return "That barcode is already assigned, including archived records. Choose a different code."


def _new_identity(raw, kind):
    code = identifier(raw)
    existing = lookup(code)
    if existing is not None:
        if kind == "item":
            raise ValueError(item_barcode_conflict(existing))
        raise ValueError("That barcode is already assigned, including archived records. Choose a different code.")
    return Identity(code=code,kind=kind)


def writer_lock():
    # Force SQLite's writer lock before reading balances, rules or idempotency
    # state. No data changes here; the surrounding transaction owns the lock.
    db.session.execute(text("UPDATE inventory_item SET version=version WHERE 0"))


def balance(item_id, location_id=None):
    query=select(func.coalesce(func.sum(Movement.qty),0)).where(Movement.item_id==item_id)
    if location_id is not None:
        query=query.where(Movement.location_id==location_id)
    return db.session.scalar(query)


def item_balances(item_id):
    amount=func.sum(Movement.qty)
    return db.session.execute(select(Location,amount).join(Movement,Movement.location_id==Location.id)
        .where(Movement.item_id==item_id).group_by(Location.id).having(amount!=0).order_by(Location.name)).all()


def receipt_locations(item_id):
    """Active physical locations with current stock or real movement history."""
    return db.session.execute(select(Location, func.sum(Movement.qty))
        .join(Movement, Movement.location_id == Location.id)
        .where(Movement.item_id == item_id, Location.id != UNASSIGNED_ID, Location.active.is_(True))
        .group_by(Location.id)).all()


def location_balances(location_id):
    amount=func.sum(Movement.qty)
    return db.session.execute(select(Item,amount).join(Movement,Movement.item_id==Item.id)
        .where(Movement.location_id==location_id).group_by(Item.id).having(amount!=0).order_by(Item.name)).all()


def rate(item):
    """Weighted historical cost per stock unit, excluding explicitly reversed receipts."""
    reverse=aliased(Transaction)
    kinds=("production_output","opening") if item.kind=="manufactured" else ("receipt","opening")
    qty,cost = db.session.execute(select(func.sum(Movement.qty),func.sum(Movement.cost))
        .join(Transaction,Transaction.id==Movement.transaction_id).where(
            Movement.item_id==item.id,Movement.kind.in_(kinds),Movement.qty>0,Movement.cost.is_not(None),
            ~select(reverse.id).where(reverse.reverses_id==Transaction.id).exists())).one()
    if qty:
        return Decimal(cost)/qty
    if item.pack_price is not None and item.pack_qty:
        return Decimal(item.pack_price)/converted(item.pack_qty,item.pack_unit,item.unit)
    return None


def value_at(qty, unit_rate):
    if unit_rate is None:
        return None
    value=int((qty*unit_rate).to_integral_value(rounding=ROUND_HALF_UP))
    if not 0<=value<=MAX_COST:
        raise ValueError("Calculated cost is outside the supported range.")
    return value


def recipe_estimate(item):
    if not item.materials or not item.yield_qty:
        return None
    with localcontext() as ctx:
        ctx.prec = 40
        batch = Decimal(0)
        for line in item.materials:
            basis = rate(line.material)
            if basis is None:
                return None
            batch += converted(line.qty, line.unit, line.material.unit) * basis
        return batch / (item.yield_qty * SCALE)


def _id(raw,label):
    if not re.fullmatch(r"[1-9][0-9]{0,12}",str(raw or "")):
        raise ValueError(f"Choose {label}.")
    return int(raw)


def item_for(raw, *, active=True):
    row=db.session.get(Item,_id(raw,"an item"))
    if not row or active and not row.active:
        raise ValueError("Choose an active inventory item.")
    return row


def location_for(raw, *, active=True):
    row=db.session.get(Location,_id(raw,"a location"))
    if not row or active and not row.active:
        raise ValueError("Choose an active storage location.")
    return row


def unassigned_location():
    row = db.session.get(Location, UNASSIGNED_ID)
    if not row or not row.active or row.identity.kind != "system":
        raise ValueError("The built-in Unassigned stock destination is not available.")
    return row


def scan_verified_location(data):
    loc = location_for(data.get("destination"))
    if str(loc.id) != data.get("verified_destination"):
        raise ValueError("Scan the physical destination’s barcode before reviewing this transaction.")
    return loc


def _check_version(row, data):
    if row and str(row.version)!=data.get("version"):
        raise ValueError("This record changed in another session. Reload it before making edits.")


def _archive_allowed(row, dependent):
    if row.active and dependent:
        raise ValueError("Move counted stock out and remove active dependencies before archiving this record.")


def save_item(kind, data, *, row=None, requirements=(), image=None, remove_image=False):
    """Caller holds the writer lock and owns commit/rollback."""
    if kind not in KINDS:
        raise ValueError("Choose a valid item type.")
    _check_version(row,data)
    active=data.get("active")=="yes"
    if row and not active:
        referenced=db.session.scalar(select(RecipeMaterial.id).join(Item,Item.id==RecipeMaterial.component_id)
            .where(RecipeMaterial.material_id==row.id,Item.active.is_(True)).limit(1))
        _archive_allowed(row,balance(row.id)!=0 or referenced)
    unit = row.unit if row else ("piece" if kind=="manufactured" else data.get("unit"))
    if unit not in UNITS:
        raise ValueError("Choose a valid stock unit.")
    ext=clean(data.get("supplier_url"),"Supplier URL",500)
    try:
        endpoint=urlsplit(ext)
        if ext and (endpoint.scheme not in {"http","https"} or not endpoint.hostname or endpoint.username or endpoint.password
                    or any(c.isspace() or ord(c)<32 for c in ext) or "\\" in ext):
            raise ValueError()
    except ValueError:
        raise ValueError("Enter an http:// or https:// supplier URL without credentials.") from None
    values={k:clean(data.get(k),label,size,required=k=="name") for k,label,size in (
        ("name","Name",160),("category","Category",100),("description","Description",4000),
        ("specification","Brand or specification",250),("supplier","Supplier",160),("supplier_sku","Supplier SKU",100),
        ("filament_type","Filament type",100),("filament_colour","Filament colour",100))}
    values.update(unit=unit,active=active,supplier_url=ext,minimum=converted(number(data.get("minimum") or "0","Minimum stock"),unit,unit),
                  pack_qty=None,pack_unit=None,pack_price=None,yield_qty=None)
    for key,label in (("spool_grams","Spool weight (g)"),("sheet_length","Sheet length"),
                      ("sheet_width","Sheet width"),("sheet_thickness","Sheet thickness"),
                      ("reorder","Reorder target")):
        values[key]=number(data.get(key),label,optional=True,positive=key!="reorder")
    if values["reorder"] is not None:
        values["reorder"]=converted(values["reorder"],unit,unit)
        if values["reorder"]<values["minimum"]:
            raise ValueError("Reorder target cannot be less than minimum stock.")
    dimensions=[values[k] for k in ("sheet_length","sheet_width","sheet_thickness")]
    if any(dimensions) and (unit!="sheet" or not all(dimensions)):
        raise ValueError("Sheet dimensions require the sheet stock unit and all three dimensions.")
    recipe={}
    if kind=="manufactured":
        value=converted(number(data.get("yield_qty"),"Expected good components per batch",positive=True),"piece","piece")
        values["yield_qty"]=value//SCALE
        if not 1<=len(requirements)<=30:
            raise ValueError("Configure between 1 and 30 distinct source materials.")
        for mid,raw,usage_unit in requirements:
            material=item_for(mid,active=active)
            if material.kind!="material" or material.id in recipe:
                raise ValueError("Each recipe line must be a distinct raw material.")
            used=number(raw,"Expected material consumption",positive=True)
            converted(used,usage_unit,material.unit)
            recipe[material.id]=(used,usage_unit)
    else:
        pack=number(data.get("pack_qty"),"Purchase pack quantity",optional=True,positive=True)
        price=cost_number(data.get("pack_price"),"Purchase pack price")
        if pack is None and price is not None:
            raise ValueError("Enter a purchase pack quantity before setting its price.")
        if pack is not None:
            pack_unit=data.get("pack_unit") or unit
            converted(pack,pack_unit,unit)
            values.update(pack_qty=pack,pack_unit=pack_unit,pack_price=price)
    if row is None:
        row=Item(identity=_new_identity(data.get("code"),"item"),kind=kind)
        db.session.add(row)
    else:
        row.version+=1
    for k,v in values.items():
        setattr(row,k,v)
    if image is not None or remove_image:
        row.image=image
    current={r.material_id:r for r in row.materials}
    row.materials[:]=[current[mid] if mid in current else RecipeMaterial(material_id=mid) for mid in recipe]
    for requirement in row.materials:
        requirement.qty,requirement.unit=recipe[requirement.material_id]
    db.session.flush()
    return row


def save_location(data, *, row=None):
    _check_version(row,data)
    active=data.get("active")=="yes"
    parent=location_for(data["parent_id"],active=active) if data.get("parent_id") else None
    ancestor=parent
    while ancestor:
        if row and ancestor.id==row.id:
            raise ValueError("A location cannot be inside itself or one of its descendants.")
        ancestor=ancestor.parent
    if row and not active:
        child=db.session.scalar(select(Location.id).where(Location.parent_id==row.id,Location.active.is_(True)).limit(1))
        _archive_allowed(row,location_balances(row.id) or child)
    if row is None:
        row=Location(identity=_new_identity(data.get("code"),"location"))
        db.session.add(row)
    else:
        row.version+=1
    row.name=clean(data.get("name"),"Location name",160,required=True)
    row.description=clean(data.get("description"),"Description",4000)
    row.parent=parent
    row.active=active
    db.session.flush()
    return row


def _movement(item, loc, qty, kind, cost=None, **extra):
    return dict(item_id=item.id,location_id=loc.id,item=item.name,location=loc.name,
                qty=qty,unit=item.unit,kind=kind,cost=cost,**extra)


def _checked(moves):
    by_location=defaultdict(int)
    totals=defaultdict(int)
    for move in moves:
        key=(move["item_id"],move["location_id"])
        allowed=balance(*key)+by_location[key]
        move["before"]=allowed
        move["after"]=allowed+move["qty"]
        if move["after"]<0:
            raise ValueError(f"Not enough {move['item']} in {move['location']}: {quantity_text(allowed)} {move['unit']} available.")
        if move["after"]>MAX_QTY:
            raise ValueError("This movement exceeds the supported stock limit.")
        by_location[key]+=move["qty"]
        totals[move["item_id"]]+=move["qty"]
    if any(balance(item_id)+qty>MAX_QTY for item_id,qty in totals.items()):
        raise ValueError("This movement exceeds the supported total stock limit.")


def plan(kind, data):
    """Read-only preview. Recomputed under the SQLite write lock on confirmation."""
    if kind not in STEPS:
        raise ValueError("Unknown stock operation.")
    why=clean(data.get("reason"),"Reason or reference",400,required=kind in {"reversal","opening"})
    moves,details,total,original=[],{},None,None
    if kind=="reversal":
        key=data.get("original","")
        original=db.session.get(Transaction,key) if re.fullmatch(r"[a-f0-9]{32}",key) else None
        if not original or original.kind=="reversal" or not original.moves or db.session.scalar(
                select(Transaction.id).where(Transaction.reverses_id==original.id)):
            raise ValueError("That transaction cannot be reversed or already has a reversal.")
        item=original.item
        for old in original.moves:
            # A reversal that would restore stock into a retired record needs
            # the operator to reactivate that exact identity first.
            if not old.item.active or not old.location.active:
                raise ValueError("Reactivate all affected items and locations before reversing.")
            moves.append(_movement(old.item,old.location,-old.qty,"reversal",old.cost))
        total=original.cost
    else:
        item=item_for(data.get("item_id"))
        if kind in {"receive","opening"}:
            if kind=="receive" and item.kind=="manufactured":
                raise ValueError("Use Complete Production for a manufactured component.")
            if kind == "receive":
                if data.get("unassigned") == "yes":
                    if data.get("destination") or data.get("verified_destination"):
                        raise ValueError("Clear the physical destination when keeping stock Unassigned.")
                    loc = unassigned_location()
                else:
                    loc = scan_verified_location(data)
            else:
                loc = location_for(data.get("destination"))
            qty=number(data.get("qty"),"Quantity",positive=True)
            if kind=="opening" and db.session.scalar(select(Movement.id).where(
                    Movement.item_id==item.id,Movement.location_id==loc.id).limit(1)):
                raise ValueError("Stock was already recorded here. Use Receive Stock for deliveries or Stock Check to correct it.")
            if data.get("entry_unit")=="packs" and kind=="receive":
                if not item.pack_qty:
                    raise ValueError("Configure the purchase pack quantity first, or receive in stock units.")
                amount,remainder=divmod(qty*converted(item.pack_qty,item.pack_unit,item.unit),SCALE)
                if remainder:
                    raise ValueError("That number of packs is below stock precision.")
                details={"packs":qty,"pack_qty":item.pack_qty,"pack_unit":item.pack_unit}
                qty=amount
            qty=converted(qty,item.unit,item.unit)
            total=cost_number(data.get("cost"))
            if total is None and item.pack_price is not None and item.pack_qty:
                total=value_at(qty,Decimal(item.pack_price)/converted(item.pack_qty,item.pack_unit,item.unit))
                details["cost_basis"]="Configured purchase pack estimate"
            elif total is not None:
                details["cost_basis"]="Recorded total cost"
            if kind == "receive":
                details["placement"] = "explicit_unassigned" if loc.id == UNASSIGNED_ID else "barcode_verified"
            moves.append(_movement(item,loc,qty,"receipt" if kind=="receive" else "opening",total))
        elif kind=="move":
            if data.get("source") == UNASSIGNED:
                source, destination = unassigned_location(), scan_verified_location(data)
                details["placement"] = "barcode_verified"
            else:
                source = location_for(data.get("source"))
                if data.get("destination") == UNASSIGNED:
                    destination = unassigned_location()
                    details["placement"] = "explicit_unassigned"
                else:
                    destination = location_for(data.get("destination"))
            if source.id==destination.id:
                raise ValueError("Choose two different locations.")
            qty=converted(number(data.get("qty"),"Quantity",positive=True),item.unit,item.unit)
            moves=[_movement(item,source,-qty,"transfer"),_movement(item,destination,qty,"transfer")]
        elif kind=="count":
            loc=location_for(data.get("destination"))
            physical=converted(number(data.get("qty"),"Physical count"),item.unit,item.unit)
            expected=balance(item.id,loc.id)
            difference=physical-expected
            details={"expected":expected,"physical":physical,"difference":difference,"location_id":loc.id,"location":loc.name}
            if difference:
                why=clean(why,"Reason for the difference",400,required=True)
                moves.append(_movement(item,loc,difference,"adjustment"))
        else:
            if item.kind!="manufactured" or not item.yield_qty or not item.materials:
                raise ValueError("Configure a manufactured component with an expected yield and raw-material recipe first.")
            destination=location_for(data.get("destination"))
            good=converted(number(data.get("qty"),"Good components produced",positive=True),"piece","piece")
            with localcontext() as ctx:
                ctx.prec=40
                total=0
                for r in item.materials:
                    material=r.material
                    if not material.active or material.kind!="material":
                        raise ValueError(f"Recipe material {material.name} is not an active raw material.")
                    source=location_for(data.get(f"source_{material.id}"))
                    actual=converted(number(data.get(f"actual_{material.id}"),f"Actual {material.name} used",positive=True),r.unit,material.unit)
                    basis=rate(material)
                    if basis is None:
                        raise ValueError(f"Record a cost for {material.name} on a purchase pack, receipt or opening stock before production.")
                    cost=value_at(actual,basis)
                    total+=cost
                    estimated=Decimal(r.qty)*good/(item.yield_qty*SCALE)
                    moves.append(_movement(material,source,-actual,"production_consumption",cost,
                        est_qty=str((estimated/SCALE).quantize(Decimal(".000001"),rounding=ROUND_HALF_UP)),est_unit=r.unit,
                        actual=str(data.get(f"actual_{material.id}")).strip(), actual_unit=r.unit))
                if total>MAX_COST:
                    raise ValueError("Actual production cost exceeds the supported range.")
                moves.append(_movement(item,destination,good,"production_output",total))
                details={"good_pieces":good//SCALE,"unit_cost":str(Decimal(total)/(good//SCALE)),
                         "expected_yield":item.yield_qty}
    _checked(moves)
    return dict(kind=kind,item_id=item.id,item=item.name,unit=item.unit,moves=moves,details=details,reason=why,
                cost=total,reverses_id=original.id if original else None)


def digest(value):
    return sha256(json.dumps(value,sort_keys=True,separators=(",",":")).encode()).hexdigest()


def post(kind, data, request_id, expected, *, actor, actor_label=None):
    """Atomically validate and append. An identical retry returns the original receipt."""
    if not re.fullmatch(r"[a-f0-9]{32}",request_id):
        raise ValueError("Missing stock operation reference. Start a new preview.")
    writer_lock()
    existing=db.session.get(Transaction,request_id)
    if existing:
        if existing.request_hash!=expected or existing.actor!=actor:
            raise ValueError("This request ID has already been used by another stock transaction.")
        return existing
    change=plan(kind,data)
    if digest(change)!=expected:
        raise ValueError("Stock, recipe or costs changed after preview. Build a fresh preview before confirming.")
    tx=Transaction(id=request_id,kind=kind,item_id=change["item_id"],actor=actor,actor_label=(actor_label or actor)[:160],
        created_at=datetime.now(UTC).replace(tzinfo=None),reason=change["reason"],cost=change["cost"],
        details=json.dumps(change["details"]),request_hash=expected,reverses_id=change["reverses_id"])
    tx.moves=[Movement(**{k:m[k] for k in ("item_id","location_id","qty","kind","unit","cost")}) for m in change["moves"]]
    db.session.add(tx)
    db.session.flush()
    return tx
