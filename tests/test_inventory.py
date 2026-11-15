import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from io import BytesIO
import re
import sqlite3
from threading import Barrier
from uuid import uuid4
from xml.etree import ElementTree

import pytest
from alembic import command
from PIL import Image
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from werkzeug.datastructures import MultiDict

from app import db
from app.database import _alembic_config, ensure_database
from app.inventory import service as stock
from app.inventory.models import Identity, Item, Location, Movement, Transaction
from app.models import User
from test_setup_flow import setup_app  # noqa: F401 - isolated migrated application fixture


@pytest.fixture
def workshop(setup_app):
    app, _ = setup_app
    with app.app_context():
        db.session.add_all([User(email=f"{name}@example.invalid", username=name, password="unused", is_admin=admin)
                            for name, admin in (("operator", True), ("operator-two", True), ("viewer", False))])
        db.session.commit()
    return app


def client_for(app, user=1):
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(user)
        session["_fresh"] = True
    return client


def row_id(response):
    assert response.status_code == 303, response.get_data(as_text=True)[:500]
    return int(response.headers["Location"].split("/")[-1])


def open_form(client, kind, item_id=None, original=None):
    form = client.get(f"/production/stock/{kind}",
                      query_string={k:v for k,v in (("item_id", item_id), ("original", original)) if v is not None})
    assert form.status_code == 200
    return re.search(rb'name="request_id" value="([a-f0-9]{32})"', form.data).group(1).decode()


def review(client, kind, item_id=None, nonce=None, **values):
    nonce = nonce or open_form(client, kind, item_id, values.get("original"))
    if kind == "receive" and values.get("destination") and "location_proof" not in values:
        with client.application.app_context():
            location_code = db.session.get(Location, int(values["destination"])).identity.code
        values["location_proof"] = scan_location(client, kind, item_id, nonce, location_code).json["proof"]
    form = client.post(f"/production/stock/{kind}", data=dict(
        values, item_id=item_id or "", request_id=nonce))
    match = re.search(rb'name="approval" value="([^\"]+)"', form.data)
    return form, match.group(1).decode() if match else None


def scan_location(client, kind, item_id, nonce, code):
    return client.post(f"/production/stock/{kind}/scan-location", data={"item_id":item_id, "request_id":nonce, "code":code})


def approve(client, kind, approval):
    return client.post(f"/production/stock/{kind}", data={"approval":approval})


def transact(client, kind, item_id=None, **values):
    page, token = review(client, kind, item_id, **values)
    assert page.status_code == 200 and token
    receipt = approve(client, kind, token)
    assert receipt.status_code == 303
    return receipt.headers["Location"]


def location(client, code, name, **values):
    return row_id(client.post("/production/location/new",data=dict(values,name=name,code=code,active="yes")))


def item(client, kind, code, name, **values):
    return row_id(client.post(f"/production/item/new/{kind}",data=dict(dict(unit="piece"), **values)|
                              dict(name=name,code=code,active="yes")))


def manufactured(client, requirements):
    fields = MultiDict(dict(name="Printed body",code="BODY",yield_qty="4",active="yes"))
    for material_id, amount, unit in requirements:
        fields.add("material_id",material_id)
        fields.add("material_qty",amount)
        fields.add("material_unit",unit)
    return row_id(client.post("/production/item/new/manufactured",data=fields))


