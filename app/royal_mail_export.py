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
from app.sale_pricing import project as selling_price, variant_sale

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
    details = []
    def note(label, reason):
        if len(details) < 6:
            details.append(f'{str(label)[:80]}: {reason[:160]}')
    def skip(label, reason):
        warnings['Skipped products/rows'] += 1
        note(label, reason)
    country_map = country_names()
    with db.session.no_autoflush:
        # published is the scanner's persisted LOCAL Published column, not a
        # Shopify observation. Exclude Draft/unknown before loading their views,
        # variations, scan receipts or validating export evidence.
        products = Product.query.filter_by(catalogue_status='active', published=True).options(
            selectinload(Product.variations).selectinload(Variation.attributes)).order_by(Product.id).all()
        unknown = Product.query.filter_by(catalogue_status='active', published=None).count()
        if unknown: warnings['Unknown local Draft/Live intent (excluded)'] += unknown
        jobs = {j.product_id: j for j in ShopifyPublishJob.query.filter(
            ShopifyPublishJob.product_id.in_([p.id for p in products])).all()}
        eligible = []
        for product in products:
            job = jobs.get(product.id)
            try:
                saved = json.loads(job.view_json) if job and job.view_json else {}
            except (TypeError, ValueError):
                saved = {}
            authored = saved.get('authored') if isinstance(saved, dict) else None
            # Older scanner rows defaulted Published=1 when intent was absent.
            # Require explicit resolved local intent too; never infer Live from
            # that default, Shopify actual state, queue state or sync health.
            if isinstance(authored, dict) and (authored.get('live') is False or
                    authored.get('shopify_status') in ('DRAFT', 'ARCHIVED')):
                continue
            if not isinstance(authored, dict) or not (
                    authored.get('live') is True or
                    'live' not in authored and authored.get('shopify_status') == 'ACTIVE') or (
                    authored.get('shopify_status') not in (None, 'ACTIVE')):
                skip(product.sku or product.title, 'explicit saved local Live intent is unavailable')
                continue
            children = [v for v in product.variations if v.catalogue_status == 'active' and v.visible is not False]
            eligible.append((product, job, saved, authored, children if product.product_type == 'variable' else [product]))
        if not eligible:
            raise ValueError('No locally Live products are available for export. Draft and unknown-intent products are excluded.'
                             + (' ' + '; '.join(details) if details else ''))
        # Count identities before row validation: neither side of an ambiguous
        # duplicate is exported, even if one also has incomplete source evidence.
        counts = Counter(item.sku.casefold() for *_, items in eligible for item in items
                         if isinstance(item.sku, str) and item.sku.strip())
        scan_ids = {job.scan_operation_id for _, job, *_ in eligible if job.scan_operation_id}
        scans = {o.id: o.status for o in CatalogueOperation.query.filter(CatalogueOperation.id.in_(scan_ids)).all()}
        refreshes = []
        for op in CatalogueOperation.query.filter(CatalogueOperation.status.in_(['running', 'pending']),
                CatalogueOperation.operation_type.in_(['append', 'product_update', 'full',
                    'shared_collection_update', 'reconstruction', 'intake_catalogue_handoff'])).all():
            try: scope = json.loads(op.scope or '{}')
            except (TypeError, ValueError): scope = {}
            folder = scope.get('collection_relpath') if isinstance(scope, dict) else None
            refreshes.append(folder if isinstance(folder, str) else None)
        rows = []
        for product, job, saved, authored, items in eligible:
            if any(not folder or (product.source_relpath or '') == folder or
                    (product.source_relpath or '').startswith(folder + '/') for folder in refreshes):
                skip(product.sku or product.title, 'local catalogue refresh is in progress; wait for completion')
                continue
            # view_pending is a Shopify *status* refresh flag, also set by
            # readback/recovery events. It does not invalidate an otherwise
            # complete, scan-bound local export snapshot. Actual local refresh
            # ownership/revision/checkpoint checks remain mandatory below.
            if (not job or job.view_revision != job.requested_revision
                    or scans.get(job.scan_operation_id) != 'succeeded'
                    or not isinstance(authored, dict) or not authored.get('collection_type')
                    or not product.resolved_row_json):
                skip(product.sku or product.title, 'completed saved local catalogue evidence is unavailable or refreshing; review its existing source/collection refresh')
                continue
            if product.product_type == 'variable' and not items:
                skip(product.sku or product.title, 'variable product has no saved sellable variants')
                continue
            if product.product_type not in {'simple', 'variable'}:
                skip(product.sku or product.title, 'unsupported local product type')
                continue
            for item in sorted(items, key=lambda v: (v.menu_order or 0, v.id)):
                sku = item.sku
                label = f'{sku or product.title} (product {product.id}' + (f', variant {item.id})' if item is not product else ')')
                if not isinstance(sku, str) or not sku.strip():
                    skip(label, 'missing sellable SKU; no identity generated')
                    continue
                if sku != sku.strip() or sku.startswith(('=', '+', '-', '@')) or any(ord(c) < 32 for c in sku):
                    skip(label, 'unsafe or whitespace-padded SKU; no identity changed')
                    continue
                if counts[sku.casefold()] > 1:
                    skip(label, 'duplicate sellable SKU; all conflicting rows excluded')
                    continue
                price = ''
                regular = authored.get('price', item.regular_price) if item is product else item.regular_price
                if regular not in (None, ''):
                    try:
                        sale = authored.get('sale_price', item.sale_price) if item is product else variant_sale(
                            authored, {a.name:a.value for a in item.attributes}, item.sale_price)
                        price = selling_price(dict(authored, sale_price=sale), regular, at=at)['price']
                    except (ValueError, TypeError, ArithmeticError):
                        warnings['Invalid selling price/schedule (left blank)'] += 1
                        note(label, 'invalid local price/schedule left blank')
                if not price:
                    warnings['Missing selling price'] += 1
                weight = ''
                if item.weight is not None:
                    # The scanner/SQLite contract is Weight (g), including
                    # resolved variant modifiers. Never include package tare.
                    try: weight = format((decimal_value(item.weight, f'{sku} weight') / Decimal(1000)).normalize(), 'f')
                    except ValueError:
                        warnings['Invalid product weight (left blank)'] += 1
                        note(label, 'invalid local weight left blank')
                else:
                    warnings['Missing product weight'] += 1
                description = authored.get('customs_description') or ''
                if not isinstance(description, str):
                    description = ''
                    warnings['Invalid Customs Description (left blank)'] += 1
                    note(label, 'invalid saved Customs Description left blank')
                hs = authored.get('harmonized_system_code') or ''
                origin = authored.get('country_of_origin') or ''
                if hs and (not isinstance(hs, str) or not re.fullmatch('[0-9]{6,13}', hs)):
                    hs = ''
                    warnings['Invalid HS Code (left blank)'] += 1
                    note(label, 'invalid saved HS Code left blank')
                country = country_map.get(origin, '') if isinstance(origin, str) else ''
                for field_label, value in [('Customs Description', description), ('HS Code', hs), ('Country of Origin', country)]:
                    if not value:
                        warnings['Missing ' + field_label] += 1
                if origin and not country:
                    warnings['Unrecognised Country of Origin (left blank)'] += 1
                name = saved.get('display_title') or product.title
                if item is not product:
                    try:
                        variant_row = json.loads(item.resolved_row_json or '{}')
                        if not isinstance(variant_row, dict): raise ValueError()
                        name = variant_row.get('Name') or name
                    except (TypeError, ValueError):
                        skip(label, 'saved variant snapshot is invalid')
                        continue
                if not isinstance(name, str) or not name.strip():
                    skip(label, 'resolved product name is unavailable')
                    continue
                if len(name) > 100:
                    warnings['Product names over Royal Mail’s 100-character editor limit (not truncated)'] += 1
                rows.append([sku, text_cell(name, warnings), price, weight, text_cell(description, warnings), hs, sku, country])
                if len(rows) > 20000:
                    raise ValueError('Royal Mail Products import supports at most 20,000 rows per file. No rows were truncated.')
    if not rows:
        raise ValueError('No exportable locally Live product rows. ' + '; '.join(details))
    if details:
        warnings['Details — ' + '; '.join(details)] = 1
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(COLUMNS)
    writer.writerows(rows)
    data = output.getvalue().encode('utf-8')
    if len(data) > 20 * 1024 * 1024:
        raise ValueError('CSV exceeds Royal Mail’s 20 MB product-import limit. No rows were truncated.')
    return data, filename, len(rows), dict(warnings)
