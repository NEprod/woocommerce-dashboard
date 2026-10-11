"""Fictional Woo only: durable evidence, read-only refresh, and no Preview dependence."""
import json
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from sqlalchemy import inspect, text

from app import db
from app.database import _alembic_config
from app.models import Product, WooProductIdentity, CatalogueOperation
from app import woo_live_state as live
from app.woo_publish_preview import generate_publish_plan, reset_preview_cache_for_tests, store_identity
from app.woo_controlled_publish import prepare_publish_confirmation, start_publish_operation
from app.woocommerce_connection import WooConnectionError
from test_phase3_woo_publish_preview import preview_app, _client, FakeWooClient, _taxonomy
from test_m56_product_taxonomy import world


def publish(app, monkeypatch, tmp_path, *, variable=False):
    pid, woo, source, *_ = world(app, monkeypatch, tmp_path, variable=variable)
    plan = generate_publish_plan({"kind": "product", "product_id": pid}, client=woo)
    confirm = prepare_publish_confirmation(plan["operation_id"], plan["digest"], [pid], client=woo)
    operation = start_publish_operation(confirm, client=woo, run_async=False)
    assert db.session.get(CatalogueOperation, operation).status == "succeeded"
    return db.session.get(Product, pid), woo, source


@pytest.mark.parametrize("variable", [False, True])
def test_complete_publish_survives_preview_and_process_cache_loss(preview_app, monkeypatch, tmp_path, variable):
    with preview_app.app_context():
        product, woo, _ = publish(preview_app, monkeypatch, tmp_path, variable=variable)
        identity = WooProductIdentity.query.filter_by(product_id=product.id).one()
        hashes = identity.last_published_digest, identity.last_remote_digest
        record = json.loads(identity.live_state_json)
        assert record["version"] == 1 and len(identity.live_state_json.encode()) <= 4096
        reset_preview_cache_for_tests()
        preview_app.extensions.pop("woo_live_state", None)
        assert live.product_state(product)["state"] == "no_change"
        count = woo.request_count
        assert live.refresh_product(product, client=woo)["state"] == "no_change"
        assert woo.request_count == count
        assert hashes == (identity.last_published_digest, identity.last_remote_digest)
        pid = product.id
    client = _client(preview_app)
    for path in ("/woo-sync", "/", f"/products/{pid}"):
        response = client.get(path)
        assert response.status_code == 200
        assert b"No Change" in response.data


def test_local_authored_and_projection_changes_invalidate_without_get(preview_app, monkeypatch, tmp_path):
    with preview_app.app_context():
        product, woo, source = publish(preview_app, monkeypatch, tmp_path)
        before = woo.request_count
        raw = source.read_text()
        data = json.loads(raw); data["price"] = "27.00"
        source.write_text(json.dumps(data))
        assert live.product_state(product)["state"] == "update"
        source.write_text(raw)
        product.title = "Locally changed title"; db.session.commit()
        assert live.product_state(product)["state"] == "update"
        assert before == woo.request_count


def test_fresh_cache_stale_refresh_force_and_remote_drift(preview_app, monkeypatch, tmp_path):
    with preview_app.app_context():
        product, woo, _ = publish(preview_app, monkeypatch, tmp_path)
        identity = WooProductIdentity.query.filter_by(product_id=product.id).one()
        record = json.loads(identity.live_state_json)
        record["checked_at"] = (datetime.now(UTC)-timedelta(minutes=11)).isoformat()
        identity.live_state_json = json.dumps(record); db.session.commit()
        assert live.product_state(product)["needs_refresh"]
        reads = woo.request_count; writes = list(woo.writes)
        assert live.refresh_product(product, client=woo)["state"] == "no_change"
        assert woo.request_count > reads and woo.writes == writes
        reads = woo.request_count
        assert live.refresh_product(product, client=woo)["state"] == "no_change"
        assert reads == woo.request_count
        woo.products[identity.woo_product_id]["name"] = "Remote changed title"
        assert live.refresh_product(product, force=True, client=woo)["state"] == "remote_drift"
        assert woo.request_count > reads and woo.writes == writes
        preview_app.extensions.pop("woo_live_state", None)
        assert live.product_state(product)["state"] == "remote_drift"


