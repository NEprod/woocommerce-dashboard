"""Read-only Click & Drop Products CSV from completed saved local projections.

No source reads, scans, remote requests, packaging weights or ORM writes.
"""
import csv
import io
import json
import re
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy.orm import selectinload

from app import db
from app.models import Product, Variation, ShopifyPublishJob, CatalogueOperation
from app.sale_pricing import catalogue as selling_prices

COLUMNS = ('SKU', 'Product Name', 'Price (GBP)', 'Weight (KG)', 'Customs Description',
           'HS Code', 'Non-standardised Manufacturer ID', 'Country of Origin')
MANUFACTURER_NOTE = ('Non-standardised Manufacturer ID is not listed in Royal Mail’s Products import mapping. '
                     'Leave this column unmapped; enter its SKU value in the product’s EU manufacturer ID field separately.')


def country_names():
    # Public-domain IANA ISO table, bundled so export needs no network/dependency.
    lines = (Path(__file__).parent / 'resources/iso3166.tab').read_text(encoding='utf-8').splitlines()
    names = dict(line.split('\t', 1) for line in lines if line and not line.startswith('#'))
    names.update(GB='United Kingdom', KP='North Korea', KR='South Korea',
                 AS='American Samoa', CD='Democratic Republic of the Congo',
                 CG='Republic of the Congo', FK='Falkland Islands',
                 PS='Palestine', VC='Saint Vincent and the Grenadines',
                 VI='United States Virgin Islands', VG='British Virgin Islands')
    return names


def decimal_value(value, label):
    try:
        result = Decimal(str(value))
        if isinstance(value, bool) or not result.is_finite() or result < 0:
            raise ValueError()
        return result
    except (ValueError, InvalidOperation):
        raise ValueError(f'{label} is invalid in the saved local catalogue. Review its source and existing scoped refresh.') from None


def text_cell(value, warnings):
    value = str(value or '')
    # CSV quoting alone does not prevent spreadsheet formula execution. Preserve
    # ordinary apostrophes/Unicode. Only dangerous text cells gain a quote prefix.
    if value.lstrip().startswith(('=', '+', '-', '@')) or any(c in value for c in '\t\r\n'):
        warnings['Spreadsheet-safe text cells (prefixed with apostrophe)'] += 1
        return "'" + value
    return value