def test_admin_only_local_pages_csrf_and_navigation(workshop):
    client=client_for(workshop)
    for path in ("/production/", "/production/items/materials", "/production/items/components",
                 "/production/locations", "/production/lookup", "/production/stock/receive", "/production/history"):
        assert workshop.test_client().get(path).status_code in {302,401}
        assert client_for(workshop,3).get(path).status_code == 403
        response=client.get(path)
        assert response.status_code == 200
        assert "no-store" in response.headers["Cache-Control"]
    home=client.get("/production/")
    assert b"Production &amp; Operations" in home.data and b"Complete Production" in home.data
    assert b"/static/assets/css/inventory.css" in home.data
    css=client.get("/static/assets/css/inventory.css").data
    assert b"@media (max-width: 680px)" in css and b"min-height: 48px" in css
    script=client.get("/static/assets/js/inventory.mjs")
    assert script.status_code == 200 and "javascript" in script.mimetype
    workshop.config["WTF_CSRF_ENABLED"]=True
    assert client.post("/production/location/new",data={"name":"Shelf"}).status_code == 400
    form=client.get("/production/location/new")
    csrf=re.search(rb'name="csrf_token" value="([^\"]+)"',form.data).group(1).decode()
    assert client.post("/production/location/new",data={"name":"Shelf","code":"SHELF","active":"yes","csrf_token":csrf}).status_code == 303


def test_item_management_pack_specs_image_location_hierarchy_and_permanent_codes(workshop):
    client=client_for(workshop)
    shelf=location(client,"RACK-A","Rack A")
    bin_id=location(client,"RACK-A-01","Bin 01",parent_id=shelf)
    wood=item(client,"material","wood","Birch plywood",unit="sheet",category="Wood",specification="Laser grade",
              sheet_length="600",sheet_width="400",sheet_thickness="3",pack_qty="5",pack_price="12.50",
              supplier="Fixture Supplier",supplier_sku="SHEET-5",supplier_url="https://example.invalid/wood",
              minimum="2",reorder="10")
    switches=item(client,"purchased","SW-1","Switches",pack_qty="50",pack_price="4.99")
    assert b"Birch plywood" in client.get("/production/items/materials?q=sheet-5").data
    assert b"Switches" in client.get("/production/items/components?kind=purchased").data
    assert b"Rack A / Bin 01" in client.get(f"/production/location/{bin_id}").data
    assert client.get("/production/lookup?code=rack-a-01").location.endswith(f"/location/{bin_id}")
    assert client.get("/production/lookup?code=wood").location.endswith(f"/item/{wood}")
    assert b"No item or location" in client.get("/production/lookup?code=BAD-42").data
    assert client.get("/production/lookup?code=%2Fbin").status_code == 400
    assert b"already assigned" in client.post("/production/location/new",data={"name":"Duplicate","code":"WoOd","active":"yes"}).data
    assert b"descendants" in client.post(f"/production/location/{shelf}/edit",data={"name":"Rack A","version":"1","parent_id":bin_id,"active":"yes"}).data
    assert client.post(f"/production/location/{shelf}/edit",data={"name":"Rack A","version":"1"}).status_code == 400
    png=BytesIO()
    Image.new("RGBA",(800,300),(0,0,0,0)).save(png,"PNG")
    png.seek(0)
    saved=client.post(f"/production/item/{wood}/edit",data={"name":"Birch plywood revised","version":"1","unit":"g",
        "code":"TRY-CHANGE","active":"yes","pack_qty":"5","pack_price":"12.50","sheet_length":"600","sheet_width":"400",
        "sheet_thickness":"3","image":(png,"picture.png")})
    assert saved.status_code == 303
    image=client.get(f"/production/item/{wood}/image.jpg")
    assert image.mimetype == "image/jpeg" and Image.open(BytesIO(image.data)).width == 640
    assert b"600 \xc3\x97 400 \xc3\x97 3 mm" in client.get(saved.location).data
    assert client.post(f"/production/item/{wood}/edit",data={"name":"Old edit","version":"1","active":"yes"}).status_code == 400
    assert client.post(f"/production/item/{switches}/edit",data={"name":"Old stock code","version":"1"}).status_code == 303
    assert b"Old stock code" in client.get("/production/items/components?state=archived").data
    assert b"already assigned" in client.post("/production/item/new/purchased",data={"name":"Reused code","unit":"piece","active":"yes","code":"SW-1"}).data
    with workshop.app_context():
        record=db.session.get(Item,wood)
        assert (record.unit,record.identity.code,stock.rate(record),stock.balance(wood)) == ("sheet","WOOD",Decimal("2.50"),0)
        assert db.session.scalar(select(func.count(Movement.id))) == 0


