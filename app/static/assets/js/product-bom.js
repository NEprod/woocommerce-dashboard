(() => {
  'use strict';
  const own = (object, key) => Object.hasOwn(object || {}, key);
  const entry = (object, key) => own(object, key) ? object[key] : undefined;
  const MODE = {inherit:'Inherit / No changes', add:'Add to BOM', replace:'Replace entire BOM'};
  const optionRef = (ref) => ref && typeof ref === 'object';
  const rule = (documentValue, ref) => ref == null ? documentValue || {} : optionRef(ref) ?
    entry(entry(documentValue?.options, ref.name), ref.value) || {} : entry(documentValue?.variants, ref) || {};
  const modeOf = (shared, local) => local.mode || (shared.mode === 'replace' ? 'replace' :
    Object.keys(local.lines || {}).length ? 'add' : shared.mode || (Object.keys(shared.lines || {}).length ? 'add' : 'inherit'));
  const refKey = (ref) => JSON.stringify(ref);
  function ensure(object, key) {
    if (!own(object, key)) Object.defineProperty(object, key, {value:{}, enumerable:true, configurable:true, writable:true});
    return object[key];
  }
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
      this.option_groups = settings.option_groups || {};
      this.selectedVariants = new Set();
      this.variantQuery = '';
      this.sequence = 0;
      this.rebuildOnPreview = true;
      this.render();
      this.schedule();
    }
    get() { return Object.keys(this.value).length ? structuredClone(this.value) : undefined; }
    dispose() { clearTimeout(this.timer); this.sequence++; }
    current(ref) { return rule(this.value, ref); }
    layer(ref) {
      if (ref == null) return this.value;
      return optionRef(ref) ? ensure(ensure(this.value.options ??= {}, ref.name), ref.value) :
        ensure(this.value.variants ??= {}, ref);
    }
    change(sku, key, operation, redraw = true) {
      const layer = this.layer(sku);
      if (operation) (layer.lines ??= {})[key] = operation;
      else if (layer.lines) delete layer.lines[key];
      this.changed(redraw);
    }
    changed(redraw = true) {
      const optionGroups = Object.values(this.value.options || {});
      for (const layer of [this.value, ...Object.values(this.value.variants || {}), ...optionGroups.flatMap(Object.values)]) {
        if (layer.lines && !Object.keys(layer.lines).length) delete layer.lines;
      }
      for (const group of [this.value.variants || {}, ...optionGroups]) {
        for (const [key, layer] of Object.entries(group)) if (!Object.keys(layer).length) delete group[key];
      }
      for (const [name, values] of Object.entries(this.value.options || {})) if (!Object.keys(values).length) delete this.value.options[name];
      if (this.value.options && !Object.keys(this.value.options).length) delete this.value.options;
      if (this.value.variants && !Object.keys(this.value.variants).length) delete this.value.variants;
      this.onChange();
      if (redraw) {
        this.rebuildOnPreview = true;
        this.render();
      }
      this.schedule();
    }
    option(ref) {
      return this.previewData?.options?.find(row => row.name === ref.name && row.value === ref.value);
    }
    result(ref) {
      if (ref === null) return this.previewData?.parent;
      if (!optionRef(ref)) return entry(this.previewData?.variants, ref);
      const row = this.option(ref);
      return row ? {...row.result, before:row.before, impact:row.impact, ownCount:row.own_count} :
        (this.previewData ? {...this.previewData.parent, before:this.previewData.parent} : undefined);
    }
    card(ref, label, open) {
      const details = make('details');
      details.dataset.bomKey = refKey(ref);
      details.open = open.has(refKey(ref));
      details.className = 'production-bom-variant';
      const title = make('summary', label);
      const text = make('span');
      title.append(text);
      const view = {ref, summary:text, estimates:new Map()};
      this.views.push(view);
      const fill = () => {
        if (details.open && details.childElementCount === 1) details.append(this.panel(ref, view));
        if (!details.open && details.childElementCount > 1) {
          details.lastElementChild.remove();
          view.total = view.replacement = null;
          view.estimates.clear();
        }
        this.paint(view);
      };
      details.append(title);
      details.addEventListener('toggle', fill);
      fill();
      return details;
    }
    variantNeedsCard(variant) {
      return this.selectedVariants.has(variant.sku) || own(this.value.variants,variant.sku) ||
        own(this.shared?.variants,variant.sku) || this.result(variant.sku)?.warnings.length;
    }
    render() {
      const open = new Set(Array.from(this.root.querySelectorAll('details[open]'), el => el.dataset.bomKey));
      this.views = [];
      this.root.replaceChildren(make('h3','Main Product BOM'), this.panel(null));
      if (Object.keys(this.option_groups).length) {
        this.root.append(make('h3','Option Materials & Components'),
          make('p','Set requirements once for each option value. They apply to every matching sellable variant.'));
        for (const [name, values] of Object.entries(this.option_groups)) {
          const group = make('section');
          group.append(make('h4', name));
          for (const value of values) group.append(this.card({name,value}, name + ' = ' + value, open));
          this.root.append(group);
        }
      }
      if (this.variants.length) {
        this.root.append(make('h3','Individual Variant Adjustments'),
          make('p','Saved exceptions and variants needing attention are shown below. Find any other variant to edit its recipe.'));
        const search = make('input');
        search.type = 'search';
        search.className = 'form-control';
        search.value = this.variantQuery;
        const picker = select([['','Find a variant']], '');
        const refresh = () => {
          const q = search.value.toLowerCase().trim();
          const candidates = this.variants.filter(v => [v.sku,v.label,v.identity].some(x => String(x ?? '').toLowerCase().includes(q)));
          picker.replaceChildren(...select([['','Select a variant'], ...candidates.map(v => [v.sku, v.sku + ' · ' + v.label])], '').children);
        };
        refresh();
        search.addEventListener('input', () => { this.variantQuery = search.value; refresh(); });
        this.root.append(field('Search by SKU, option values or variant identity', search), field('Variant', picker),
          button('Edit variant BOM', () => {
            if (!picker.value) return;
            const sku = picker.value;
            this.selectedVariants.add(sku);
            this.render();
            const target = Array.from(this.root.querySelectorAll('details')).find(el => el.dataset.bomKey === refKey(sku));
            if (target) {
              target.open = true;
              target.querySelector('summary')?.focus();
            }
          }));
        for (const variant of this.variants.filter(v => this.variantNeedsCard(v))) {
          this.root.append(this.card(variant.sku, variant.label ? variant.label + ' · ' + variant.sku : variant.sku, open));
        }
        const summary = make('details');
        summary.className = 'production-bom-variant';
        summary.dataset.bomKey = 'cost-summary';
        summary.open = open.has('cost-summary');
        this.summaryList = make('ul');
        summary.append(make('summary','Cost Summary — all sellable variants'), this.summaryList);
        this.root.append(summary);
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
      for (const orphan of this.previewData?.orphans || []) {
        const notice = make('p', 'Unmatched option BOM (retained, not applied): ' + orphan.name + ' = ' + orphan.value + ' ');
        if (orphan.local) notice.append(button('Remove orphaned option rule', () => {
          if (!window.confirm('Remove this unmatched option rule only? Other BOMs will not change.')) return;
          delete this.value.options[orphan.name][orphan.value];
          this.changed();
        }));
        this.root.append(notice);
      }
      for (const view of this.views) this.paint(view);
      this.paintSummary();
    }
    changeMode(ref, next) {
      const prior = this.current(ref);
      const replacing = next === 'replace' && prior.mode !== 'replace';
      const clears = replacing || next === 'inherit' || next === '' || prior.mode === 'replace';
      if (replacing && !window.confirm('Replace the entire inherited recipe for this rule? Inherited items, including packaging, will not automatically remain. Build a complete independent BOM.')) return false;
      if (!replacing && clears && Object.keys(prior.lines || {}).length &&
          !window.confirm('Change BOM mode and discard this rule’s local line edits? The parent recipes and manual cost will not change.')) return false;
      const layer = this.layer(ref);
      if (clears) delete layer.lines;
      if (next) layer.mode = next;
      else delete layer.mode;
      this.changed();
      return true;
    }
    panel(sku, view) {
      const box = make('div');
      box.className = 'structured-editor-list';
      const settings = this.current(sku);
      const result = this.result(sku);
      const isOption = optionRef(sku);
      const ownReplace = settings.mode === 'replace';
      const sharedRule = rule(this.shared, sku);
      const currentMode = modeOf(sharedRule, settings);
      if (sku != null) {
        const choices = this.kind === 'override' && Object.keys(sharedRule).length ?
          [['', 'Use shared rule — ' + MODE[modeOf({},sharedRule)]]] : [];
        choices.push(...Object.entries(MODE));
        const selected = settings.mode || (Object.keys(settings.lines || {}).length && modeOf({},sharedRule) !== 'replace' ? 'add' :
          choices[0][0] === '' ? '' : currentMode);
        const modeControl = select(choices, selected);
        modeControl.addEventListener('change', () => {
          if (!this.changeMode(sku, modeControl.value)) modeControl.value = selected;
        });
        box.append(field('BOM mode', modeControl));
        if (currentMode === 'replace') {
          const compare = make('div');
          compare.className = 'structured-editor-row';
          compare.append(make('strong','Replacement recipe — inherited requirements are not automatically kept.'));
          const replacement = make('p');
          compare.append(replacement);
          view.replacement = replacement;
          box.append(compare);
        }
      }
      const all = layersFor(this.shared, this.value, null);
      const inherited = sku == null ? rows(all.slice(0,-1)) :
        new Map((result?.before?.rows || []).map(line => [line.line_id,line]));
      const resolved = sku == null ? rows(all) : new Map((result?.rows || []).map(line => [line.line_id,line]));
      const conflicts = new Map((result?.conflicts || []).filter(conflict => conflict.line_id).map(conflict => [conflict.line_id,conflict]));
      if (ownReplace) for (const id of resolved.keys()) if (!own(settings.lines, id)) resolved.delete(id);
      const canEdit = sku == null || currentMode !== 'inherit';
      const itemOptions = this.items.map(i => [i.id, i.name + ' · ' + i.code]);
      const estimates = new Map();
      for (const id of canEdit ? new Set([...resolved.keys(), ...Object.keys(settings.lines || {}), ...conflicts.keys()]) : []) {
        const local = settings.lines?.[id];
        const base = inherited.get(id);
        const line = resolved.get(id) || (local?.op === 'add' ? local :
          local?.op === 'remove' && base ? {...base, excluded:true} : undefined);
        const conflict = conflicts.get(id);
        const record = make('div');
        record.className = 'structured-editor-row production-bom-line';
        const status = !line ? 'Conflict · inherited line missing' : line.excluded ? 'Excluded' :
          local?.op === 'add' ? (ownReplace ? 'Replacement recipe' : this.kind === 'shared' && sku == null ? 'Shared line' : 'Added here') :
          local?.op === 'modify' ? 'Modified here' : (line.origin || 'Inherited');
        const badge = make('p', status);
        const reset = local?.op !== 'add' ? button('Reset to inherited', () => this.change(sku, id, null)) : null;
        if (reset && !local) reset.hidden = true;
        record.append(badge);
        if (conflict) record.append(make('p', conflict.message));
        if (line) {
          const item = this.items.find(i => i.id === line.item_id);
          if (line.excluded) {
            record.append(make('p', item?.name || 'Inventory item #' + line.item_id));
            if (!local && !ownReplace) record.append(button('Restore this requirement here', () =>
              this.change(sku, id, {op:'modify', item_id:line.item_id, quantity:line.quantity, unit:line.unit})));
          }
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
              for (const key of Object.keys(patch)) if (!conflict?.fields.includes(key) && !base?.excluded &&
                String(patch[key]) === String(base?.[key])) delete patch[key];
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
            if (conflict && sku != null && !isOption && !ownReplace) record.append(button('Resolve with these values', () =>
              this.change(sku, id, {op:'modify', item_id:Number(chosen.value), quantity:qty.value, unit:unit.value})));
          }
        }
        if (!line && conflict && sku != null && !isOption && !ownReplace) record.append(button('Explicitly exclude this target',
          () => this.change(sku, id, {op:'remove'})));
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
      if (canEdit) box.append(field('Search inventory by name, SKU, ID or barcode', search), field('Inventory item', picker),
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
      if (!isOption) box.append(field('Cost source', mode));
      if (!isOption && mode.value === 'manual') {
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
      else this.views.push({ref:sku, total, estimates});
      return box;
    }
    paint(view) {
      const result = this.result(view.ref);
      const isOption = optionRef(view.ref);
      const mode = view.ref == null ? null : modeOf(rule(this.shared,view.ref), this.current(view.ref));
      const quote = value => value == null ? '—' : '£' + value;
      if (view.summary) {
        view.summary.textContent = ' · ' + MODE[mode] + (isOption ?
          (result?.ownCount != null ? ' · ' + result.ownCount + ' changes' : '') +
          (result?.impact == null ? '' : ' · estimate impact ' + quote(result.impact)) :
          (result ? ' · ' + result.count + ' requirements · ' + quote(result.estimated) : '')) +
          (result?.warnings.length ? ' · Needs Attention' : '');
      }
      if (!result) return;
      if (view.total) view.total.textContent = 'BOM estimate: ' + quote(result.estimated) +
        (isOption ? ' · actual variant costs depend on all matching rules' :
          ' · Shopify cost: ' + quote(result.shopify) + ' (' + result.mode + '; ' + result.mode_source + ')') +
        (result.warnings.length ? ' · ' + result.warnings.join(' ') : '');
      if (view.total && !isOption && result.profitability) {
        const p = result.profitability;
        view.total.textContent += p.complete ?
          ' · Finished-product cost £' + p.cost + ' · Fee-aware suggested price £' + p.fee_adjusted +
          ' · Profit at chosen £' + p.selling_price + ': £' + p.profit + ' · Margin ' + p.margin + '%' :
          ' · Finished-product costing incomplete: ' + p.missing.join(' ');
      }
      if (view.replacement) {
        const describe = (recipe) => (recipe?.rows || []).filter(r => !r.excluded)
          .map(r => (r.name || 'Inventory item #' + r.item_id) + ' × ' + r.quantity + ' ' + r.unit).join('; ') || 'None';
        view.replacement.textContent = 'Inherited: ' + describe(result.before) + ' · Resolved replacement: ' + describe(result);
      }
      for (const line of result.rows) {
        const price = view.estimates.get(line.line_id);
        if (price) price.textContent = 'Line cost: ' + quote(line.cost) + (line.problem ? ' · ' + line.problem : '');
      }
    }
    paintSummary() {
      if (!this.summaryList || !this.previewData) return;
      this.summaryList.replaceChildren(...this.variants.map(variant => {
        const r = this.result(variant.sku);
        return make('li', variant.sku + ' · ' + (variant.label || 'Variant') +
          ' · BOM ' + (r?.estimated == null ? '—' : '£' + r.estimated) +
          ' · Shopify ' + (r?.shopify == null ? 'unconfigured or blocked' : '£' + r.shopify) +
          (r?.warnings.length ? ' · Needs Attention' : ''));
      }));
    }
    schedule() {
      const generation = ++this.sequence;
      clearTimeout(this.timer);
      for (const view of this.views) {
        if (view.total) view.total.textContent = 'Calculating…';
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
          const visible = this.variants.filter(v => this.variantNeedsCard(v)).map(v => v.sku).join('\n');
          this.previewData = data;
          const after = this.variants.filter(v => this.variantNeedsCard(v)).map(v => v.sku).join('\n');
          if (this.rebuildOnPreview || after !== visible) {
            this.rebuildOnPreview = false;
            this.render();
          } else {
            for (const view of this.views) this.paint(view);
            this.paintSummary();
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
  window.TLCProduction = {Editor, rows, layersFor, modeOf, rule};
})();
