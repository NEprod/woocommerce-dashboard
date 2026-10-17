"""Administrator-only task settings, durable queue acceptance and read-only history."""
import json
from flask import Blueprint, abort, request, jsonify, render_template, redirect, url_for
from flask_login import login_required, current_user
from flask_wtf import FlaskForm
from app import db
from app.models import ScheduledTask, ScheduledTaskRun
from app import scheduled_tasks as service

tasks=Blueprint("tasks",__name__)


@tasks.before_request
@login_required
def administrator():
    if not current_user.is_admin:abort(403)


@tasks.route("/settings/tasks")
def index():
    key=request.args.get('task',service.KEY)
    if key not in {service.KEY,service.SALE_KEY,service.ORDERS_KEY}: abort(404)
    return render_template("scheduled_tasks.html",task=service.snapshot(db.session.get(ScheduledTask,key)),form=FlaskForm())


@tasks.route("/settings/tasks/status")
def status():
    key=request.args.get('task',service.KEY)
    if key not in {service.KEY,service.SALE_KEY,service.ORDERS_KEY}: abort(404)
    return jsonify(service.snapshot(db.session.get(ScheduledTask,key))),200,{"Cache-Control":"no-store"}


@tasks.route("/settings/tasks/<key>",methods=["POST"])
def settings(key):
    data=request.get_json(silent=True) if request.is_json else request.form
    try:
        if not data:raise ValueError("Task settings are required.")
        enabled=data.get("enabled") if request.is_json else data.get("enabled")=="yes"
        raw=data.get("interval")
        interval=raw if request.is_json and type(raw) is int else int(raw) if not request.is_json and isinstance(raw,str) and raw in {"5","10","20","30","60"} else None
        result=service.configure(key,enabled=enabled,interval=interval)
    except (ValueError,TypeError):
        return jsonify(error="Choose an enabled state and a supported interval (sale tasks also allow five minutes)."),400
    return jsonify(result) if request.is_json else redirect(url_for("tasks.index"))


@tasks.route("/settings/tasks/<key>/run",methods=["POST"])
def run_now(key):
    data=request.get_json(silent=True) or {}
    try:run_id=service.enqueue(key,manual=True,product_ids=data.get("product_ids"))
    except ValueError as exc:return jsonify(error=str(exc)),400
    task=service.snapshot(db.session.get(ScheduledTask,key))
    return jsonify(**task,history_url=url_for("tasks.run_detail",run_id=run_id)),202,{"Cache-Control":"no-store"}


@tasks.route("/settings/tasks/<key>/history")
def history(key):
    if key not in {service.KEY,service.SALE_KEY,service.ORDERS_KEY,'catalogue_startup'}:abort(404)
    runs=ScheduledTaskRun.query.filter_by(task_key=key).order_by(ScheduledTaskRun.created_at.desc()).limit(100).all()
    return render_template("scheduled_task_history.html",runs=runs,detail=None,definition=service.definition(key))


@tasks.route("/settings/tasks/runs/<run_id>")
def run_detail(run_id):
    run=db.session.get(ScheduledTaskRun,run_id)
    if not run:abort(404)
    data=json.loads(run.data_json)
    if request.accept_mimetypes.best=="application/json":
        return jsonify(id=run.id,state=run.state,counts=data.get("counts",{}),reason=data.get("reason"),
            error=run.error,diagnostic=data.get('diagnostic'),auth_requests=data.get('auth_requests',0),
            order_results={k:data.get('orders_'+k,0) for k in ('created','updated','unchanged','stale')},
            outstanding=data.get('pending_orders',len(data.get("queue",[]))),items=data.get("items",[])),200,{"Cache-Control":"no-store"}
    return render_template("scheduled_task_history.html",runs=[run],detail=data,definition=service.definition(run.task_key))