def test_item_barcode_scanner_present_on_new_and_existing_editors(workshop):
    client = client_for(workshop)
    raw = item(client, "material", "RAW", "Filament")
    purchased = item(client, "purchased", "SW", "Switches")
    made = manufactured(client, [(raw, "1", "piece")])

    for kind, record_id in (("material", raw), ("purchased", purchased), ("manufactured", made)):
        new = client.get(f"/production/item/new/{kind}")
        edit = client.get(f"/production/item/{record_id}/edit")
        for page in (new, edit):
            assert page.status_code == 200
            assert b'data-editor-scan="/production/item/barcode-check"' in page.data
            assert b'type="button" data-scan-open hidden>Scan Barcode' in page.data
            assert b'type="button" data-scan-stop hidden>Cancel scan' in page.data
        new_input = re.search(rb'<input[^>]*id="inventory-item-barcode"[^>]*>', new.data).group()
        edit_input = re.search(rb'<input[^>]*id="inventory-item-barcode"[^>]*>', edit.data).group()
        assert b'name="code"' in new_input and b'readonly' not in new_input
        assert b'readonly' in edit_input and b'name=' not in edit_input


def test_item_barcode_check_is_read_only_and_creates_only_on_save(workshop):
    client = client_for(workshop)
    check = "/production/item/barcode-check?code="
    assert client_for(workshop, 3).get(check + "00042").status_code == 403
    assert client.get(check + "%2Fbin").status_code == 400
    free = client.get(check + "00042")
    assert free.json == {"available": True}
    assert "no-store" in free.headers["Cache-Control"]
    with workshop.app_context():
        assert db.session.scalar(select(func.count(Identity.id)).where(Identity.id > 0)) == 0
        assert db.session.scalar(select(func.count(Movement.id))) == 0

    raw = item(client, "material", "00042", "Filament")
    switches = item(client, "purchased", "SW", "Switches")
    rack = location(client, "RACK", "Rack")
    comp = manufactured(client, [(raw, "1", "piece")])
    for code, owner in (("00042", "Filament"), ("SW", "Switches"), ("RACK", "Rack"), ("BODY", "Printed body")):
        duplicate = client.get(check + code)
        assert duplicate.status_code == 409
        assert owner in duplicate.json["error"]
        failed = client.post("/production/item/new/purchased", data={"name":"Duplicate","unit":"piece","active":"yes","code":code})
        assert failed.status_code == 400 and b"already assigned" in failed.data and owner.encode() in failed.data

    assert client.get("/production/lookup?code=00042").location.endswith(f"/item/{raw}")
    assert f'value="{raw}" data-code="00042"'.encode() in client.get("/production/stock/receive").data
    transact(client, "receive", raw, destination=rack, qty="2")
    edited = client.post(f"/production/item/{raw}/edit", data={"name":"Filament renamed","version":"1","active":"yes","code":"NEW"})
    assert row_id(edited) == raw
    with workshop.app_context():
        assert db.session.get(Item, raw).identity.code == "00042"
        assert db.session.get(Item, comp).materials[0].material_id == raw
        assert stock.balance(raw, rack) == 2 * stock.SCALE
        assert db.session.scalar(select(func.count(Transaction.id))) == 1
        assert db.session.scalar(select(func.count(Identity.id)).where(Identity.id > 0)) == 4


