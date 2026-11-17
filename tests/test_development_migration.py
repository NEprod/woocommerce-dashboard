from contextlib import closing
from datetime import datetime
import json
import os
import sqlite3

from flask import Flask
import pytest

from app import db
from app.database import ensure_database
from app import deployment_guard as guard
from app.development_migration import run
from app.inventory.models import Identity, Item, Location, Movement, Transaction
from app.models import (Collection, Product, Variation, ShopifyProductIdentity,
                        ShopifyPublishJob, ScheduledTask, ScheduledTaskRun, ShopifyOrder)
from app.promotions import domain, publish
from app.promotions.models import Promotion
from app.promotions.shopify import ShopifyPromotionError
from app.shopify_connection import ShopifyAdapter, ShopifyConfiguration, ShopifyError


APP = "gid://shopify/App/1234567"
TOKEN = "fixture-only-token-123456"
ROOTS = ("instance", "catalogue", "output")
REL = "Prints/Test Print"
MARKET = "gid://shopify/Market/123"
FIXED = "gid://shopify/ProductVariant/456"
PRIVATE = "fixture-private-order@example.invalid"


def dump(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def mocked(cfg):
    assert cfg.domain == guard.DEV_DOMAIN
    return (guard.DEV_DOMAIN, guard.DEV_SHOP, APP, True)


def env_for(root, storage="a" * 32, **options):
    return {
        "TLC_DEPLOYMENT_ENV": "development",
        "TLC_DEPLOYMENT_STORAGE_ID": storage,
        "TLC_DEPLOYMENT_SHOP_DOMAIN": guard.DEV_DOMAIN,
        "TLC_DEPLOYMENT_SHOP_ID": guard.DEV_SHOP,
        "TLC_DEPLOYMENT_PRODUCT_APP_ID": APP,
        "PRODUCT_FOLDER": str(root / "catalogue"),
        "OUTPUT_FOLDER": str(root / "output"),
        "SHOPIFY_STORE_DOMAIN": guard.DEV_DOMAIN,
        "SHOPIFY_ADMIN_ACCESS_TOKEN": TOKEN,
        **options,
    }


@pytest.fixture
def storage(tmp_path):
    env = env_for(tmp_path)
    for role in ROOTS:
        path = tmp_path / role
        path.mkdir()
        dump(path / guard.MARKER, guard.identity(env).marker(role))
    return tmp_path, env


def test_legacy_and_marker_fallback(tmp_path, storage):
    assert guard.verify_startup(tmp_path / "legacy", {}) is None
    root, _ = storage
    with pytest.raises(guard.DeploymentError, match="legacy fallback"):
        guard.verify_startup(root / "instance", {})
    with pytest.raises(guard.DeploymentError, match="incomplete"):
        guard.verify_startup(root / "instance", {"TLC_DEPLOYMENT_ENV": "development"})


@pytest.mark.parametrize("field,value", [
    ("SHOPIFY_STORE_DOMAIN", "unrelated.myshopify.com"),
    ("TLC_DEPLOYMENT_SHOP_ID", guard.LIVE_SHOP),
    ("TLC_DEPLOYMENT_PRODUCT_APP_ID", guard.LIVE_APP),
    ("TLC_DEPLOYMENT_PRODUCT_APP_ID", guard.PROMOTIONS_APP),
    ("TLC_DEPLOYMENT_STORAGE_ID", "b" * 32),
    ("TLC_PHOTO_WORKER_TOKEN", "never-print-this"),
    ("SHOPIFY_ORDERS_WEBHOOK_DOMAIN", "unrelated.myshopify.com"),
    ("TLC_PROMOTIONS_DEV_APP_ID", guard.LIVE_APP),
])
def test_bad_local_config_and_swapped_storage_never_call_shopify(storage, field, value):
    root, env = storage
    env[field] = value
    with pytest.raises(guard.DeploymentError):
        guard.verify_startup(root / "instance", env, verify_product=lambda _: pytest.fail("remote"))


def test_storage_alias_and_db_hardlink(storage, tmp_path):
    root, env = storage
    guard.validate_storage(guard.identity(env), guard.roots_for(root / "instance", env))
    for target in (root / "instance", root / "catalogue" / "child"):
        target.mkdir(exist_ok=True)
        with pytest.raises(guard.DeploymentError):
            guard.verify_startup(root / "instance", env | {"OUTPUT_FOLDER": str(target)}, verify_product=mocked)
    link = tmp_path / "alias"
    link.symlink_to(root / "catalogue")
    with pytest.raises(guard.DeploymentError):
        guard.verify_startup(root / "instance", env | {"OUTPUT_FOLDER": str(link)}, verify_product=mocked)
    original = tmp_path / "unmanaged.db"
    original.touch()
    os.link(original, root / "instance/site.db")
    with pytest.raises(guard.DeploymentError, match="non-aliased"):
        guard.verify_startup(root / "instance", env, verify_product=mocked)


@pytest.mark.parametrize("evidence", [
    ("different.myshopify.com", guard.DEV_SHOP, APP, True),
    (guard.DEV_DOMAIN, guard.LIVE_SHOP, APP, True),
    (guard.DEV_DOMAIN, guard.DEV_SHOP, guard.LIVE_APP, True),
    (guard.DEV_DOMAIN, guard.DEV_SHOP, APP, False),
])
def test_authentic_shop_and_app_evidence_must_match(storage, evidence):
    root, env = storage
    with pytest.raises(guard.DeploymentError, match="Authenticated Shopify"):
        guard.verify_startup(root / "instance", env, verify_product=lambda _: evidence)
    assert not (root / "instance/site.db").exists()


def test_bad_credentials_fail_before_flask_migrations_or_workers(storage, monkeypatch):
    import app
    root, env = storage
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(app, "Flask", lambda *args, **kwargs: Flask(
        "managed_fixture", instance_path=str(root / "instance"), **kwargs))
    def rejected(*_):
        raise ShopifyError("fixture token SECRET cannot work")
    monkeypatch.setattr(ShopifyAdapter, "deployment_identity", rejected)
    with pytest.raises(guard.DeploymentError, match="authentication") as error:
        app.create_app()
    assert "SECRET" not in str(error.value)
    assert not (root / "instance/site.db").exists()


def test_managed_transport_and_scheduler_are_fenced(storage, monkeypatch):
    from app import scheduled_tasks
    root, env = storage
    proof = guard.verify_startup(root / "instance", env, verify_product=mocked)
    fixture = Flask("deployment_test")
    fixture.config["TLC_DEPLOYMENT_PROOF"] = proof
    with fixture.app_context():
        good = ShopifyConfiguration(guard.DEV_DOMAIN, TOKEN)
        guard.product_transport(good, writing=False)
        with pytest.raises(ShopifyError, match="refused"):
            guard.product_transport(good, writing=True)
        with pytest.raises(ShopifyError, match="refused"):
            guard.product_transport(ShopifyConfiguration(guard.DEV_DOMAIN, TOKEN + "bad"), writing=False)
        monkeypatch.setattr(scheduled_tasks, "enqueue", lambda *a, **kw: pytest.fail("background enqueue"))
        calls = []
        monkeypatch.setattr(scheduled_tasks, "claim", lambda owner, *, manual_only: calls.append(manual_only))
        scheduled_tasks.tick("fixture")
        assert calls == [True]


def test_promotions_proof_separate(storage):
    root, env = storage
    prefix = "TLC_PROMOTIONS_DEV_"
    supplied = {"DOMAIN": guard.DEV_DOMAIN, "STORE_ID": guard.DEV_SHOP,
                "APP_ID": guard.PROMOTIONS_APP, "CLIENT_ID": guard.PROMOTIONS_CLIENT,
                "KEY_ID": "fixture", "PRIVATE_KEY_PATH": "/not-a-real/private.pem",
                "UK_MARKET_ID": guard.DEV_MARKET}
    env.update({prefix + k: v for k, v in supplied.items()})
    observed = []
    proof = guard.verify_startup(root / "instance", env, verify_product=mocked, verify_promotions=observed.append)
    assert proof[2] == observed[0] and observed[0].app != proof[0].app


def test_production_identity_requires_distinct_confirmed_app(storage):
    root, env = storage
    domain = "synthetic-production.myshopify.com"
    env.update(TLC_DEPLOYMENT_ENV="production", TLC_DEPLOYMENT_SHOP_DOMAIN=domain,
               TLC_DEPLOYMENT_SHOP_ID=guard.LIVE_SHOP, TLC_DEPLOYMENT_PRODUCT_APP_ID=guard.LIVE_APP,
               SHOPIFY_STORE_DOMAIN=domain)
    with pytest.raises(guard.DeploymentError, match="another environment"):
        guard.verify_startup(root / "instance", env, verify_product=lambda _: pytest.fail("remote"))
    for role in ROOTS:
        dump(root / role / guard.MARKER, guard.identity(env).marker(role))
    proof = guard.verify_startup(root / "instance", env,
        verify_product=lambda cfg: (cfg.domain, guard.LIVE_SHOP, guard.LIVE_APP, False))
    assert proof[0].environment == "production" and not proof[0].automation


@pytest.fixture
def backup(tmp_path):
    root = tmp_path / "source"
    db_file = root / "instance/site.db"
    ensure_database("sqlite:///" + str(db_file))
    fixture = Flask("synthetic_backup")
    fixture.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + str(db_file)
    db.init_app(fixture)
    p = root / "catalogue" / REL
    p.mkdir(parents=True)
    bom = {"lines": {"a" * 32: {"op": "add", "item_id": 7, "quantity": "1", "unit": "piece"}}}
    local_doc = {"title": "Fictional print", "live": True, "shopify_status": "ACTIVE",
                 "shopify_market_ids": [MARKET], "production": bom}
    dump(p.parent / "product_info.json", local_doc)
    dump(p / "product_info.json", {"description": "Synthetic override"})
    dump(p / ".scanned", {"sku": "EX-1", "variations": [{"sku": "EX-1-A"}]})
    dump(root / "catalogue/sku_index.json", {"EX": 1})
    (p / "photo.png").write_bytes(b"synthetic image bytes")
    (root / "instance/.env").write_text("SHOPIFY_ADMIN_ACCESS_TOKEN=not-real-keep-out")
    now = datetime(2026, 11, 15)
    with fixture.app_context():
        collection = Collection(id=11, name="Fictional", slug="fictional", sku_prefix="EX", root_path="/catalogue/Prints",
                                source_relpath="Prints", shared_json_path="/catalogue/Prints/product_info.json",
                                shared_json_relpath="Prints/product_info.json")
        product = Product(id=12, title="Fictional print", sku="EX-1", product_type="variable",
                          collection_id=11, source_relpath=REL, product_dir="/catalogue/" + REL,
                          resolved_row_json=json.dumps({**local_doc, "Images": "https://synthetic.invalid/p.png"}))
        variant = Variation(id=13, product_id=12, sku="EX-1-A", catalogue_status="active")
        task = db.session.get(ScheduledTask, "shopify_orders")
        task.enabled = True
        task.active_run_id = "a" * 32
        db.session.add_all([collection, product, variant,
            Identity(id=7, code="TESTITEM7", kind="item"),
            Identity(id=8, code="TESTLOC8", kind="location"),
            Item(id=7, name="Test Paper", kind="material", category="Paper", unit="piece"),
            Location(id=8, name="Test Drawer"),
            Transaction(id="t" * 32, item_id=7, actor="test", actor_label="test", kind="opening",
                        created_at=now, reason="synthetic stock", details="{}", request_hash="fixture", cost=100),
            Movement(id=22, transaction_id="t" * 32, item_id=7, location_id=8, qty=3000000,
                     unit="piece", kind="opening", cost=100),
            ShopifyProductIdentity(product_id=12, source_identity=REL, sku="EX-1",
                store_id=guard.LIVE_SHOP, store_domain="old-shop.myshopify.com", store_key="test",
                shopify_product_id="gid://shopify/Product/678", state="verified", verification_state="verified",
                reviewed_digest="synthetic", media_evidence_json=json.dumps({"gid": FIXED})),
            ShopifyPublishJob(product_id=12, source_identity=REL, requested_revision="old", state="pending",
                               next_attempt=now, updated_at=now),
            ScheduledTaskRun(id="a" * 32, task_key="shopify_orders", trigger="startup", state="pending",
                             created_at=now, data_json="{}"),
            ShopifyOrder(store_id=guard.LIVE_SHOP, remote_id="gid://shopify/Order/123", name="fixture",
                         api_version="2026-10", financial_status="PAID", fulfillment_status="UNFULFILLED",
                         currency="GBP", created_at=now, updated_at=now, email=PRIVATE, test=False,
                         total=3, money_json="{}", shipping_json="{}", quantity=1, outstanding_quantity=1),
            Promotion(id="b" * 32, updated_at=now, state="verified", receipt='{"id":"gid://shopify/DiscountNode/111"}',
                document=domain.canonical(domain.definition(dict(name="Two print offer", kind="exact",
                    method="automatic", scope="product", intent="live", starts="2026-11-15T00:00:00Z",
                    targets=[dict(mode="fixed", id=12, source=REL, sku="EX-1-A", gid=FIXED)],
                    tiers=[{"qty": 2, "total": 900}]))))
        ])
        db.session.commit()
        db.session.remove()
        db.engine.dispose()
    return root


def test_offline_copy_preserves_ledger_and_detaches_all_remote_state(backup, tmp_path):
    before = {p.relative_to(backup): p.read_bytes() for p in backup.rglob("*") if p.is_file()}
    audit = run(backup)
    assert not audit["blockers"], audit
    assert audit["balance_groups_verified"] == 1
    assert audit["review"] == {"market_documents": 1, "variant_targets": 1}
    assert audit["excluded"]["shopify_order"] == 1
    assert audit["detached"]["scheduled_task_run"] == 1
    destination = tmp_path / "new"
    assert run(backup, destination=destination, approval="bad")["blockers"] and not destination.exists()
    staged = run(backup, destination=destination, approval=audit["review_digest"])
    assert staged["staged"], staged
    assert not (destination / "instance/.env").exists()
    actual = {p.relative_to(backup): p.read_bytes() for p in backup.rglob("*") if p.is_file()}
    assert before == actual
    pdoc = json.loads((destination / "catalogue/Prints/product_info.json").read_bytes())
    assert pdoc["production"]["lines"]["a" * 32]["item_id"] == 7
    assert pdoc["shopify_status"] == "ACTIVE" and "shopify_market_ids" not in pdoc
    assert (destination / "catalogue" / REL / ".update").is_file()
    assert (destination / "catalogue" / REL / "photo.png").read_bytes() == b"synthetic image bytes"
    assert PRIVATE.encode() not in (destination / "instance/site.db").read_bytes()
    with closing(sqlite3.connect(destination / "instance/site.db")) as con:
        assert con.execute("SELECT id,code FROM inventory_identity WHERE id=7").fetchone() == (7,"TESTITEM7")
        assert con.execute("SELECT id,item_id,location_id,qty,cost FROM inventory_movement").fetchone() == (22,7,8,3000000,100)
        assert con.execute("SELECT id,sku FROM variation").fetchone() == (13,"EX-1-A")
        assert not con.execute("SELECT 1 FROM scheduled_task WHERE enabled OR active_run_id IS NOT NULL").fetchone()
        for table in ("shopify_order", "shopify_product_identity", "scheduled_task_run", "shopify_publish_job"):
            assert not con.execute(f"SELECT 1 FROM {table}").fetchone()
        raw, receipt, state = con.execute("SELECT document,receipt,state FROM promotion").fetchone()
        assert not receipt and state == "migration_review"
        assert json.loads(raw)["tiers"] == [{"qty": 2, "total": 900}]
        assert FIXED not in raw
        with pytest.raises(ShopifyPromotionError, match="detached"):
            publish.build(Promotion(document=raw, state=state), adapter=None)
    assert run(backup, destination=destination, approval=audit["review_digest"])["blockers"]
    env = env_for(destination, staged["storage_id"])
    assert guard.verify_startup(destination / "instance", env, verify_product=mocked)


def test_legacy_store_rows_block_managed_startup(backup):
    (backup / "output").mkdir()
    env = env_for(backup)
    for role in ROOTS:
        dump(backup / role / guard.MARKER, guard.identity(env).marker(role))
    with pytest.raises(guard.DeploymentError, match="another store"):
        guard.verify_startup(backup / "instance", env, verify_product=lambda _: pytest.fail("remote"))


def test_migrated_dev_restarts_after_publishing_and_a_failed_status_read(backup, tmp_path, monkeypatch):
    from app import shopify_drafts, shopify_status
    from app.models import CatalogueOperation, ShopifyProductComparison, ShopifyPublishingEvent

    destination = tmp_path / "published-dev"
    review = run(backup)
    result = run(backup, destination=destination, approval=review["review_digest"])
    assert result["staged"], result
    env = env_for(destination, result["storage_id"])
    instance = destination / "instance"
    assert guard.verify_startup(instance, env, verify_product=mocked)

    path = instance / "site.db"
    fixture = Flask("published_dev")
    fixture.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + str(path)
    db.init_app(fixture)
    monkeypatch.setattr(shopify_drafts, "configuration", lambda: ShopifyConfiguration(guard.DEV_DOMAIN, TOKEN))
    now = datetime(2026, 11, 17)
    operation = "e" * 32
    remote = "gid://shopify/Product/1234567"
    receipts = json.dumps({"variant_maps": {"EX-1-A": {"id": FIXED}}, "completed_steps": ["product_create"]})
    with fixture.app_context():
        db.session.add(CatalogueOperation(id=operation, operation_type="shopify_draft_publish",
            status="succeeded", started_at=now, finished_at=now, products_succeeded=1))
        db.session.flush()
        db.session.add_all([
            ShopifyProductIdentity(product_id=12, source_identity=REL, sku="EX-1", store_id=guard.DEV_SHOP,
                store_domain=guard.DEV_DOMAIN, store_key=shopify_drafts.digest({"id": guard.DEV_SHOP}),
                shopify_product_id=remote, state="verified", verification_state="verified", operation_id=operation,
                reviewed_digest="fixture", media_evidence_json=receipts, verified_at=now),
            ShopifyProductComparison(store_id=guard.DEV_SHOP, source_identity=REL, summary_json="{}", compared_at=now),
            ShopifyPublishingEvent(id="fixture-published", operation_id=operation, product_id=12,
                event_type="verified_success", trigger="manual", local_revision="fixture", occurred_at=now, receipt_json=receipts),
            ShopifyOrder(store_id=guard.DEV_SHOP, remote_id="gid://shopify/Order/4321", name="dev fixture",
                api_version="2026-10", financial_status="PAID", fulfillment_status="UNFULFILLED", currency="GBP",
                created_at=now, updated_at=now, email=PRIVATE, test=True, total=3, money_json="{}", shipping_json="{}",
                quantity=1, outstanding_quantity=1),
        ])
        db.session.commit()
        shopify_status.mark_failure(db.session.get(Product, 12), None, "Fixture read could not complete.")
        marker = ShopifyProductComparison.query.filter(ShopifyProductComparison.store_id != guard.DEV_SHOP).one()
        assert marker.store_id == "unverified:" + shopify_drafts.configuration().fingerprint()
        db.session.remove()
        db.engine.dispose()

    before = path.read_bytes()
    assert guard.verify_startup(instance, env, verify_product=mocked)
    assert path.read_bytes() == before  # Includes IDs, receipts, orders, and the staged inventory ledger.
    assert json.loads((destination / "catalogue/Prints/product_info.json").read_bytes())["production"]


@pytest.mark.parametrize("table,key,changes", [
    ("shopify_product_comparison", guard.LIVE_SHOP, {}),
    ("shopify_product_comparison", "unverified:bad", {}),
    ("shopify_product_comparison", "unverified:" + "a" * 64, {"config": "b" * 64}),
    ("shopify_product_comparison", "unverified:" + "a" * 64, {"classification": "no_change"}),
    ("shopify_product_comparison", "unverified:" + "a" * 64, {"remote_id": "gid://shopify/Product/678"}),
    ("shopify_product_identity", "unverified:" + "a" * 64, {}),
    ("shopify_order", guard.LIVE_SHOP, {}),
])
def test_store_audit_rejects_foreign_or_misplaced_store_keys(storage, table, key, changes):
    root, env = storage
    path = root / "instance/site.db"
    diagnostic = dict(version=1, config="a" * 64, classification="unavailable", failures=1, retry_after="2026-11-17T09:30:00",
                      refresh_failure={"at": "2026-11-17T09:29:30", "message": "Fixture unavailable"}, failure_local=None)
    with closing(sqlite3.connect(path)) as con, con:
        con.execute(f"CREATE TABLE {table} (store_id TEXT NOT NULL, summary_json TEXT NOT NULL)")
        if table == "shopify_product_comparison":
            con.execute(f"INSERT INTO {table} VALUES (?, ?)", ("unverified:" + "a" * 64, json.dumps(diagnostic)))
        con.execute(f"INSERT INTO {table} VALUES (?, ?)", (key, json.dumps(diagnostic | changes)))
    with pytest.raises(guard.DeploymentError, match="another store") as error:
        guard.verify_startup(root / "instance", env, verify_product=lambda _: pytest.fail("remote"))
    assert f"{table}.store_id" in str(error.value)
    assert key not in str(error.value)


def test_interrupted_copy_cannot_start_as_legacy(backup, tmp_path, monkeypatch):
    from app import development_migration
    audit = run(backup)
    destination = tmp_path / "incomplete"
    def stopped(*_, **__):
        raise development_migration.Refused("Simulated interruption.")
    monkeypatch.setattr(development_migration, "exclusive_copy", stopped)
    result = run(backup, destination=destination, approval=audit["review_digest"])
    assert result["blockers"] and not result["staged"]
    with pytest.raises(guard.DeploymentError, match="legacy fallback"):
        guard.verify_startup(destination / "instance", {})


@pytest.mark.parametrize("problem", ["schema", "bom", "unknown_remote", "scanner", "wal", "symlink", "mismatch"])
def test_ambiguous_backup_is_not_staged(backup, tmp_path, problem):
    catalogue = backup / "catalogue"
    dbpath = backup / "instance/site.db"
    if problem == "schema":
        with closing(sqlite3.connect(dbpath)) as con, con:
            con.execute("UPDATE alembic_version SET version_num='unknown'")
    elif problem == "bom":
        p = catalogue / "Prints/product_info.json"
        bom = json.loads(p.read_text())
        bom["production"]["lines"]["a" * 32]["item_id"] = 999
        dump(p, bom)
    elif problem == "unknown_remote":
        dump(catalogue / REL / "product_info.json", {"unrecognised": FIXED})
    elif problem == "scanner":
        dump(catalogue / REL / ".scanned.pending", {"marker": {"sku": "EX-1", "variations": [{"sku": "EX-1-A"}]}})
    elif problem == "wal":
        dbpath.with_name("site.db-wal").touch()
    elif problem == "symlink":
        (catalogue / "bad.png").symlink_to(backup / "instance/.env")
    else:
        dump(catalogue / REL / ".scanned", {"sku": "EX-WRONG"})
    target = tmp_path / "destination"
    result = run(backup, destination=target, approval="does-not-matter")
    assert result["blockers"] and not target.exists()
    if problem == "scanner":
        assert result["scanner_recovery"]["pending_matching"] == 1
    if problem == "mismatch":
        assert result["scanner_recovery"]["settled_mismatch"] == 1
