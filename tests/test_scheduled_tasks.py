"""Isolated SQLite/fictional Shopify. No live Shopify or Discord delivery."""
import copy
import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from flask import current_app
from sqlalchemy import text
from alembic import command

from app import db, scheduled_tasks as tasks, shopify_drafts as drafts, shopify_status
from app import shopify_capacity as capacity
from app.models import (ScheduledTask, ScheduledTaskRun, ShopifyProductComparison, CatalogueOperation,
    Product, ShopifyApiCapacity, ShopifyProductIdentity, User)
from app.database import _alembic_config, ensure_database
from app.shopify_connection import ShopifyError, ShopifyAdapter
from app.utils.operation_control import acquire_catalogue_operation, finish_catalogue_operation, CatalogueOperationActive
from test_phase3_woo_publish_preview import preview_app, _client
from test_shopify_drafts import world
from test_shopify_status_reconciliation import RevisionAdapter
from test_shopify_simple_updates import change
from test_shopify_variables import variable_world, VariableAdapter
from test_shopify_foundation import isolated_transport, Session, Response


@pytest.fixture
def published(world):
    Product.query.filter(Product.id!=world[0].id).update({Product.catalogue_status:"missing"})
    db.session.commit()
    adapter=RevisionAdapter()
    _,token=drafts.preview(world[0],adapter)
    row=drafts.confirm(token,True,adapter)
    adapter.remote["updatedAt"]="2026-10-14T12:00:00Z"
    drafts.preview(world[0],adapter)  # independently complete no-change proof
    adapter.calls.clear()
    return world[0],row,adapter


def age(stamp):
    saved=ShopifyProductComparison.query.one();data=json.loads(saved.summary_json)
    for key in ("full_compared_at","related_checked_at","revision_checked_at"):
        data[key]=stamp.isoformat()
    saved.summary_json=json.dumps(data);db.session.commit()


def run(product,adapter,*,manual=False):
    task=db.session.get(ScheduledTask,tasks.KEY)
    task.next_due=tasks.now()-timedelta(minutes=1);db.session.commit()
    run_id=tasks.enqueue(manual=manual,product_ids=[product.id] if manual else None)
    assert tasks.claim("owner")== (tasks.KEY,run_id)
    tasks.run_slice(tasks.KEY,run_id,"owner",adapter=adapter)
    result=db.session.get(ScheduledTaskRun,run_id)
    return result,json.loads(result.data_json)


def test_default_configuration_and_restart_persistence(preview_app):
    with preview_app.app_context():
        task=db.session.get(ScheduledTask,tasks.KEY)
        assert task.enabled and task.interval_minutes==20 and task.state=="idle"
        original=task.next_due
        tasks.configure(tasks.KEY,enabled=True,interval=30)
        db.session.remove()
        task=db.session.get(ScheduledTask,tasks.KEY)
        assert task.interval_minutes==30 and task.next_due!=original
        assert abs((task.next_due-tasks.now()).total_seconds()-1800)<2


@pytest.mark.parametrize("minutes,skip",[(20,False),(15,False),(9,True),(2,True),(10,False)])
def test_explicit_ten_minute_boundary(published,monkeypatch,minutes,skip):
    stamp=datetime(2026,10,14,12,20)
    monkeypatch.setattr(drafts,"now",lambda:stamp)
    product,row,adapter=published;age(stamp-timedelta(minutes=minutes))
    result,data=run(product,adapter)
    assert result.state=="completed"
    assert data["counts"]["skipped_recent"]==int(skip)
    assert data["counts"]["checked"]==int(not skip)
    assert ("product_read" in adapter.calls) is not skip
    assert not any("write" in call or "create" in call or "update" in call for call in adapter.calls)


def test_manual_run_bypasses_freshness_and_preserves_next_due(published):
    product,row,adapter=published
    task=db.session.get(ScheduledTask,tasks.KEY);original=task.next_due
    run_id=tasks.enqueue(manual=True,product_ids=[product.id])
    assert tasks.enqueue(manual=True,product_ids=[product.id])==run_id
    assert task.next_due==original
    tasks.claim("owner");tasks.run_slice(tasks.KEY,run_id,"owner",adapter=adapter)
    result=db.session.get(ScheduledTaskRun,run_id);data=json.loads(result.data_json)
    assert result.trigger=="manual" and result.state=="completed"
    assert data["counts"]["checked"]==1 and data["counts"]["skipped_recent"]==0
    assert "product_read" in adapter.calls