def test_receiving_suggests_deduplicated_physical_stock_and_real_history(workshop):
    client = client_for(workshop)
    raw = item(client, "material", "PLA", "Filament", unit="g")
    a = location(client, "A", "Shelf A")
    b = location(client, "B", "Shelf B")
    c = location(client, "C", "Earlier drawer")
    location(client, "UNUSED", "Never used")
    assert b"No physical locations have been used" in client.get(f"/production/stock/receive?item_id={raw}").data
    transact(client, "receive", raw, destination=a, qty="200")
    first = client.get(f"/production/stock/receive?item_id={raw}").data
    assert first.count(b'data-location-label="Shelf A"') == 1 and b"200 g on hand" in first
    transact(client, "receive", raw, destination=a, qty="125")
    transact(client, "receive", raw, destination=c, qty="70")
    transact(client, "move", raw, source=c, destination=b, qty="70")
    transact(client, "receive", raw, unassigned="yes", qty="50")
    page = client.get(f"/production/stock/receive?item_id={raw}").data
    assert page.count(b'data-location-label="Shelf A"') == 1 and b"325 g on hand" in page
    assert b"70 g on hand" in page and b"Previously used" in page
    assert page.index(b'data-location-label="Shelf B"') < page.index(b'data-location-label="Earlier drawer"')
    assert b'data-location-label="Never used"' not in page and b'data-location-label="Unassigned' not in page
    assert b'name="unassigned" value="yes" data-keep-unassigned checked' not in page


def test_receipt_requires_matching_scan_bound_to_item_form_and_operator(workshop):
    client = client_for(workshop)
    other_operator = client_for(workshop, 2)
    raw = item(client, "material", "00012345678905", "Filament", unit="g")
    other_item = item(client, "purchased", "LED", "LED")
    a = location(client, "A", "Shelf A")
    b = location(client, "B", "New shelf")
    nonce = open_form(client, "receive", raw)
    scan = scan_location(client, "receive", raw, nonce, "B")
    assert scan.status_code == 200 and scan.json["destination"] == str(b)
    proof = scan.json["proof"]
    item_scan = scan_location(client, "receive", raw, nonce, "00012345678905")
    assert item_scan.status_code == 400 and b"item barcode" in item_scan.data and "proof" not in item_scan.json
    assert b"Unknown location barcode" in scan_location(client, "receive", raw, nonce, "UNKNOWN").data
    assert scan_location(client_for(workshop, 3), "receive", raw, nonce, "B").status_code == 403
    for operator, item_id, form_id, selected, evidence in (
        (client, raw, nonce, b, ""), (client, raw, nonce, b, "forged"), (client, raw, nonce, a, proof),
        (client, other_item, nonce, b, proof), (client, raw, open_form(client, "receive", raw), b, proof),
        (other_operator, raw, nonce, b, proof),
    ):
        response, approval = review(operator, "receive", item_id, nonce=form_id, destination=selected, qty="100",
                                    location_proof=evidence, verified_destination=str(selected))
        assert response.status_code == 400 and approval is None
        assert b"Scan the selected physical destination" in response.data
    preview, approval = review(client, "receive", raw, nonce=nonce, destination=b, qty="100", location_proof=proof)
    assert b"Physical destination verified" in preview.data and b"Confirm receipt" in preview.data
    with workshop.app_context():
        assert stock.balance(raw) == 0
    receipt = approve(client, "receive", approval)
    assert receipt.status_code == 303 and approve(client, "receive", approval).location == receipt.location
    with workshop.app_context():
        assert stock.balance(raw, b) == 100 * stock.SCALE
        assert db.session.scalar(select(func.count(Transaction.id))) == 1
    # A matched scan cannot commit if its destination is retired after preview.
    _, old_approval = review(client, "receive", raw, destination=a, qty="10")
    assert client.post(f"/production/location/{a}/edit", data={"name":"Shelf A","version":"1"}).status_code == 303
    blocked = approve(client, "receive", old_approval)
    assert blocked.status_code == 409 and b"active storage location" in blocked.data


