"""Presentation-only regressions: zero categories, retained unknowns and totals."""
from copy import deepcopy
from pathlib import Path
import re

from flask import Flask, render_template
import pytest


def example():
    return dict(complete=True, missing=[], lines=[], selling_price='5.00', margin='50',
                subtotals=dict(raw_material='0.00', purchased_component='0.01',
                               manufactured_component='0.35', packaging='0.38'),
                buffer='0.04', buffer_rate='11', print_waste='0.00', print_waste_rate='11',
                electricity='0.21', labour='1.00', other='0.00', cost='1.99')


def render(result):
    app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1]/'app/templates'))
    with app.app_context():
        return render_template('inventory/_profitability.html', result=result)


def summary(html):
    return re.search(r'<table class="table bom-cost-summary">(.*?)</table>', html, re.S)[1]


@pytest.mark.parametrize('zero', ['0', '0.00', 0])
def test_zero_categories_hidden_nonzero_and_total_retained(zero):
    result = example()
    result['subtotals']['raw_material'] = result['print_waste'] = result['other'] = zero
    before = deepcopy(result)
    html = render(result)
    table = summary(html)
    for label in ['Direct raw materials', 'Direct 3D-print material failure', 'Other / overhead']:
        assert label not in table
    for label in ['Purchased components', 'Manufactured components', 'Packaging',
                  'Manufactured-component material waste allowance', 'Production electricity',
                  'Hands-on labour', 'Total production cost before selling fees']:
        assert label in table
    assert '£1.99' in table and '£0.01' in table
    assert result == before
    # The full-width table is outside padded body content, like existing tables.
    assert '</div>\n<div class="table-responsive"><table class="table bom-cost-summary">' in html


def test_unknowns_warnings_and_zero_total_remain_visible():
    result = example()
    result.update(complete=False, missing=['Labour rate unknown'], labour=None, cost=None)
    result['subtotals']['packaging'] = None
    html = render(result)
    assert 'Labour rate unknown' in html and 'known partial subtotals' in html
    assert 'Hands-on labour' in summary(html) and 'Packaging' in summary(html)
    assert 'Not retained / incomplete' in summary(html)
    result.update(cost='0.00')
    assert 'Total production cost before selling fees</th><td>£0.00' in summary(render(result))


def test_legacy_snapshot_without_subtotals_still_renders():
    result = example()
    del result['subtotals']
    result['material_component'] = '0.00'
    assert 'Materials / components including packaging' not in summary(render(result))
    result['material_component'] = None
    assert 'Materials / components including packaging' in summary(render(result))