def test_failure_preserves_last_known_and_cools_down(preview_app, monkeypatch, tmp_path):
    with preview_app.app_context():
        product, woo, _ = publish(preview_app, monkeypatch, tmp_path)
        identity = WooProductIdentity.query.filter_by(product_id=product.id).one()
        saved = identity.live_state_json
        def unavailable(*a, **k): raise WooConnectionError("network_failure", "Fictional offline")
        monkeypatch.setattr(woo, "request_json", unavailable)
        state = live.refresh_product(product, force=True, client=woo)
        assert state["state"] == "no_change" and "Unable to refresh" in state["message"]
        assert state["verified_at"]
        assert identity.live_state_json == saved
        assert live.refresh_product(product, client=woo) == state


def test_local_only_and_old_identity_unknown_never_backfilled(preview_app):
    with preview_app.app_context():
        p = db.session.get(Product, 3)
        assert live.product_state(p)["state"] == "local_only"
        store = store_identity()
        row = WooProductIdentity(product_id=p.id, store_key=store["key"], store_host=store["host"],
            sku=p.sku, stable_identity=p.source_relpath, woo_product_id=303, verification_state="verified",
            last_published_digest="a"*64, last_remote_digest="b"*64, sync_state="synced")
        db.session.add(row); db.session.commit()
        assert live.product_state(p)["state"] == "unknown"
        assert row.live_state_json is None


def test_unknown_summary_is_not_active_checking_in_paginated_workspace(preview_app, monkeypatch):
    with preview_app.app_context():
        for i in range(51):
            db.session.add(Product(title=f"Unknown {i}", sku=f"UNKNOWN-{i}",
                product_type="simple", catalogue_status="active"))
        db.session.commit()
        count = Product.query.filter_by(catalogue_status="active").count()
    monkeypatch.setattr(live, "product_state", lambda *a, **kw: live._view("unknown", refresh=False))
    response = _client(preview_app).get('/woo-sync')
    assert response.status_code == 200
    assert b'data-woo-active-count>0</strong>' in response.data
    assert f'data-woo-count="unknown">{count}</strong>'.encode() in response.data
    assert b'Unknown / not checked' in response.data
    assert response.data.count(b'data-woo-product=') == 50


def test_publishing_running_returns_non_checking_retryable_state(preview_app):
    with preview_app.app_context():
        product = db.session.get(Product, 3)
        db.session.add(CatalogueOperation(id="active-publish", operation_type="woo_controlled_publish",
            status="running", recovery_state="none", scope="{}"))
        db.session.commit()
        state = live.product_state(product)
        assert state["state"] == "unknown" and "Checking" not in state["label"]
        assert not state["needs_refresh"] and "Publishing is running" in state["message"]
        woo = FakeWooClient()
        result = live.refresh_product(product, force=True, client=woo)
        assert result["busy"] and woo.request_count == 0


def test_unlinked_refresh_reuses_cache_never_links_or_writes(preview_app):
    with preview_app.app_context():
        p = db.session.get(Product, 3)
        woo = FakeWooClient(taxonomy=_taxonomy(), by_sku={p.sku: [{"id": 321, "sku": p.sku, "type": "simple"}]})
        assert live.refresh_product(p, client=woo)["state"] == "link_candidate"
        count = woo.request_count
        assert live.refresh_product(p, client=woo)["state"] == "link_candidate"
        assert count == woo.request_count and set(woo.methods) == {"GET"}
        assert WooProductIdentity.query.count() == 0


def test_recovery_wins_even_outside_recent_history(preview_app, monkeypatch, tmp_path):
    with preview_app.app_context():
        product, woo, _ = publish(preview_app, monkeypatch, tmp_path)
        db.session.add(CatalogueOperation(id="unresolved", operation_type="woo_controlled_publish", status="failed",
            recovery_state="review_required", scope=json.dumps({"store_identity": store_identity()["key"], "product_ids": [product.id]})))
        db.session.commit()
        count = woo.request_count
        state = live.refresh_product(product, force=True, client=woo)
        assert state["state"] == "recovery_required" and state["recovery_id"] == "unresolved"
        assert woo.request_count == count


@pytest.mark.parametrize("status", ["pass_1_verified", "pass_2_failed", "recovery_required", "verified_with_warnings"])
def test_incomplete_publish_cannot_persist_complete_proof(preview_app, monkeypatch, tmp_path, status):
    with preview_app.app_context():
        product, _, _ = publish(preview_app, monkeypatch, tmp_path)
        live.record_verified_publish(product, {}, {"status": status}, live.source_digest(product), store_identity()["key"])
        db.session.commit()
        assert WooProductIdentity.query.filter_by(product_id=product.id).one().live_state_json is None