def test_unassigned_receipt_without_locations_and_partial_verified_allocation(workshop):
    client = client_for(workshop)
    raw = item(client, "material", "PLA", "Filament", unit="g")
    assert client.get("/production/location/0").status_code == 404
    assert client.get("/production/label/0/qr.svg").status_code == 404
    assert b"Unassigned" not in client.get("/production/locations").data
    invalid, _ = review(client, "receive", raw, qty="200", unassigned="off")
    assert invalid.status_code == 400
    received = transact(client, "receive", raw, qty="200", unassigned="yes")
    assert b"Explicitly kept Unassigned" in client.get(received).data
    assert b"Unassigned" in client.get(f"/production/item/{raw}").data
    shelf = location(client, "A", "Shelf A")
    nonce = open_form(client, "move", raw)
    proof = scan_location(client, "move", raw, nonce, "A").json["proof"]
    rejected, _ = review(client, "move", raw, nonce=nonce, source="unassigned", destination=shelf, qty="100")
    assert rejected.status_code == 400 and b"Scan the selected physical destination" in rejected.data
    overdraw, _ = review(client, "move", raw, nonce=nonce, source="unassigned", destination=shelf, qty="201", location_proof=proof)
    assert overdraw.status_code == 400 and b"Not enough Filament in Unassigned" in overdraw.data
    _, approved = review(client, "move", raw, nonce=nonce, source="unassigned", destination=shelf, qty="100", location_proof=proof)
    moved = approve(client, "move", approved)
    assert moved.status_code == 303
    assert b"verified by barcode" in client.get(moved.location).data
    with workshop.app_context():
        assert stock.balance(raw, stock.UNASSIGNED_ID) == 100 * stock.SCALE
        assert stock.balance(raw, shelf) == 100 * stock.SCALE
        assert stock.balance(raw) == 200 * stock.SCALE
        receipt_id = received.rsplit("/", 1)[1]
        assert db.session.get(Transaction, receipt_id).moves[0].location_id == stock.UNASSIGNED_ID


def test_manual_unassigned_location_can_be_explicitly_transferred_without_merging_history(workshop):
    client = client_for(workshop)
    legacy = location(client, "MANUAL", "Unassigned")
    raw = item(client, "material", "PLA", "Filament", unit="g")
    receipt = transact(client, "receive", raw, destination=legacy, qty="100")
    assert b"separate from the built-in" in client.get(f"/production/location/{legacy}").data
    assert client.get("/production/lookup?code=MANUAL").location.endswith(f"/location/{legacy}")
    moved = transact(client, "move", raw, source=legacy, destination="unassigned", qty="40", reason="Reconcile old staging stock")
    assert b"Explicitly kept Unassigned" in client.get(moved).data
    with workshop.app_context():
        assert stock.balance(raw, legacy) == 60 * stock.SCALE
        assert stock.balance(raw, stock.UNASSIGNED_ID) == 40 * stock.SCALE
        assert stock.balance(raw) == 100 * stock.SCALE
        assert db.session.get(Transaction, receipt.rsplit("/", 1)[1]).moves[0].location_id == legacy