def generate(*, at=None):
    at = at or datetime.now(ZoneInfo('Europe/London'))
    if at.tzinfo:
        at = at.astimezone(ZoneInfo('Europe/London'))
    filename = f'tlc-royal-mail-products-{at.date().isoformat()}.csv'
    warnings = Counter()
    country_map = country_names()
    with db.session.no_autoflush:
        if CatalogueOperation.query.filter(CatalogueOperation.status.in_(['running', 'pending']),
                CatalogueOperation.operation_type.in_(['append', 'product_update', 'full',
                    'shared_collection_update', 'reconstruction', 'intake_catalogue_handoff'])).first():
            raise ValueError('Local catalogue refresh is in progress. Wait for its completion before exporting.')
        products = Product.query.filter_by(catalogue_status='active').options(
            selectinload(Product.variations).selectinload(Variation.attributes)).order_by(Product.id).all()
        jobs = {j.product_id: j for j in ShopifyPublishJob.query.all()}
        scan_ids = {j.scan_operation_id for j in jobs.values() if j.scan_operation_id}
        scans = {o.id: o.status for o in CatalogueOperation.query.filter(CatalogueOperation.id.in_(scan_ids)).all()}
        rows, seen = [], set()
        for product in products:
            job = jobs.get(product.id)
            try:
                saved = json.loads(job.view_json) if job and job.view_json else {}
            except (TypeError, ValueError):
                saved = {}
            authored = saved.get('authored')
            # view_pending is a Shopify *status* refresh flag, also set by
            # readback/recovery events. It does not invalidate an otherwise
            # complete, scan-bound local export snapshot. Actual local refresh
            # ownership/revision/checkpoint checks remain mandatory below.
            if (not job or job.view_revision != job.requested_revision
                    or scans.get(job.scan_operation_id) != 'succeeded'
                    or not isinstance(authored, dict) or not authored.get('collection_type')
                    or not product.resolved_row_json):
                raise ValueError(f'{product.sku or product.id}: completed saved local catalogue evidence is unavailable or refreshing. '
                                 'Review the existing source/collection refresh; export does not scan or verify source files.')
            children = [v for v in product.variations if v.catalogue_status == 'active' and v.visible is not False]
            if product.product_type == 'variable' and not children:
                raise ValueError(f'{product.sku}: variable product has no saved sellable variants. Review its local source.')
            if product.product_type not in {'simple', 'variable'}:
                raise ValueError(f'{product.sku}: unsupported local product type for this product CSV.')
            prices = selling_prices(product, authored=authored, at=at)
            if prices['error']:
                raise ValueError(f'{product.sku}: {prices["error"]}')
            amounts = {v['sku']: v['price'] for v in prices['variants']}
            for item in sorted(children, key=lambda v: (v.menu_order or 0, v.id)) if product.product_type == 'variable' else [product]:
                sku = item.sku
                if not isinstance(sku, str) or not sku.strip():
                    raise ValueError(f'{product.sku or product.id}: missing sellable SKU. No SKUs were generated or rows omitted.')
                if sku != sku.strip() or sku.startswith(('=', '+', '-', '@')) or any(ord(c) < 32 for c in sku):
                    raise ValueError(f'Unsafe or whitespace-padded SKU {sku!r}; export cannot change an identity for spreadsheet safety.')
                key = sku.casefold()
                if key in seen:
                    raise ValueError(f'Duplicate sellable SKU: {sku}. Resolve the identity conflict; no rows were combined.')
                seen.add(key)
                price = amounts.get(sku, '')
                if not price:
                    warnings['Missing selling price'] += 1
                weight = ''
                if item.weight is not None:
                    # The scanner/SQLite contract is Weight (g), including
                    # resolved variant modifiers. Never include package tare.
                    weight = format((decimal_value(item.weight, f'{sku} weight') / Decimal(1000)).normalize(), 'f')
                else:
                    warnings['Missing product weight'] += 1
                description = authored.get('customs_description') or ''
                hs = authored.get('harmonized_system_code') or ''
                origin = authored.get('country_of_origin') or ''
                if hs and (not isinstance(hs, str) or not re.fullmatch('[0-9]{6,13}', hs)):
                    raise ValueError(f'{sku}: invalid saved HS Code; text of 6–13 digits is required.')
                country = country_map.get(origin, '')
                for label, value in [('Customs Description', description), ('HS Code', hs), ('Country of Origin', country)]:
                    if not value:
                        warnings['Missing ' + label] += 1
                if origin and not country:
                    warnings['Unrecognised Country of Origin (left blank)'] += 1
                name = saved.get('display_title') or product.title
                if item is not product:
                    try:
                        name = json.loads(item.resolved_row_json or '{}').get('Name') or name
                    except (TypeError, ValueError):
                        raise ValueError(f'{sku}: saved variant snapshot is invalid.') from None
                if len(name) > 100:
                    warnings['Product names over Royal Mail’s 100-character editor limit (not truncated)'] += 1
                rows.append([sku, text_cell(name, warnings), price, weight, text_cell(description, warnings), hs, sku, country])
                if len(rows) > 20000:
                    raise ValueError('Royal Mail Products import supports at most 20,000 rows per file. No rows were truncated.')
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(COLUMNS)
    writer.writerows(rows)
    data = output.getvalue().encode('utf-8')
    if len(data) > 20 * 1024 * 1024:
        raise ValueError('CSV exceeds Royal Mail’s 20 MB product-import limit. No rows were truncated.')
    return data, filename, len(rows), dict(warnings)