def test_continuation_uses_run_cutoff_and_new_publish_evidence(published,monkeypatch):
    product,row,adapter=published
    start=datetime(2026,10,14,12,20)
    monkeypatch.setattr(drafts,"now",lambda:datetime(2026,10,14,12,30))
    age(datetime(2026,10,14,12,18))
    assert tasks.recent_success(product,drafts.configuration(),as_of=start)
    assert not tasks.recent_success(product,drafts.configuration())
    age(datetime(2026,10,14,12,25))
    assert tasks.recent_success(product,drafts.configuration(),as_of=start)


def test_publishing_verification_is_immediate_and_recent(published):
    product,row,adapter=published
    assert tasks.recent_success(product,drafts.configuration())
    age(drafts.now()-timedelta(minutes=11))
    assert not tasks.recent_success(product,drafts.configuration())
    drafts.preview(product,adapter)
    assert tasks.recent_success(product,drafts.configuration())


def test_stale_saved_status_remains_visible_without_claiming_current_equality(published):
    from app.shopify_products import states_for
    product,row,adapter=published;age(drafts.now()-timedelta(minutes=11))
    with current_app.test_request_context():state=states_for([product])[product.id]
    assert state["label"]=="Last check matched" and state["evidence_stale"]
    assert state["compared_at"] and "Verification is due" in state["message"] and not adapter.calls


def test_saved_real_changes_remain_visible_between_scheduled_runs(published,world):
    from app.shopify_products import states_for
    product,row,adapter=published;change(world,weight=53);drafts.preview(product,adapter)
    age(drafts.now()-timedelta(minutes=11));adapter.calls.clear()
    with current_app.test_request_context():state=states_for([product])[product.id]
    assert state["label"]=="Update Available (last check)" and state["change_count"]>0
    assert not adapter.calls


def test_changed_local_data_cannot_borrow_stale_equality(published,world):
    from app.shopify_products import states_for
    product,row,adapter=published;age(drafts.now()-timedelta(minutes=11));change(world,weight=54)
    with current_app.test_request_context():state=states_for([product])[product.id]
    assert state["label"]=="Checking Changes" and not state.get("evidence_stale")


@pytest.mark.parametrize("change_kind",["failed","missing","invalidated","incomplete"])
def test_failed_missing_invalidated_evidence_is_not_skipped(published,world,change_kind):
    product,row,adapter=published
    saved=ShopifyProductComparison.query.one();before=json.loads(saved.summary_json)["full_compared_at"]
    if change_kind=="missing":db.session.delete(saved);db.session.commit()
    elif change_kind=="invalidated":change(world,weight=51)
    else:
        data=json.loads(saved.summary_json)
        if change_kind=="failed":data.update(refresh_failure={"message":"Read failed"},retry_after=(drafts.now()+timedelta(minutes=5)).isoformat())
        else:data["read_complete"]=False
        saved.summary_json=json.dumps(data);db.session.commit()
    assert not tasks.recent_success(product,drafts.configuration())
    result,data=run(product,adapter)
    assert data["counts"]["skipped_recent"]==0 and "product_read" in adapter.calls


def test_unavailable_local_source_does_not_abort_other_product_checks(published,monkeypatch):
    from app import shopify_comparison
    product,identity,adapter=published
    other=db.session.get(Product,2);other.catalogue_status="active";db.session.commit()
    original=shopify_comparison.current
    def current(p,*args,**kw):
        if p.id==product.id:raise OSError("Fictional source unavailable")
        return original(p,*args,**kw)
    called=[]
    def preview(p,*args,**kw):
        called.append(p.id)
        if p.id==product.id:raise OSError("Fictional source unavailable")
    monkeypatch.setattr(shopify_comparison,"current",current)
    monkeypatch.setattr(drafts,"preview",preview)
    result=shopify_status.refresh([product,other],force=True,adapter=adapter)
    assert called==[product.id,other.id] and result["checked"]==[product.id,other.id]
    assert len(result["errors"])==1 and result["errors"][0]["product_id"]==product.id