@pytest.mark.parametrize("has_history", [False, True])
def test_unassigned_upgrade_keeps_manual_location_and_all_existing_rows(tmp_path, has_history):
    database = tmp_path / "inventory-m1.db"
    url = f"sqlite:///{database}"
    command.upgrade(_alembic_config(url), "0022_inventory_foundation")
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO inventory_identity VALUES (1, 'MANUAL', 'location')")
        connection.execute("INSERT INTO inventory_location VALUES (1, 'Unassigned', 'Manually created', NULL, 1, 1)")
        if has_history:
            connection.execute("INSERT INTO inventory_identity VALUES (2, 'PLA', 'item')")
            connection.execute("""INSERT INTO inventory_item
                (id,kind,name,category,description,specification,filament_type,filament_colour,unit,supplier,supplier_sku,supplier_url,minimum,active,version)
                VALUES (2,'material','Filament','','','','','','g','','','',0,1,1)""")
            connection.execute("""INSERT INTO inventory_transaction
                (id,kind,item_id,actor,actor_label,created_at,reason,details,request_hash)
                VALUES ('prior','receive',2,'1','Operator','2026-01-01 00:00','Original','{}','original-hash')""")
            connection.execute("""INSERT INTO inventory_movement (id,transaction_id,item_id,location_id,qty,unit,kind)
                VALUES (3,'prior',2,1,120000000,'g','receipt')""")
        old_rows = {table: connection.execute(f"SELECT * FROM inventory_{table} ORDER BY id").fetchall()
                    for table in ("identity", "location", "item", "transaction", "movement")}
    report = ensure_database(url)
    assert report.revision == "0023_unassigned_stock" and report.backup_path.is_file()
    with sqlite3.connect(database) as connection:
        for table, rows in old_rows.items():
            assert connection.execute(f"SELECT * FROM inventory_{table} WHERE id != 0 ORDER BY id").fetchall() == rows
        assert connection.execute("SELECT code, kind FROM inventory_identity WHERE id=0").fetchone() == ("~UNASSIGNED", "system")
        assert connection.execute("SELECT COUNT(*) FROM inventory_movement WHERE location_id=0").fetchone() == (0,)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_receiving_opening_counts_transfers_idempotence_and_correction(workshop):
    client=client_for(workshop)
    rack=location(client,"RACK","Rack")
    drawer=location(client,"DRAWER","Drawer")
    switches=item(client,"purchased","SW-1","Switches",pack_qty="50",pack_unit="piece",pack_price="19.99")
    opening=transact(client,"opening",switches,destination=rack,qty="8",reason="Migration count",cost="2.50")
    assert b"Stock was already recorded here" in review(client,"opening",switches,destination=rack,qty="8",reason="Double count")[0].data
    page, approval=review(client,"receive",switches,destination=rack,qty="1",entry_unit="packs",reason="Invoice 42")
    assert b"Stock: 8 \xe2\x86\x92 58 piece" in page.data and b"19.99" in page.data
    assert approve(client_for(workshop,2),"receive",approval).status_code == 409
    received=approve(client,"receive",approval)
    assert received.status_code == 303
    assert approve(client,"receive",approval).location == received.location
    assert b"Not enough" in review(client,"move",switches,source=rack,destination=drawer,qty="59")[0].data
    transfer=transact(client,"move",switches,source=rack,destination=drawer,qty="10")
    assert b"Reason for the difference" in review(client,"count",switches,destination=drawer,qty="9")[0].data
    count=transact(client,"count",switches,destination=drawer,qty="9",reason="One component damaged")
    matching=transact(client,"count",switches,destination=drawer,qty="9")
    assert b"No stock moved" in client.get(matching).data
    assert b"10 / 9" in client.get(count).data
    with workshop.app_context():
        assert stock.balance(switches,rack)==48*stock.SCALE
        assert stock.balance(switches,drawer)==9*stock.SCALE
        assert stock.balance(switches)==57*stock.SCALE
        assert stock.rate(db.session.get(Item,switches))==Decimal("22.49")/58
        assert db.session.scalar(select(func.count(Transaction.id)))==5
    assert b"Not enough" in review(client,"reversal",original=transfer.rsplit("/",1)[1],reason="Undo")[0].data
    reversal=transact(client,"reversal",original=count.rsplit("/",1)[1],reason="Component found")
    assert b"Original transaction" in client.get(reversal).data
    assert b"Correction receipt" in client.get(count).data
    assert b"already has a reversal" in review(client,"reversal",original=count.rsplit("/",1)[1],reason="Again")[0].data
    assert b"Switches" in client.get(f"/production/location/{drawer}").data
    assert b"Invoice 42" in client.get(f"/production/history?item_id={switches}&kind=receive").data
    assert b"Migration count" in client.get(opening).data
    with workshop.app_context():
        assert stock.balance(switches)==58*stock.SCALE
        assert stock.balance(switches,drawer)==10*stock.SCALE
        for stmt in ("UPDATE inventory_movement SET qty=2", "DELETE FROM inventory_transaction", "UPDATE inventory_identity SET code='OTHER'"):
            with pytest.raises(IntegrityError, match="immutable"):
                db.session.execute(text(stmt))
            db.session.rollback()


