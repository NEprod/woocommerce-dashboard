from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text()


def test_products_use_one_shared_row_renderer():
    source = read('app/static/assets/js/products-browser.js')
    render = source.split('const body = document.createElement("div");', 1)[1].split('function renderVariationItems', 1)[0]
    assert 'desktop.appendChild(desktopProduct(product))' in render
    assert 'mobile.appendChild(mobileProduct(product))' not in render
    assert 'body.appendChild(mobile)' not in render
    css = read('app/static/assets/css/custom.css')
    assert '.products-desktop-table .product-column-headings, .products-desktop-table .product-row { min-width: 920px; }' in css
    assert '.products-desktop-table, .operator-table-scroll, .variation-list { max-width: 100%; overflow-x: auto;' in css


def test_workspace_and_review_keep_real_tables_and_local_scroll():
    workspace = read('app/templates/product_detail.html')
    review = read('app/templates/shopify_drafts.html')
    scroll = read('app/static/assets/js/operator-table-scroll.js')
    assert 'operator-effective-table shopify-review-table' in workspace
    assert '<th>Field</th><th>Resolved Value</th><th>Source</th>' in workspace
    assert '<th>Field</th><th>Current Shopify</th><th>Proposed Value</th><th>Action / Status</th>' in review
    assert 'operator-table-scroll.js' in workspace and 'operator-table-scroll.js' in review
    assert "document.addEventListener('tlc:review-ready', wrapTables)" in scroll
    assert "table.parentElement.classList.contains('operator-table-scroll')" in scroll
    css = read('app/static/assets/css/custom.css')
    assert '.workspace-card-body > .operator-table-scroll { width: calc(100% + 2 * var(--space-7));' in css
    assert '.workspace-card-body > .operator-table-scroll { width: calc(100% + 2 * var(--space-5));' in css
    assert '.operator-workspace .operator-table-scroll .table > :is(thead, tbody) > tr { border-bottom: var(--border-subtle); }' in css
    assert '.operator-workspace .metadata-comparison { overflow-x: auto;' in css
    assert '.operator-workspace .metadata-comparison-head { display: grid; background: var(--color-brand-forest);' in css


def test_simple_comparison_open_by_default_but_large_variable_remains_collapsible():
    review = read('app/templates/shopify_drafts.html')
    scroll = read('app/static/assets/js/operator-table-scroll.js')
    assert "plan.kind != 'variable' or review_display.matching|length <= 20" in review
    assert 'Status, sales channels and Markets|Collections & standard category' in scroll
    assert 'Attributes and definitions|Packages|Deferred fields|Already Matching' in scroll
    assert 'variantGroups <= 4 && count <= 10' in scroll
    assert "group.closest('.operator-help')" in scroll
    assert 'data-review-group="{{ owner }}"' in review
    assert 'name="acknowledge" value="yes" required' in review
    assert 'name="review" value="{{ token }}"' in review


def test_mobile_editor_short_fields_share_grid_without_changing_inputs():
    css = read('app/static/assets/css/custom.css')
    editor = read('app/templates/metadata_editor.html')
    assert '.metadata-editor-main .editor-field-grid:has(#metadata-price)' in css
    assert '.metadata-editor-main .dimensions-grid { grid-template-columns: repeat(2, minmax(0, 1fr));' in css
    assert "extra_field('price','Regular price'" in editor
    assert "extra_field('sale_price','Sale price'" in editor
    assert 'data-acknowledge-shared' in editor