def test_timeout_does_not_renew_successful_verification(published):
    product,row,adapter=published;age(drafts.now()-timedelta(minutes=11))
    before=json.loads(ShopifyProductComparison.query.one().summary_json)["full_compared_at"]
    adapter.read_simple=lambda *args: (_ for _ in ()).throw(ShopifyError("Shopify timed out"))
    result,data=run(product,adapter)
    assert result.state=="partially_completed" and data["counts"]["unavailable"]==1
    assert json.loads(ShopifyProductComparison.query.one().summary_json)["full_compared_at"]==before
    assert not tasks.recent_success(product,drafts.configuration())


def test_genuine_recovery_never_overwritten(published):
    product,row,adapter=published;row.state="uncertain";row.verification_state="unverified";db.session.commit()
    original=row.media_evidence_json
    result,data=run(product,adapter,manual=True)
    assert result.state=="completed" and data["counts"]["attention"]==1
    assert data["counts"]["deferred"]==0  # intentional protection, not failed executable work
    assert row.state=="uncertain" and row.media_evidence_json==original and not adapter.calls


def test_verified_variable_layouts_reconcile_read_only(variable_world):
    product=variable_world[0]
    Product.query.filter(Product.id!=product.id).update({Product.catalogue_status:"missing"});db.session.commit()
    adapter=VariableAdapter();_,token=drafts.preview(product,adapter)
    identity=drafts.confirm(token,True,adapter);assert identity.state=="verified"
    mappings=copy.deepcopy(drafts.evidence(identity)["variant_maps"]);media=copy.deepcopy(adapter.remote["media"])
    adapter.calls.clear();result,data=run(product,adapter,manual=True)
    assert result.state=="completed" and data["counts"]["up_to_date"]==1
    assert mappings==drafts.evidence(identity)["variant_maps"] and media==adapter.remote["media"]
    assert not any("write" in call or "create" in call or "update" in call for call in adapter.calls)


def test_incomplete_variable_readback_is_not_success(variable_world):
    product=variable_world[0];Product.query.filter(Product.id!=product.id).update({Product.catalogue_status:"missing"});db.session.commit()
    adapter=VariableAdapter();_,token=drafts.preview(product,adapter);drafts.confirm(token,True,adapter)
    adapter.remote["variants"]["pageInfo"]["hasNextPage"]=True
    result,data=run(product,adapter,manual=True)
    assert result.state!="completed" and data["counts"]["up_to_date"]==0


def test_opt_in_variable_native_readback_preserves_inventory_and_media(variable_world,monkeypatch):
    monkeypatch.setenv("SHOPIFY_NATIVE_DIMENSIONS_ENABLED","true")
    monkeypatch.setenv("SHOPIFY_PACKED_DIMENSIONS_USE_PRODUCT_DIMENSIONS","true")
    product=variable_world[0];Product.query.filter(Product.id!=product.id).update({Product.catalogue_status:"missing"});db.session.commit()
    adapter=VariableAdapter();_,token=drafts.preview(product,adapter);identity=drafts.confirm(token,True,adapter)
    assert identity.state=="verified"
    mappings=copy.deepcopy(drafts.evidence(identity)["variant_maps"]);packed=copy.deepcopy(adapter.packed)
    adapter.calls.clear();result,data=run(product,adapter,manual=True)
    assert result.state=="completed" and data["counts"]["up_to_date"]==1
    assert mappings==drafts.evidence(identity)["variant_maps"] and packed==adapter.packed
    assert not any("write" in call or "create" in call or "update" in call for call in adapter.calls)


def test_budget_yield_retains_queue_and_does_not_mark_read_failed(published,monkeypatch):
    product,row,adapter=published
    before=ShopifyProductComparison.query.one().summary_json
    def wait(*args):raise capacity.BackgroundYield("Request budget reached")
    adapter.read_simple=wait
    result,data=run(product,adapter,manual=True)
    assert result.state=="paused" and data["queue"]==[product.id]
    assert data["counts"]["checked"]==0 and ShopifyProductComparison.query.one().summary_json==before
    assert tasks.claim("another") is None


def test_interactive_operation_has_priority(published):
    product,row,adapter=published
    lease=acquire_catalogue_operation("shopify_draft_publish",{"product_id":product.id})
    result,data=run(product,adapter,manual=True)
    assert result.state=="paused" and not adapter.calls
    assert "Interactive" in data["reason"]
    finish_catalogue_operation(lease.id,status="failed")