def test_multi_material_production_actual_cost_and_atomic_rollback(workshop):
    client=client_for(workshop)
    draw=location(client,"DRAWER","Drawer")
    rack=location(client,"RACK","Rack")
    out=location(client,"OUT","Finished bin")
    pla=item(client,"material","PLA-RED","Red filament",unit="g",pack_qty="1",pack_unit="kg",pack_price="19.99",
             filament_type="PLA",filament_colour="Red",spool_grams="1000")
    wood=item(client,"material","WOOD","Wood",unit="sheet",pack_qty="5",pack_unit="sheet",pack_price="12.50")
    comp=manufactured(client,[(pla,"0.04","kg"),(wood,"1","sheet")])
    assert b"Recipe &amp; estimated cost" in client.get(f"/production/item/{comp}").data
    assert b"0.8249" in client.get(f"/production/item/{comp}").data  # estimated (0.7996 + 2.5) / 4
    transact(client,"receive",pla,destination=draw,qty="1",entry_unit="packs")
    fields={f"actual_{pla}":"0.03275",f"source_{pla}":draw,f"actual_{wood}":"0.5",f"source_{wood}":rack,
            "destination":out,"qty":"3","reason":"Workshop batch 001"}
    form=client.get(f"/production/stock/production?item_id={comp}")
    assert b"Actual consumed (kg)" in form.data and b"1000 g available" in form.data and b"Actual consumed (sheet)" in form.data
    assert b"Not enough Wood" in review(client,"production",comp,**fields)[0].data
    with workshop.app_context():
        assert (stock.balance(pla),stock.balance(wood),stock.balance(comp))==(1000*stock.SCALE,0,0)
    transact(client,"receive",wood,destination=rack,qty="1",entry_unit="packs")
    invalid=fields|{f"actual_{wood}":""}
    assert b"Actual Wood used" in review(client,"production",comp,**invalid)[0].data
    page, approval=review(client,"production",comp,**fields)
    assert b"Estimate 0.030000 kg" in page.data and b"1.904673" in page.data
    with workshop.app_context():
        db.session.execute(text("""CREATE TRIGGER fail_inventory_output BEFORE INSERT ON inventory_movement
          WHEN NEW.kind='production_output' BEGIN SELECT RAISE(ABORT,'fixture failure'); END"""))
        db.session.commit()
        f={key:str(val) for key,val in fields.items()}|{"item_id":str(comp)}
        with pytest.raises(IntegrityError,match="fixture failure"):
            stock.post("production",f,uuid4().hex,stock.digest(stock.plan("production",f)),actor="1")
        db.session.rollback()
        assert (stock.balance(pla),stock.balance(wood),stock.balance(comp))==(1000*stock.SCALE,5*stock.SCALE,0)
        assert db.session.scalar(select(func.count(Transaction.id)))==2
        db.session.execute(text("DROP TRIGGER fail_inventory_output"))
        db.session.commit()
    receipt=approve(client,"production",approval)
    assert receipt.status_code==303 and approve(client,"production",approval).location==receipt.location
    page=client.get(receipt.location)
    assert b"Actual cost per good piece" in page.data and b"0.634891" in page.data
    transact(client,"receive",pla,destination=draw,qty="200",cost="20")
    assert b"1.904673" in client.get(receipt.location).data
    with workshop.app_context():
        assert (stock.balance(pla),stock.balance(wood),stock.balance(comp))==(1167250000,4500000,3000000)
        batch=db.session.get(Transaction,receipt.location.rsplit("/",1)[1])
        assert batch.cost==1904673 and json.loads(batch.details)["good_pieces"]==3
        assert [(m.kind,m.qty,m.cost) for m in batch.moves]==[("production_consumption",-32750000,654673),
            ("production_consumption",-500000,1250000),("production_output",3000000,1904673)]