def test_read_facade_rejects_writes_and_bounds_requests(preview_app):
    with preview_app.app_context():
        reader = live._Reads(FakeWooClient(), store_identity()["key"], True)
        for method in ("POST", "PUT", "DELETE"):
            with pytest.raises(WooConnectionError, match="GET only"):
                reader.request_json(method, "https://shop.example.test/wp-json/wc/v3/products")
        reader.request_count = live.MAX_READS
        with pytest.raises(WooConnectionError, match="budget"):
            reader.request_json("GET", "https://shop.example.test/wp-json/wc/v3/products")


def test_authenticated_csrf_incremental_endpoint(preview_app, monkeypatch):
    client = _client(preview_app)
    assert preview_app.test_client().post("/woo-sync/state/3").status_code == 401
    monkeypatch.setattr(live, "refresh_product", lambda p, **kw: live._view("local_only", refresh=False))
    response = client.post("/woo-sync/state/3")
    assert response.json["state"] == "local_only" and response.headers["Cache-Control"] == "no-store"
    preview_app.config["WTF_CSRF_ENABLED"] = True
    assert client.post("/woo-sync/state/3").status_code == 400


def test_nullable_migration_without_backfill(preview_app):
    with preview_app.app_context():
        columns = {c["name"]: c for c in inspect(db.engine).get_columns("woo_product_identity")}
        assert columns["live_state_json"]["nullable"] and columns["live_state_json"]["default"] is None
        assert db.session.execute(text("select version_num from alembic_version")).scalar() == "0009_woo_live_state"


def test_migration_upgrade_retains_old_unknown_identity(tmp_path):
    from sqlalchemy import create_engine
    url = f"sqlite:///{tmp_path / 'migration.db'}"
    config = _alembic_config(url)
    command.upgrade(config, "0008_woo_taxonomy_identity")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO product (id,title) VALUES (1,'Old product')"))
        connection.execute(text("INSERT INTO woo_product_identity (product_id,stable_identity,store_key,store_host,woo_product_id,last_published_digest) VALUES (1,'old','store','example.test',9,'old-hash')"))
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT live_state_json,last_published_digest FROM woo_product_identity")).one() == (None, "old-hash")
    command.downgrade(config, "0008_woo_taxonomy_identity")
    assert "live_state_json" not in {c["name"] for c in inspect(engine).get_columns("woo_product_identity")}
    engine.dispose()


def test_busy_refresh_and_changed_identity_do_not_reuse_proof(preview_app, monkeypatch, tmp_path):
    with preview_app.app_context():
        product, woo, _ = publish(preview_app, monkeypatch, tmp_path)
        runtime = live._runtime()
        runtime["lock"].acquire()
        try:
            assert live.refresh_product(product, force=True, client=woo)["busy"]
        finally:
            runtime["lock"].release()
        row = WooProductIdentity.query.filter_by(product_id=product.id).one()
        row.woo_product_id += 100
        db.session.commit()
        assert live.product_state(product)["state"] == "unknown"


def test_new_write_attempt_invalidates_previous_proof_before_failure(preview_app, monkeypatch, tmp_path):
    with preview_app.app_context():
        product, _, _ = publish(preview_app, monkeypatch, tmp_path)
        assert live.product_state(product)["state"] == "no_change"
        live.invalidate_products([product.id], store_identity()["key"])
        db.session.commit()
        assert live.product_state(product)["state"] == "unknown"