def test_capacity_uses_actual_cost_and_forbids_background_writes(published):
    config=drafts.configuration();db.session.query(ShopifyApiCapacity).delete();db.session.commit()
    with capacity.background_work(lambda:None,requests=2) as budget:
        capacity.observed(config,{"requestedQueryCost":30,"throttleStatus":{"currentlyAvailable":50,"maximumAvailable":1000,"restoreRate":100}})
        with pytest.raises(capacity.BackgroundYield,match="reserved"):capacity.before(config)
        with pytest.raises(capacity.BackgroundYield,match="forbidden"):capacity.before(config,writing=True)
        assert budget["requests"]==0
    observed=db.session.get(ShopifyApiCapacity,config.fingerprint())
    assert observed.available==50 and observed.restore_rate==100


def test_capacity_priority_survives_database_connection_change(published):
    capacity.signal_priority();db.session.remove()
    with capacity.background_work(lambda:None):
        with pytest.raises(capacity.BackgroundYield,match="interactive"):capacity.before(drafts.configuration())


def test_manual_priority_cannot_downgrade_publishing_demand(published):
    capacity.signal_priority(2);capacity.signal_priority(1);db.session.remove()
    assert db.session.get(ShopifyApiCapacity,drafts.configuration().fingerprint()).priority==2


def test_background_request_budget_is_bounded(published):
    db.session.query(ShopifyApiCapacity).delete();db.session.commit()
    with capacity.background_work(lambda:None,requests=1) as budget:
        capacity.before(drafts.configuration())
        with pytest.raises(capacity.BackgroundYield,match="budget"):capacity.before(drafts.configuration())
        assert budget["requests"]==1


def test_real_read_throttling_remains_bounded(published,isolated_transport,monkeypatch):
    from app.shopify_connection import COUNTRIES_QUERY, ShopifyReadThrottled
    db.session.query(ShopifyApiCapacity).delete();db.session.commit()
    session=Session(Response(status=429));waits=[]
    monkeypatch.setattr("app.shopify_connection.sleep",lambda delay:waits.append(delay))
    with capacity.background_work(lambda:None) as budget:
        with pytest.raises(ShopifyReadThrottled):ShopifyAdapter()._read(drafts.configuration(),COUNTRIES_QUERY,session=session)
        assert budget["requests"]==3 and budget["throttled"]==3
    assert len(session.calls)==3 and len(waits)==2 and sum(waits)<=3


def test_cost_failure_cannot_hide_interactive_receipt(published,monkeypatch):
    from sqlalchemy.exc import OperationalError
    monkeypatch.setattr(db.engine,"begin",lambda: (_ for _ in ()).throw(OperationalError("capacity",{},Exception())))
    capacity.observed(drafts.configuration(),{"requestedQueryCost":5,"throttleStatus":{"currentlyAvailable":900,"maximumAvailable":1000,"restoreRate":100}})


def test_empty_catalogue_completes_without_credentials_or_network(preview_app,monkeypatch):
    with preview_app.app_context():
        Product.query.update({Product.catalogue_status:"missing"});db.session.commit()
        monkeypatch.setattr(drafts,"configuration",lambda:pytest.fail("Empty local catalogue fetched credentials"))
        run_id=tasks.enqueue(manual=True);tasks.claim("owner");tasks.run_slice(tasks.KEY,run_id,"owner")
        result=db.session.get(ScheduledTaskRun,run_id)
        assert result.state=="completed" and json.loads(result.data_json)["counts"]["checked"]==0


def test_repeated_budget_deferral_is_bounded_and_preserves_evidence(published,monkeypatch):
    product,identity,adapter=published;before=ShopifyProductComparison.query.one().summary_json
    adapter.read_simple=lambda *args: (_ for _ in ()).throw(capacity.BackgroundYield("Request budget reached"))
    run_id=tasks.enqueue(manual=True,product_ids=[product.id])
    for _ in range(3):
        tasks.claim("owner");tasks.run_slice(tasks.KEY,run_id,"owner",adapter=adapter)
        task=db.session.get(ScheduledTask,tasks.KEY)
        if task.active_run_id:
            task.resume_at=tasks.now()-timedelta(seconds=1);db.session.commit()
    result=db.session.get(ScheduledTaskRun,run_id);data=json.loads(result.data_json)
    assert result.state=="partially_completed" and data["deferred_ids"]==[product.id]
    assert data["counts"]["deferred"]==1 and ShopifyProductComparison.query.one().summary_json==before