def test_two_operators_cannot_confirm_the_same_last_stock(workshop):
    client=client_for(workshop)
    origin=location(client,"FIRST","First shelf")
    target=location(client,"SECOND","Second shelf")
    material=item(client,"material","PLATES","Plates")
    transact(client,"opening",material,qty="1",destination=origin,reason="Initial count")
    fields={"item_id":str(material),"source":str(origin),"destination":str(target),"qty":"1"}
    with workshop.app_context():
        digest=stock.digest(stock.plan("move",fields))
    gate=Barrier(2,timeout=5)

    def submit(actor):
        with workshop.app_context():
            gate.wait()
            try:
                changed=stock.post("move",fields,uuid4().hex,digest,actor=actor)
                db.session.commit()
                return changed.id
            except ValueError as exc:
                db.session.rollback()
                return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(submit,("1","2")))
    assert sum(bool(re.fullmatch(r"[a-f0-9]{32}",val)) for val in results)==1
    assert any("Not enough" in val for val in results)
    with workshop.app_context():
        assert stock.balance(material,origin)==0 and stock.balance(material,target)==stock.SCALE
        assert db.session.scalar(select(func.count(Transaction.id)))==2


def test_validation_of_units_recipe_and_stale_stock_confirmation(workshop):
    client=client_for(workshop)
    location_id=location(client,"SHELF","Shelf")
    g=item(client,"material","PLA","Filament",unit="g",pack_qty="1",pack_unit="kg")
    with pytest.raises(ValueError,match="compatible"):
        stock.converted(stock.SCALE,"ml","g")
    assert stock.converted(250000000,"mm","m")==250000
    assert stock.converted(4500000,"l","ml")==4500000000
    with pytest.raises(ValueError,match="whole"):
        stock.converted(500000,"piece","piece")
    with pytest.raises(ValueError,match="precision"):
        stock.converted(1,"g","kg")
    _, old=review(client,"receive",g,destination=location_id,qty="10",entry_unit="units")
    transact(client,"receive",g,destination=location_id,qty="15")
    blocked=approve(client,"receive",old)
    assert blocked.status_code==409 and b"changed after preview" in blocked.data
    bad=MultiDict([("name","Part"),("code","PART"),("yield_qty","2"),("active","yes"),
        ("material_id",g),("material_qty","3"),("material_unit","g"),
        ("material_id",g),("material_qty","4"),("material_unit","g")])
    assert b"distinct raw material" in client.post("/production/item/new/manufactured",data=bad).data
    part=manufactured(client,[(g,"5","g")])
    assert b"material before production" not in client.get(f"/production/item/{part}").data
    assert b"Record a cost for Filament" in review(client,"production",part,destination=location_id,qty="1",
        **{f"source_{g}":location_id,f"actual_{g}":"2"})[0].data
    with workshop.app_context():
        assert stock.balance(g)==15000000 and stock.balance(part)==0


def test_label_svg_uses_real_code128_and_qr_and_shares_identifier(workshop):
    from barcode import Code128
    from qrcode import make
    from qrcode.image.svg import SvgPathFillImage

    client=client_for(workshop)
    row=location(client,"SH-A-01","Bin 01")
    bundle=client.get(f"/production/label/{row}?size=125")
    assert b"Bin 01" in bundle.data and b"SH-A-01" in bundle.data and b"workshop-label-125" in bundle.data
    def svg(kind):
        response=client.get(f"/production/label/{row}/{kind}.svg")
        assert response.mimetype=="image/svg+xml" and response.headers["X-Content-Type-Options"]=="nosniff"
        return ElementTree.fromstring(response.data)
    vector=svg("code128")
    _, *bars=vector.findall(".//{http://www.w3.org/2000/svg}rect") # background, then black bars
    patterns=Code128("SH-A-01").build()[0]
    widths=[len(group) for group in re.findall(r"1+",patterns)]
    assert [round(float(bar.get("width").removesuffix("mm"))/.29) for bar in bars]==widths
    qr=svg("qr")
    expected=ElementTree.fromstring(make("SH-A-01",image_factory=SvgPathFillImage).to_string())
    assert qr.find(".//{http://www.w3.org/2000/svg}path").get("d")==expected.find(".//{http://www.w3.org/2000/svg}path").get("d")
    assert client.get("/production/lookup?code=SH-A-01").location.endswith(f"/location/{row}")
    assert client_for(workshop,3).get(f"/production/label/{row}/qr.svg").status_code==403
