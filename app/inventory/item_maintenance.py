"""Safe corrections of stock masters, never stock ledger edits."""
import json
import os
from pathlib import Path

from sqlalchemy import select
from app import db
from app.models import Settings, Product
from .models import RecipeMaterial


def audit(action, row, actor, **details):
    from datetime import UTC, datetime
    from uuid import uuid4
    from app.models import CatalogueOperation
    now = datetime.now(UTC).replace(tzinfo=None)
    db.session.add(CatalogueOperation(id=uuid4().hex, operation_type='inventory_item_maintenance',
        status='succeeded', started_at=now, finished_at=now,
        scope=json.dumps(dict(action=action, item_id=row.id, name=row.name,
            actor=str(actor.get_id()), actor_label=actor.username, **details))))


def _referenced(value, item_id):
    if isinstance(value, dict):
        return any((key in {'item_id', 'material_id', 'component_id', 'stock_item_id'}
                    and str(child) == str(item_id)) or _referenced(child, item_id)
                   for key, child in value.items())
    return isinstance(value, list) and any(_referenced(child, item_id) for child in value)


def _document(raw, item_id):
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        raise ValueError('A saved BOM or catalogue document cannot be checked. Repair it or archive this item instead.') from None
    return _referenced(value, item_id)


def delete_unused(row, data):
    from .service import _check_version
    _check_version(row, data)
    if data.get('confirmed') != 'yes' or data.get('confirm_code') != row.identity.code:
        raise ValueError('Enter the current barcode and confirm deletion of this unused item.')
    for table in db.metadata.sorted_tables:
        for column in table.columns:
            if any(fk.target_fullname == 'inventory_item.id' for fk in column.foreign_keys):
                if table.name == RecipeMaterial.__tablename__ and column.name == 'component_id':
                    continue  # Remove the unused component's own recipe with it.
                if db.session.execute(select(column).where(column == row.id).limit(1)).first():
                    raise ValueError('This item has stock history, batches, orders or recipe references. Archive it instead; history cannot be deleted.')
    documents = [('inventory_bom_scenario', 'document'), ('inventory_product_costing_profile', 'document'),
                 ('inventory_production_batch', 'planned_recipe'), ('order_work_line', 'bom_json'),
                 ('order_work_line', 'policy_json'), ('order_work_plan', 'document')]
    for name, field in documents:
        table = db.metadata.tables[name]
        if any(_document(raw, row.id) for raw in db.session.scalars(select(table.c[field]))):
            raise ValueError('This item is referenced by a saved scenario, production plan or order snapshot. Archive it instead.')
    settings = Settings.query.first()
    if settings and settings.product_folder:
        root = Path(settings.product_folder)
        if not root.is_dir():
            raise ValueError('Catalogue storage is unavailable. References cannot be checked; archive the item instead.')
        root = root.resolve()
        def fail(error):
            raise error
        try:
            for directory, folders, files in os.walk(root, followlinks=False, onerror=fail):
                if any((Path(directory) / folder).is_symlink() for folder in folders):
                    raise ValueError('Linked catalogue folders cannot be checked safely. Archive this item instead.')
                if 'product_info.json' not in files:
                    continue
                path = Path(directory) / 'product_info.json'
                if path.is_symlink() or path.stat().st_size > 1024 * 1024:
                    raise ValueError('A catalogue source cannot be checked safely. Archive this item instead.')
                if _document(path.read_text(), row.id):
                    raise ValueError('This item is referenced by catalogue JSON (BOM or workflow). Remove that reference first, or archive it.')
        except OSError:
            raise ValueError('Catalogue storage cannot be fully read. Archive this item instead.') from None
    elif db.session.scalar(select(Product.id).limit(1)):
        raise ValueError('Configure accessible catalogue storage before deleting inventory records.')
    # Reserve the internal ID/barcode so stale external references cannot ever
    # resolve to a different item through SQLite ID reuse.
    db.session.delete(row)
    db.session.flush()