def test_concurrent_run_now_returns_one_durable_run(preview_app):
    def enqueue(_):
        with preview_app.app_context():return tasks.enqueue(manual=True)
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(enqueue,range(2)))
    assert results[0]==results[1]
    with preview_app.app_context():assert ScheduledTaskRun.query.count()==1


def test_independent_processes_claim_only_one_owner(preview_app):
    with preview_app.app_context():
        run_id=tasks.enqueue(manual=True);url=str(db.engine.url);db.session.remove()
    code="from flask import Flask\nfrom app import db\nfrom app.scheduled_tasks import claim\nimport sys,json\na=Flask('isolated');a.config['SQLALCHEMY_DATABASE_URI']=sys.argv[1];db.init_app(a)\nwith a.app_context(): print(json.dumps(claim(sys.argv[2])))"
    children=[subprocess.Popen([sys.executable,"-c",code,url,str(i)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for i in range(2)]
    results=[]
    for child in children:
        stdout,stderr=child.communicate(timeout=15);assert child.returncode==0,stderr
        results.append(json.loads(stdout))
    assert sum(value is not None for value in results)==1


def test_expired_owner_recovers_without_backlog_or_duplicate_run(preview_app):
    with preview_app.app_context():
        task=db.session.get(ScheduledTask,tasks.KEY);task.next_due=tasks.now()-timedelta(days=2);db.session.commit()
        run_id=tasks.enqueue();assert tasks.enqueue()==run_id
        tasks.claim("old")
        task.lease_until=tasks.now()-timedelta(seconds=1);db.session.commit()
        assert tasks.claim("new")== (tasks.KEY,run_id)
        with pytest.raises(capacity.BackgroundYield):tasks.renew(tasks.KEY,run_id,"old")
        assert ScheduledTaskRun.query.count()==1
        assert json.loads(db.session.get(ScheduledTaskRun,run_id).data_json)["restarts"]==1
        assert task.next_due>tasks.now()


def test_disabled_not_automatically_enqueued_but_manual_allowed(preview_app):
    with preview_app.app_context():
        tasks.configure(tasks.KEY,enabled=False,interval=10)
        task=db.session.get(ScheduledTask,tasks.KEY);task.next_due=tasks.now()-timedelta(minutes=1);db.session.commit()
        assert tasks.enqueue() is None and tasks.enqueue(manual=True)


def test_task_pages_are_authenticated_csrf_protected_and_read_only(preview_app,published,monkeypatch):
    monkeypatch.setattr(shopify_status,"refresh",lambda *a,**k:pytest.fail("Page load initiated comparison"))
    monkeypatch.setattr("app.utils.discord.requests.post",lambda *a,**k:pytest.fail("Read-only page sent Discord"))
    assert preview_app.test_client().get("/settings/tasks").status_code==401
    from flask import g
    g.pop("_login_user",None)  # world fixture holds an app context across simulated clients
    client=_client(preview_app)
    assert client.get("/settings/tasks").status_code==200
    assert client.get("/products").status_code==200
    assert client.get("/api/edit_products").status_code==200
    accepted=client.post("/settings/tasks/shopify_products/run",json={"product_ids":[published[0].id]})
    assert accepted.status_code==202 and accepted.json["state"]=="queued"
    assert client.post("/settings/tasks/shopify_products/run",json={}).json["run_id"]==accepted.json["run_id"]
    assert client.get(accepted.json["history_url"]).status_code==200
    preview_app.config["WTF_CSRF_ENABLED"]=True
    assert client.post("/settings/tasks/shopify_products/run",json={}).status_code==400


def test_recent_reviewed_update_is_skipped_without_waiting_for_scheduler(published,world):
    product,identity,adapter=published
    change(world,weight=52)
    _,token=drafts.preview(product,adapter)
    assert drafts.confirm(token,True,adapter).state=="verified"
    verified_at=identity.verified_at
    adapter.calls.clear();result,data=run(product,adapter)
    assert result.state=="completed" and data["counts"]["skipped_recent"]==1
    assert identity.verified_at==verified_at and not adapter.calls


def test_owner_loss_cannot_commit_progress_or_comparison(published):
    product,identity,adapter=published
    run_id=tasks.enqueue(manual=True);tasks.claim("old")
    task=db.session.get(ScheduledTask,tasks.KEY);task.lease_until=tasks.now()-timedelta(seconds=1);db.session.commit()
    tasks.claim("new");before=ShopifyProductComparison.query.one().summary_json
    with capacity.background_work(lambda:tasks.renew(tasks.KEY,run_id,"old")):
        with pytest.raises(capacity.BackgroundYield):
            from app.shopify_comparison import store_summary
            store_summary(ShopifyProductComparison.query.one(),{"classification":"no_change"})
    assert ShopifyProductComparison.query.one().summary_json==before


def test_commit_fence_rechecks_owner_after_slow_preparation(published):
    product,identity,adapter=published
    run_id=tasks.enqueue(manual=True);tasks.claim("old")
    saved=ShopifyProductComparison.query.one();before=saved.summary_json
    # Simulate preparation finishing after a different durable owner took over.
    task=db.session.get(ScheduledTask,tasks.KEY);task.lease_until=tasks.now()-timedelta(seconds=1);db.session.commit()
    tasks.claim("new")
    with capacity.background_work(lambda:None,fence=lambda:tasks.fence(tasks.KEY,run_id,"old")):
        with pytest.raises(capacity.BackgroundYield):
            from app.shopify_comparison import store_summary
            store_summary(saved,{"classification":"no_change"})
    assert ShopifyProductComparison.query.one().summary_json==before


@pytest.mark.parametrize("kind",["graphql","binary_upload"])
def test_real_transport_blocks_any_background_mutation_before_http(published,isolated_transport,monkeypatch,kind):
    from app.shopify_connection import UPDATE_QUERY
    monkeypatch.setattr("app.shopify_connection.requests.Session",lambda:pytest.fail("Mutation reached HTTP"))
    with capacity.background_work(lambda:None):
        with pytest.raises(capacity.BackgroundYield,match="forbidden"):
            if kind=="graphql":ShopifyAdapter()._write(drafts.configuration(),UPDATE_QUERY,{"product":{"id":"gid://shopify/Product/100"}})
            else:ShopifyAdapter().upload_image(drafts.configuration(),{}, {},b"not uploaded")


def test_recovery_releases_only_abandoned_scheduled_operation(preview_app):
    with preview_app.app_context():
        run_id=tasks.enqueue(manual=True);tasks.claim("old")
        op=CatalogueOperation(id="dead",operation_type="shopify_status_refresh",status="running",scope=json.dumps({"scheduled_run":run_id,"scheduled_owner":"old"}))
        db.session.add(op);db.session.commit()
        task=db.session.get(ScheduledTask,tasks.KEY);task.lease_until=tasks.now()-timedelta(seconds=1);db.session.commit()
        tasks.claim("new")
        assert op.status=="interrupted" and op.recovery_state=="none"


def test_task_partial_result_does_not_advance_last_success(published):
    product,identity,adapter=published
    good,data=run(product,adapter,manual=True)
    success=db.session.get(ScheduledTask,tasks.KEY).last_successful
    adapter.read_simple=lambda *args: (_ for _ in ()).throw(ShopifyError("Read unavailable"))
    partial,data=run(product,adapter,manual=True)
    assert partial.state=="partially_completed"
    assert db.session.get(ScheduledTask,tasks.KEY).last_successful==success


def test_successful_scheduled_run_is_silent_and_history_persists(published,monkeypatch):
    product,row,adapter=published
    monkeypatch.setattr("app.utils.discord.requests.post",lambda *a,**k:pytest.fail("Routine task sent Discord"))
    result,data=run(product,adapter,manual=True)
    run_id=result.id;db.session.remove()
    result=db.session.get(ScheduledTaskRun,run_id)
    assert result.state=="completed" and result.completed_at and result.duration_seconds is not None
    task=db.session.get(ScheduledTask,tasks.KEY)
    assert task.last_successful==result.completed_at and tasks.snapshot(task)["progress"]["up_to_date"]==1
    assert json.loads(ShopifyProductComparison.query.one().summary_json)["classification"]=="no_change"


def test_fresh_and_upgrade_migration_preserves_existing_data(tmp_path):
    path=tmp_path/"upgrade.db";url="sqlite:///"+str(path);config=_alembic_config(url)
    command.upgrade(config,"0012_shopify_attributes")
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO shopify_attribute_identity (store_id,source_key,state,evidence_json) VALUES ('shop','attribute:test','verified','{}')")
    ensure_database(url)
    with sqlite3.connect(path) as conn:
        from app.database import migration_head
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]==migration_head()
        assert conn.execute("SELECT source_key,state FROM shopify_attribute_identity").fetchone()==('attribute:test','verified')
        assert conn.execute("SELECT enabled,interval_minutes FROM scheduled_task").fetchone()==(1,20)
    fresh=tmp_path/"fresh.db";ensure_database("sqlite:///"+str(fresh))
    with sqlite3.connect(fresh) as conn:assert conn.execute("SELECT COUNT(*) FROM scheduled_task_run").fetchone()[0]==0


def test_retention_protects_active_runs(preview_app):
    with preview_app.app_context():
        stamp=tasks.now()
        db.session.add_all([ScheduledTaskRun(id=f"{i:032d}",task_key=tasks.KEY,trigger="scheduled",state="completed",created_at=stamp-timedelta(days=91),completed_at=stamp-timedelta(days=91),data_json="{}") for i in range(2)])
        active=tasks.enqueue(manual=True)
        tasks.prune_history()
        assert ScheduledTaskRun.query.count()==1 and db.session.get(ScheduledTaskRun,active)


def test_scoped_manual_check_preserves_catalogue_sweep_cursor(preview_app):
    with preview_app.app_context():
        task=db.session.get(ScheduledTask,tasks.KEY)
        product=Product.query.first()
        task.selection_cursor=product.id+1000
        db.session.commit()
        data={"scope":[product.id],"counts":{k:0 for k in tasks.COUNTERS}}
        tasks.select_products(task,data)
        assert data["queue"]==[product.id]
        assert task.selection_cursor==product.id+1000


def test_scoped_partial_failure_cannot_rewind_catalogue_sweep(preview_app):
    with preview_app.app_context():
        product=Product.query.first()
        task=db.session.get(ScheduledTask,tasks.KEY)
        task.selection_cursor=product.id+1000;db.session.commit()
        run_id=tasks.enqueue(manual=True,product_ids=[product.id]);tasks.claim("owner")
        run=db.session.get(ScheduledTaskRun,run_id);data=json.loads(run.data_json)
        data.update(queue=[product.id],initialized=True)
        tasks.save_progress(tasks.KEY,run_id,"owner",data,terminal="partially_completed")
        assert db.session.get(ScheduledTask,tasks.KEY).selection_cursor==product.id+1000


def test_selection_windows_reach_more_than_one_thousand_parents_and_wrap(preview_app):
    with preview_app.app_context():
        Product.query.update({Product.catalogue_status:"missing"})
        db.session.add_all([Product(sku=f"FAIR-{i:04d}",title="Fictional",product_type="simple",
            catalogue_status="active",source_relpath=f"Fair/Product {i}") for i in range(1005)])
        db.session.commit()
        task=db.session.get(ScheduledTask,tasks.KEY);task.selection_cursor=0
        def selected():
            data={"scope":None,"counts":{k:0 for k in tasks.COUNTERS}}
            tasks.select_products(task,data);db.session.commit();return data
        first=selected();second=selected();third=selected()
        assert len(first["queue"])==1000 and first["overflow"]==5
        assert len(second["queue"])==5 and second["overflow"]==0
        assert set(first["queue"]).isdisjoint(second["queue"])
        assert third["queue"]==first["queue"]


def test_partial_run_resumes_lowest_unfinished_product_after_restart(preview_app):
    with preview_app.app_context():
        task=db.session.get(ScheduledTask,tasks.KEY)
        ids=[p.id for p in Product.query.filter_by(catalogue_status="active").order_by(Product.id)]
        assert len(ids)>=2
        run_id=tasks.enqueue(manual=True);tasks.claim("owner")
        data=json.loads(db.session.get(ScheduledTaskRun,run_id).data_json)
        tasks.select_products(task,data)
        data["queue"]=ids[1:];data["counts"]["checked"]=1
        tasks.save_progress(tasks.KEY,run_id,"owner",data,terminal="partially_completed")
        db.session.remove()
        task=db.session.get(ScheduledTask,tasks.KEY)
        next_data={"scope":None,"counts":{k:0 for k in tasks.COUNTERS}}
        tasks.select_products(task,next_data)
        assert next_data["queue"][0]==ids[1]
