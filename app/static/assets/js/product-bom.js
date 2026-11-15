(() => {
  'use strict';
  const own = (object, key) => Object.hasOwn(object || {}, key);
  const entry = (object, key) => own(object, key) ? object[key] : undefined;
  const layersFor = (shared, local, sku) => sku == null ? [shared, local] :
    [shared, local, entry(shared?.variants, sku), entry(local?.variants, sku)];
  function rows(layers) {
    const result = new Map();
    for (const layer of layers) for (const [id, change] of Object.entries(layer?.lines || {})) {
      if (result.has(id) === (change.op === 'add')) continue;
      result.set(id, {...result.get(id), ...change, excluded: change.op === 'remove'});
    }
    return result;
  }
  const make = (tag, value) => {
    const element = document.createElement(tag);
    if (value != null) element.textContent = value;
    return element;
  };
  const button = (label, action) => {
    const control = make('button', label);
    control.type = 'button';
    control.className = 'btn btn-outline-secondary btn-sm';
    control.addEventListener('click', action);
    return control;
  };
  function field(label, control) {
    const wrapper = make('label', label + ' ');
    wrapper.className = 'guided-field';
    wrapper.append(control);
    return wrapper;
  }
  function select(choices, value) {
    const control = make('select');
    control.className = 'form-select';
    for (const [key, label] of choices) {
      const option = make('option', label);
      option.value = key;
      control.append(option);
    }
    control.value = value ?? '';
    return control;
  }
  function newId() {
    return 'line-' + Array.from(crypto.getRandomValues(new Uint8Array(12)), n => n.toString(16).padStart(2, '0')).join('');
  }
  class Editor {
    constructor(root, settings) {
      Object.assign(this, settings);
      this.root = root;
      this.value = structuredClone(settings.value || {});
      this.items = settings.items || [];
      this.variants = settings.variants || [];
      this.sequence = 0;
      this.render();
    }
    get() { return Object.keys(this.value).length ? structuredClone(this.value) : undefined; }
    dispose() { clearTimeout(this.timer); this.sequence++; }
    current(sku) { return sku == null ? this.value : (entry(this.value.variants, sku) || {}); }
    layer(sku) {
      if (sku == null) return this.value;
      const variants = this.value.variants ??= {};
      if (!own(variants, sku)) Object.defineProperty(variants, sku, {value:{}, enumerable:true, configurable:true});
      return variants[sku];
    }
    change(sku, key, operation, redraw = true) {
      const layer = this.layer(sku);
      if (operation) (layer.lines ??= {})[key] = operation;
      else if (layer.lines) delete layer.lines[key];
      this.changed(redraw);
    }
    changed(redraw = true) {
      for (const layer of [this.value, ...Object.values(this.value.variants || {})]) {
        if (layer.lines && !Object.keys(layer.lines).length) delete layer.lines;
      }
      for (const [sku, layer] of Object.entries(this.value.variants || {})) {
        if (!Object.keys(layer).length) delete this.value.variants[sku];
      }
      if (this.value.variants && !Object.keys(this.value.variants).length) delete this.value.variants;
      this.onChange();
      if (redraw) this.render();
      else this.schedule();
    }
    render() {
      const open = new Set(Array.from(this.root.querySelectorAll('details[open]'), el => el.dataset.bomSku));
      this.views = [];
      this.root.replaceChildren(this.panel(null));
      for (const variant of this.variants) {
        const details = make('details');
        details.dataset.bomSku = variant.sku;
        details.open = open.has(variant.sku);
        details.className = 'production-bom-variant';
        const title = make('summary', variant.label ? variant.label + ' · ' + variant.sku : variant.sku);
        const text = make('span');
        title.append(text);
        const view = {sku:variant.sku, summary:text, estimates:new Map()};
        this.views.push(view);
        function close() {
          if (!details.open && details.childElementCount > 1) {
            details.lastElementChild.remove();
            view.total = null;
            view.estimates.clear();
          }
        }
        const fill = () => {
          if (details.open && details.childElementCount === 1) {
            details.append(this.panel(variant.sku, view));
            this.schedule();
          } else close();
        };
        details.append(title);
        this.root.append(details);
        details.addEventListener('toggle', fill);
        fill();
      }
      if (this.kind === 'override') {
        for (const sku of Object.keys(this.value.variants || {}).filter(key => !this.variants.some(v => v.sku === key))) {
          const notice = make('p', 'Unmatched retained variant BOM: ' + sku + ' ');
          notice.append(button('Reset unmatched variant changes', () => {
            delete this.value.variants[sku];
            this.changed();
          }));
          this.root.append(notice);
        }
      }
      this.schedule();
    }
    panel(sku, view) {
      const box = make('div');
      box.className = 'structured-editor-list';
      const settings = this.current(sku);
      const all = layersFor(this.shared, this.value, sku);
      const inherited = rows(all.slice(0, -1));
      const resolved = rows(all);
      const itemOptions = this.items.map(i => [i.id, i.name + ' · ' + i.code]);
      const estimates = new Map();
      for (const id of new Set([...resolved.keys(), ...Object.keys(settings.lines || {})])) {
        const local = settings.lines?.[id];
        const base = inherited.get(id);
        const line = resolved.get(id);
        const record = make('div');
        record.className = 'structured-editor-row production-bom-line';
        const status = !line ? 'Conflict · inherited line missing' : line.excluded ? 'Excluded' :
          local?.op === 'add' ? (this.kind === 'shared' && sku == null ? 'Shared line' : 'Added here') :
          local?.op === 'modify' ? 'Modified here' : 'Inherited';
        const badge = make('p', status);
        const reset = local?.op !== 'add' ? button('Reset to inherited', () => this.change(sku, id, null)) : null;
        if (reset && !local) reset.hidden = true;
        record.append(badge);
        if (line) {
          const item = this.items.find(i => i.id === line.item_id);
          if (line.excluded) record.append(make('p', item?.name || 'Inventory item #' + line.item_id));
          else {
            const chosen = select(item ? itemOptions : [[line.item_id, 'Unavailable item #' + line.item_id], ...itemOptions], line.item_id);
            const qty = make('input');
            qty.type = 'text';
            qty.inputMode = 'decimal';
            qty.className = 'form-control';
            qty.value = line.quantity;
            const unit = select((item?.units || [line.unit]).map(u => [u, u]), line.unit);
            const delta = (next) => {
              const current = this.current(sku).lines?.[id];
              if (current?.op === 'add') return {...current, ...next};
              const patch = {...current, ...next};
              delete patch.op;
              // Unchanged fields remain inherited independently.
              for (const key of Object.keys(patch)) if (!base?.excluded && String(patch[key]) === String(base?.[key])) delete patch[key];
              return Object.keys(patch).length ? {op:'modify', ...patch} : null;
            };
            const edit = next => {
              const operation = delta(next);
              if (reset) {
                reset.hidden = !operation;
                badge.textContent = operation ? 'Modified here' : 'Inherited';
              }
              this.change(sku, id, operation, false);
            };
            chosen.addEventListener('change', () => {
              const newItem = this.items.find(i => i.id === Number(chosen.value));
              if (newItem) this.change(sku, id, delta({item_id:newItem.id, quantity:qty.value,
                unit:newItem.units.includes(unit.value) ? unit.value : newItem.unit}));
            });
            qty.addEventListener('input', () => edit({quantity:qty.value}));
            unit.addEventListener('change', () => edit({unit:unit.value}));
            const price = make('p');
            estimates.set(id, price);
            record.append(field('Material or component', chosen), field('Required quantity', qty), field('Unit', unit), price);
            record.append(button(local?.op === 'add' ? 'Remove line' : 'Exclude inherited line', () =>
              this.change(sku, id, local?.op === 'add' ? null : {op:'remove'})));
          }
        }
        if (reset) record.append(reset);
        box.append(record);
      }
      const search = make('input');
      search.type = 'search';
      search.className = 'form-control';
      const picker = select([['', 'Select an inventory item'], ...itemOptions], '');
      search.addEventListener('input', () => {
        const q = search.value.toLowerCase();
        picker.replaceChildren(...select([['', 'Select an inventory item'], ...this.items
          .filter(i => [i.name, i.code, i.id, i.supplier_sku].some(v => String(v ?? '').toLowerCase().includes(q)))
          .map(i => [i.id, i.name + ' · ' + i.code])], '').children);
      });
      box.append(field('Search inventory by name, SKU, ID or barcode', search), field('Inventory item', picker),
        button('Add Material or Component', () => {
          const item = this.items.find(i => i.id === Number(picker.value));
          if (item) this.change(sku, newId(), {op:'add', item_id:item.id, quantity:'1', unit:item.unit});
          else {
            picker.setCustomValidity('Select a material or component first.');
            picker.reportValidity();
          }
        }));
      picker.addEventListener('input', () => picker.setCustomValidity(''));
      const mode = select([['', this.kind === 'shared' && sku == null ? 'Automatic from BOM if present' : 'Inherit'],
        ['unconfigured', 'Not configured — leave Shopify unchanged'], ['calculated', 'Calculated from BOM'],
        ['manual', 'Manual cost']], settings.cost?.mode);
      mode.addEventListener('change', () => {
        const layer = this.layer(sku);
        if (!mode.value) delete layer.cost;
        else layer.cost = mode.value === 'manual' ? {mode:'manual', amount:settings.cost?.amount ?? ''} : {mode:mode.value};
        this.changed();
      });
      box.append(field('Cost source', mode));
      if (mode.value === 'manual') {
        const cost = make('input');
        cost.type = 'text';
        cost.inputMode = 'decimal';
        cost.className = 'form-control';
        cost.value = settings.cost.amount;
        cost.addEventListener('input', () => { this.layer(sku).cost.amount = cost.value; this.changed(false); });
        box.append(field('Manual cost (GBP)', cost));
      }
      const total = make('p', 'Calculating…');
      total.setAttribute('aria-live', 'polite');
      box.append(total);
      if (view) Object.assign(view, {total, estimates});
      else this.views.push({sku, total, estimates});
      return box;
    }
    schedule() {
      const generation = ++this.sequence;
      clearTimeout(this.timer);
      for (const view of this.views) {
        if (view.total) view.total.textContent = 'Calculating…';
        if (view.summary) view.summary.textContent = '';
        for (const price of view.estimates.values()) price.textContent = '';
      }
      this.timer = setTimeout(async () => {
        if (!this.preview || !this.root.isConnected) return;
        try {
          const response = await fetch(this.preview, {method:'POST', headers:{'Content-Type':'application/json',
            'X-CSRFToken':document.querySelector('meta[name="csrf-token"]')?.content || ''},
          body:JSON.stringify({kind:this.kind, production:this.value})});
          const data = await response.json();
          if (!response.ok) throw new Error(data.error || 'Current costs are unavailable.');
          if (generation !== this.sequence) return;
          for (const view of this.views) {
            const result = view.sku == null ? data.parent : data.variants[view.sku];
            if (!result) continue;
            const quote = value => value == null ? '—' : '£' + value;
            if (view.total) view.total.textContent = 'BOM estimate: ' + quote(result.estimated) + ' · Shopify cost: ' +
              quote(result.shopify) + ' (' + result.mode + '; ' + result.mode_source + ')' +
              (result.warnings.length ? ' · ' + result.warnings.join(' ') : '');
            if (view.summary) view.summary.textContent = ' · ' + result.count + ' requirements · ' +
              quote(result.estimated) + (result.warnings.length ? ' · Check costs' : '');
            for (const line of result.rows) {
              const price = view.estimates.get(line.line_id);
              if (price) price.textContent = 'Line cost: ' + quote(line.cost) + (line.problem ? ' · ' + line.problem : '');
            }
          }
        } catch (error) {
          if (generation === this.sequence) for (const view of this.views) {
            if (view.total) view.total.textContent = error.message;
            if (view.summary) view.summary.textContent = ' · Check costs';
          }
        }
      }, 300);
    }
  }
  window.TLCProduction = {Editor, rows, layersFor};
})();
