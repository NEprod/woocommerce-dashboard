"""OPTIONAL isolated WSGI gateway. Not registered on the Dashboard or started by it.

Deploy only after merchant approval, with dedicated HTTPS and private durable disk.
It has NO Orders/Admin API client, catalogue access or dashboard authentication.
"""
import hashlib
import hmac
import json
import os
import sqlite3
import time
from pathlib import Path
from flask import Flask, request, abort, jsonify
from app.order_webhook_security import MAX_BODY, verified, seal


def create_gateway(config=None):
    config=config or {k:os.environ.get('TLC_WEBHOOK_'+k,'') for k in ('DATABASE','SECRET','DOMAIN','KEY','TOKEN')}
    if not all(config.get(k) for k in ('DATABASE','SECRET','DOMAIN','KEY','TOKEN')) or len(config['TOKEN'])<32:
        raise ValueError('Dedicated webhook gateway configuration is incomplete.')
    from cryptography.fernet import Fernet
    Fernet(config['KEY'].encode())
    path=Path(config['DATABASE'])
    if not path.is_absolute() or not path.parent.is_dir():raise ValueError('Use a prepared private persistent gateway directory.')
    fd=os.open(path,os.O_CREAT|os.O_RDWR,0o600);os.close(fd);os.chmod(path,0o600)
    app=Flask('tlc-orders-gateway');app.config['MAX_CONTENT_LENGTH']=MAX_BODY
    def connect():
        conn=sqlite3.connect(path,timeout=3)
        conn.execute('PRAGMA journal_mode=WAL')
        return conn
    with connect() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS event (id TEXT PRIMARY KEY, topic TEXT NOT NULL, digest TEXT NOT NULL, envelope TEXT, received REAL NOT NULL, acknowledged INTEGER NOT NULL DEFAULT 0, order_gid TEXT, source_updated TEXT)')
        conn.execute('CREATE TABLE IF NOT EXISTS request_limit (id INTEGER PRIMARY KEY CHECK(id=1), second INTEGER, count INTEGER)')
    @app.before_request
    def limit():
        with connect() as conn:
            second=int(time.time())
            conn.execute('INSERT INTO request_limit VALUES(1,?,1) ON CONFLICT(id) DO UPDATE SET second=excluded.second,count=CASE WHEN second=excluded.second THEN count+1 ELSE 1 END',(second,))
            if conn.execute('SELECT count FROM request_limit').fetchone()[0]>60:abort(429)
            conn.execute('UPDATE event SET envelope=NULL WHERE envelope IS NOT NULL AND received<?',(time.time()-7*86400,))
    @app.after_request
    def private(response):
        response.headers['Cache-Control']='no-store';return response
    def authorised():
        if not hmac.compare_digest(request.headers.get('Authorization',''),'Bearer '+config['TOKEN']):abort(401)
    @app.post('/webhooks/shopify/orders')
    def receive():
        body=request.get_data()
        try:raw,topic,delivery=verified(body,request.headers,secret=config['SECRET'],domain=config['DOMAIN'])
        except Exception:abort(401)
        from app.order_webhook_payload import gid,identity
        from app.shopify_orders import stamp
        order_gid=source_updated=None
        try:
            order_gid=gid(raw['order_id'],'Order') if topic=='refunds/create' else identity(raw,'Order')
            source_updated=stamp(raw.get('updated_at') or raw.get('created_at')).isoformat()+'Z'
        except (ValueError,KeyError,TypeError):pass
        digest=hashlib.sha256(body).hexdigest()
        envelope=seal(body,request.headers,config['KEY'])
        with connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            existing=conn.execute('SELECT digest,topic FROM event WHERE id=?',(delivery,)).fetchone()
            if existing:
                if existing!=(digest,topic):abort(409)
            else:
                if conn.execute('SELECT COUNT(*) FROM event WHERE acknowledged=0').fetchone()[0]>=10000:abort(503)
                conn.execute('INSERT INTO event(id,topic,digest,envelope,received,order_gid,source_updated) VALUES(?,?,?,?,?,?,?)',
                    (delivery,topic,digest,envelope,time.time(),order_gid,source_updated))
        return '',200
    @app.get('/events')
    def events():
        authorised()
        with connect() as conn:
            # Expiry is explicit and not acknowledged; private receiver gets an
            # expiry marker and requires manual catch-up instead of silent loss.
            conn.execute('UPDATE event SET envelope=NULL WHERE received<?',(time.time()-7*86400,))
            conn.execute('DELETE FROM event WHERE acknowledged=1 AND received<?',(time.time()-90*86400,))
            rows=conn.execute('SELECT id,envelope,topic,digest,order_gid,source_updated FROM event WHERE acknowledged=0 ORDER BY received,id LIMIT 1').fetchall()
        return jsonify([{'id':r[0],'envelope':r[1],'topic':r[2],'digest':r[3],'order_gid':r[4],'updated_at':r[5]} for r in rows])
    @app.post('/ack')
    def ack():
        authorised();value=request.get_json(silent=True)
        ids=value.get('ids') if isinstance(value,dict) else None
        if not isinstance(ids,list) or len(ids)>1 or any(not isinstance(i,str) or len(i)>128 for i in ids):abort(400)
        with connect() as conn:
            conn.executemany('UPDATE event SET acknowledged=1,envelope=NULL WHERE id=?',[(i,) for i in ids])
        return '',200
    return app
