"""Release-only upgrade audit; never opens the source database for writing.

CLI: PYTHONPATH=tests:. python tests/test_order_release_migrations.py SOURCE.db
The source is backed up read-only. Synthetic order/ledger rows are added only to
the disposable 0027 copy, then every pre-existing row is compared through 28/29.
"""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from datetime import datetime
from decimal import Decimal
from alembic import command
from app.database import _alembic_config, _assert_phase0_schema, BASELINE_REVISION, ensure_database, migration_head
from test_database_migrations import _create_phase0_database
from test_shopify_orders import order, STORE
from app.shopify_orders import normalise


def backup(source, destination):
    with sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True) as original, sqlite3.connect(destination) as copied:
        original.backup(copied)
    destination.chmod(0o600)


def snapshot(path):
    with sqlite3.connect(path) as connection:
        tables=[r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {t:sorted(connection.execute(f'SELECT * FROM "{t}"').fetchall(),key=repr) for t in tables}


def insert(connection, table, **values):
    columns=connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    for _,name,kind,required,default,primary in columns:
        if name not in values and required and default is None and not primary:
            values[name]='2026-11-22 00:00:00' if 'DATE' in kind.upper() else '' if any(k in kind.upper() for k in ('CHAR','TEXT')) else 0
    known={r[1] for r in columns};values={k:v for k,v in values.items() if k in known}
    values={k:str(v) if isinstance(v,Decimal) else v.isoformat() if isinstance(v,datetime) else v for k,v in values.items()}
    names=','.join('"'+k+'"' for k in values)
    return connection.execute(f'INSERT INTO "{table}" ({names}) VALUES ({",".join("?" for _ in values)})',list(values.values())).lastrowid


def seed_copy(path):
    with sqlite3.connect(path) as c:
        c.execute('PRAGMA foreign_keys=ON')
        identity=insert(c,'inventory_identity',code='RELEASE-COPY-FIXTURE',kind='item')
        insert(c,'inventory_item',id=identity,kind='purchased',name='Disposable migration fixture',unit='piece',active=1,
               version=1,minimum=1000000,reorder=10000000,recipe='[]',pack_qty=1000000,pack_price=500000)
        tx='ed'*16
        insert(c,'inventory_transaction',id=tx,kind='opening',item_id=identity,actor='release-copy-test',
               created_at='2026-11-22 00:00:00',reason='Disposable audit fixture',details='{}',request_hash='a'*64)
        insert(c,'inventory_movement',transaction_id=tx,item_id=identity,location_id=0,qty=7000000,unit='piece',kind='opening',cost=3500000)
        raw=order(987654321);raw['_lines']=raw['lineItems']['nodes']
        header,lines=normalise(raw,api_version='2026-10')
        oid=insert(c,'shopify_order',store_id=STORE,**header)
        for row in lines:insert(c,'shopify_order_line',order_id=oid,**row)
        run='ef'*16
        insert(c,'scheduled_task_run',id=run,task_key='shopify_orders',trigger='manual',state='paused',
               created_at='2026-11-22 00:00:00',data_json='{"counts":{},"pending_orders":2}')
        c.execute("UPDATE scheduled_task SET enabled=0,interval_minutes=5,active_run_id=?,state='paused' WHERE key='shopify_orders'",(run,))
        assert not c.execute('PRAGMA foreign_key_check').fetchall()


def verify(source, directory):
    directory.mkdir(parents=True,exist_ok=True)
    source_stat=source.stat().st_mtime_ns,source.stat().st_size
    historical=directory/'existing-copy.db';backup(source,historical)
    with sqlite3.connect(historical) as c:
        unversioned=not c.execute("SELECT 1 FROM sqlite_master WHERE name='alembic_version'").fetchone()
        if unversioned:_assert_phase0_schema(c)
    if unversioned:command.stamp(_alembic_config(f'sqlite:///{historical}'),BASELINE_REVISION)
    command.upgrade(_alembic_config(f'sqlite:///{historical}'),'0027_production_batches')
    seed_copy(historical)
    upgraded=directory/'upgrade-copy.db';backup(historical,upgraded)
    before=snapshot(upgraded)
    command.upgrade(_alembic_config(f'sqlite:///{upgraded}'),'0028_order_fulfilment')
    middle=snapshot(upgraded)
    assert all(middle[t]==rows for t,rows in before.items() if t!='alembic_version')
    result=ensure_database(f'sqlite:///{upgraded}')
    assert result.revision==migration_head() and result.backup_path
    after=snapshot(upgraded)
    assert all(([(r[:len(rows[0])]) for r in after[t]] if t=='settings' and rows else after[t])==rows for t,rows in before.items() if t not in {'alembic_version','scheduled_task'})
    with sqlite3.connect(upgraded) as c:
        columns=[r[1] for r in c.execute('PRAGMA table_info(scheduled_task)')]
        for old,new in zip(before['scheduled_task'],after['scheduled_task']):
            a,b=dict(zip(columns,old)),dict(zip(columns,new))
            if a['key']=='shopify_orders':
                assert b['enabled']==1 and b['interval_minutes']==15 and b['state']=='paused'
                assert {k:v for k,v in a.items() if k not in {'enabled','interval_minutes','next_due'}}=={k:v for k,v in b.items() if k not in {'enabled','interval_minutes','next_due'}}
            else:assert a==b
        assert c.execute('PRAGMA integrity_check').fetchone()==('ok',)
        assert not c.execute('PRAGMA foreign_key_check').fetchall()
        for table in ('order_webhook_setting','order_integration_settings','order_status_projection','order_work_line','order_work_outbound'):
            assert c.execute(f'SELECT count(*) FROM {table}').fetchone()[0]==0
        c.execute("UPDATE scheduled_task SET enabled=0,interval_minutes=30 WHERE key='shopify_orders'")
    ensure_database(f'sqlite:///{upgraded}')
    with sqlite3.connect(upgraded) as c:assert c.execute("SELECT enabled,interval_minutes FROM scheduled_task WHERE key='shopify_orders'").fetchone()==(0,30)
    assert snapshot(historical)==before
    assert source_stat==(source.stat().st_mtime_ns,source.stat().st_size)
    return dict(revision=result.revision,legacy_tables_preserved=len(before)-2,
        inventory_items=len(before['inventory_item']),ledger_movements=len(before['inventory_movement']),
        orders=len(before['shopify_order']),order_lines=len(before['shopify_order_line']),
        paired_backup=True,source_unchanged=True,foreign_keys='ok',integrity='ok',outbound_authority='absent',
        other_schedules='unchanged',active_run='preserved',restart_operator_settings='preserved')


def test_0028_0029_preserve_existing_database_copy(tmp_path):
    source=tmp_path/'historical.db';_create_phase0_database(source)
    result=verify(source,tmp_path/'audit')
    assert result['orders']==1 and result['order_lines']==2 and result['ledger_movements']==1


if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='tlc-release-migration-') as directory:
        print(json.dumps(verify(Path(sys.argv[1]).resolve(),Path(directory)),sort_keys=True))