@pytest.mark.parametrize("force", [False, True], ids=["stale", "manual"])
def test_observation_recovery_rechecks_and_replaces_evidence(preview_app, monkeypatch, tmp_path, force):
    with preview_app.app_context():
        product, woo, _ = publish(preview_app, monkeypatch, tmp_path)
        identity = WooProductIdentity.query.filter_by(product_id=product.id).one()
        store = store_identity()["key"]
        stamp = (datetime.now(UTC) - timedelta(minutes=0 if force else 11)).isoformat()
        saved = json.loads(identity.live_state_json)
        saved["observation"] = dict(state="recovery_required", checked_at=stamp,
            source=live.source_digest(product), dependencies=live._dependencies(product, store),
            message="Media identity required before publishing: 14 unreachable.")
        identity.live_state_json = json.dumps(saved)
        db.session.commit()
        previous = live.product_state(product)
        live._runtime()["states"][(store, product.id)] = dict(
            source=saved["source"], dependencies=saved["dependencies"], fresh_from=stamp, view=previous)
        reads, writes = woo.request_count, list(woo.writes)
        assert previous["state"] == "recovery_required" and previous["recovery_id"] is None
        result = live.refresh_product(product, force=force, client=woo)
        assert result["state"] == "no_change" and woo.request_count > reads
        assert woo.writes == writes
        assert "observation" not in json.loads(identity.live_state_json)
        assert live.product_state(product)["state"] == "no_change"
        preview_app.extensions.pop("woo_live_state", None)
        assert live.product_state(product)["state"] == "no_change"


def test_recovery_appearing_during_read_wins(preview_app, monkeypatch, tmp_path):
    with preview_app.app_context():
        product, woo, _ = publish(preview_app, monkeypatch, tmp_path)
        identity = WooProductIdentity.query.filter_by(product_id=product.id).one()
        saved = identity.live_state_json
        original = live.preview.generate_publish_plan
        def checked(*args, **kwargs):
            plan = original(*args, **kwargs)
            db.session.add(CatalogueOperation(id="during-read", operation_type="woo_controlled_publish",
                status="failed", recovery_state="review_required", scope=json.dumps({
                    "store_identity": store_identity()["key"], "product_ids": [product.id]})))
            db.session.commit()
            return plan
        monkeypatch.setattr(live.preview, "generate_publish_plan", checked)
        result = live.refresh_product(product, force=True, client=woo)
        assert result["state"] == "recovery_required" and result["recovery_id"] == "during-read"
        assert identity.live_state_json == saved


@pytest.mark.parametrize("trusted_proof", [True, False])
@pytest.mark.parametrize("failure", ["budget", "transport"])
def test_absorbed_read_failure_is_not_durable_recovery(preview_app, monkeypatch, tmp_path, trusted_proof, failure):
    with preview_app.app_context():
        product, woo, _ = publish(preview_app, monkeypatch, tmp_path)
        identity = WooProductIdentity.query.filter_by(product_id=product.id).one()
        if not trusted_proof:
            identity.live_state_json = None
            db.session.commit()
        saved = identity.live_state_json
        writes = list(woo.writes)
        original_plan, original_read = live.preview.generate_publish_plan, woo.request_json
        def request(method, url, **kwargs):
            if url.endswith('/failed-media-probe'):
                raise WooConnectionError("read_timeout", "Fictional media timeout")
            return original_read(method, url, **kwargs)
        monkeypatch.setattr(woo, "request_json", request)
        def incomplete(*args, **kwargs):
            plan = original_plan(*args, **kwargs)
            reader = kwargs["client"]
            if failure == "budget":
                reader.request_count = live.MAX_READS
            # Model the existing canonical helper absorbing an unsuccessful GET.
            try:
                reader.request_json("GET", woo.base_url + '/failed-media-probe')
            except WooConnectionError:
                pass
            plan["products"][0]["action"] = "recovery_required"
            plan["products"][0]["blockers"] = ["Media identity required before publishing: 14 unreachable."]
            return plan
        monkeypatch.setattr(live.preview, "generate_publish_plan", incomplete)
        state = live.refresh_product(product, force=True, client=woo)
        assert state["state"] == ("no_change" if trusted_proof else "unknown")
        assert "Unable to refresh" in state["message"]
        assert bool(state["verified_at"]) == trusted_proof
        assert identity.live_state_json == saved and woo.writes == writes
        monkeypatch.setattr(live.preview, "generate_publish_plan", original_plan)
        state = live.refresh_product(product, force=True, client=woo)
        assert state["state"] == "no_change" and woo.writes == writes


def test_untrusted_identity_still_blocks_forced_refresh(preview_app, monkeypatch, tmp_path):
    with preview_app.app_context():
        product, woo, _ = publish(preview_app, monkeypatch, tmp_path)
        identity = WooProductIdentity.query.filter_by(product_id=product.id).one()
        identity.verification_state = "conflict"
        db.session.commit()
        reads = woo.request_count
        assert live.refresh_product(product, force=True, client=woo)["state"] == "recovery_required"
        assert woo.request_count == reads


@pytest.mark.parametrize("case", ["complete", "none", "failed", "partial", "warnings", "pending",
    "pass1", "other_product", "other_store", "older", "same_time", "other_woo", "untrusted",
    "unfinished", "preview", "candidate", "uncertain", "missing_evidence",
    "candidate_none", "candidate_failed", "candidate_partial", "candidate_warnings",
    "candidate_other_product", "candidate_other_store", "candidate_uncertain", "candidate_truncated"])
def test_recovery_supersession_requires_later_complete_evidence(preview_app, monkeypatch, tmp_path, case):
    with preview_app.app_context():
        product, woo, _ = publish(preview_app, monkeypatch, tmp_path)
        identity = WooProductIdentity.query.filter_by(product_id=product.id).one()
        success = CatalogueOperation.query.filter_by(operation_type="woo_controlled_publish").one()
        now = datetime.now(UTC).replace(tzinfo=None)
        # Use the actual publisher's complete result, not a fabricated success shape.
        success.started_at, success.finished_at = now, now + timedelta(seconds=1)
        old_summary = {"product_results": [{"product_id": product.id, "sku": product.sku,
            "status": "recovery_required", "error": "Historical taxonomy conflict"}]}
        retained_candidate = case.startswith("candidate")
        if retained_candidate:
            old_summary["taxonomy"] = {"failures": [{"remote_candidate_id": 686}]}
            case = case.removeprefix("candidate_") if case != "candidate" else "complete"
        if case == "uncertain":
            old_summary["woo_errors"] = [{"uncertain": True}]
        if case == "truncated": old_summary["diagnostics_truncated"] = 1
        old = CatalogueOperation(id="historical-recovery", operation_type="woo_controlled_publish",
            status="failed", recovery_state="review_required", error="Historical failure",
            started_at=now-timedelta(minutes=2), finished_at=now-timedelta(minutes=1),
            scope=json.dumps({"store_identity": store_identity()["key"], "product_ids": [product.id],
                "operation_summary": old_summary}))
        db.session.add(old)
        scope = json.loads(success.scope)
        result = scope["operation_summary"]["product_results"][0]
        if case in {"failed", "partial"}: success.status = case
        if case == "warnings": result["status"] = "verified_with_warnings"
        if case == "pass1": result["status"] = "pass_1_verified"
        if case == "pending": result["relationships"]["pending"] = [{"sku": "WAIT"}]
        if case == "other_product": scope["product_ids"] = [product.id + 100]
        if case == "other_store": scope["store_identity"] = "another-store"
        if case == "older":
            success.started_at, success.finished_at = now-timedelta(minutes=4), now-timedelta(minutes=3)
        if case == "same_time": success.started_at = old.finished_at
        if case == "other_woo": result["woo_id"] += 100
        if case == "untrusted": identity.verification_state = "conflict"
        if case == "unfinished": success.finished_at = None
        if case == "preview": success.operation_type = "woo_publish_preview"
        if case == "missing_evidence": result.pop("relationships")
        success.scope = json.dumps(scope)
        if case == "none": db.session.delete(success)
        db.session.commit()
        historical = (old.status, old.recovery_state, old.scope, old.error, old.started_at, old.finished_at)
        store = store_identity()["key"]
        assert live.recovery_operation(product.id, store) == (None if case == "complete" else old.id)
        if case == "complete":
            reads, writes = woo.request_count, list(woo.writes)
            assert live.refresh_product(product, force=True, client=woo)["state"] == "no_change"
            assert woo.request_count > reads and woo.writes == writes
            # A later real recovery still wins over the already-qualified success.
            db.session.add(CatalogueOperation(id="new-recovery", operation_type="woo_controlled_publish",
                status="failed", recovery_state="review_required", scope=old.scope,
                started_at=now+timedelta(minutes=1), finished_at=now+timedelta(minutes=2)))
            db.session.commit()
            assert live.refresh_product(product, force=True, client=woo)["recovery_id"] == "new-recovery"
        db.session.expire(old)
        assert historical == (old.status, old.recovery_state, old.scope, old.error, old.started_at, old.finished_at)
        if retained_candidate:
            assert json.loads(old.scope)["operation_summary"]["taxonomy"]["failures"] == [{"remote_candidate_id": 686}]
