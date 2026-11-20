import { cents, exactQuote } from './promotion-quote.mjs';

const make = (tag,text) => {
  const result = document.createElement(tag);
  if (text != null) result.textContent = text;
  return result;
};
const button = (label, callback) => {
  const result = make('button',label);
  result.type = 'button';
  result.className = 'btn btn-outline-primary';
  result.addEventListener('click',callback);
  return result;
};

for (const editor of document.querySelectorAll('[data-promotion-editor]')) {
  const kind = editor.elements.kind, method = editor.elements.method, scope = editor.elements.scope;
  function show() {
    const productOnly = ['exact','bxgy'].includes(kind.value);
    if (productOnly) scope.value = 'product';
    for (const section of editor.querySelectorAll('[data-show-kind]')) section.hidden = !section.dataset.showKind.split(' ').includes(kind.value);
    for (const section of editor.querySelectorAll('[data-show-method]')) section.hidden = section.dataset.showMethod !== method.value;
    for (const section of editor.querySelectorAll('[data-promotion-product-scope]')) section.hidden = scope.value !== 'product';
  }
  for (const field of [kind,method,scope]) field.addEventListener('change',show);
  show();
  const addTier = editor.querySelector('[data-add-tier]');
  addTier?.addEventListener('click', () => {
    const rows = editor.querySelector('[data-tier-rows]');
    if (rows.querySelectorAll('[data-tier-row]').length >= 8) return;
    const row = make('div'); row.className = 'editor-field-grid'; row.dataset.tierRow = '';
    for (const [label,name,mode] of [['Quantity','tier_qty','numeric'],['Fixed total (£)','tier_total','decimal']]) {
      const field = make('label',label); field.className = 'guided-field';
      const input = make('input'); input.name = name; input.inputMode = mode; input.className = 'form-control';
      field.append(input); row.append(field);
    }
    rows.append(row);
  });
  for (const picker of editor.querySelectorAll('[data-promotion-targets]')) {
    const group = picker.dataset.promotionTargets;
    const input = picker.querySelector('[data-target-search]');
    const results = picker.querySelector('[data-target-results]');
    const chosen = picker.querySelector('[data-target-chosen]');
    const status = picker.querySelector('[data-target-status]');
    const mode = picker.querySelector('[data-eligibility-mode]');
    const key = item => JSON.stringify(Object.entries(item).sort(([left],[right]) => left < right ? -1 : left === right ? 0 : 1));
    let timer, generation = 0;
    chosen.addEventListener('click', event => {
      const action = event.target.closest('[data-remove-target]');
      if (action) action.closest('[data-target-row]').remove();
    });
    function add(target,label) {
      if ([...chosen.querySelectorAll('input')].some(el => key(JSON.parse(el.value))===key(target))) {
        status.textContent = 'This target is already selected.';
        return;
      }
      if (chosen.querySelectorAll('input').length >= 100) {
        status.textContent = 'Use up to 100 selections; narrow this group.';
        return;
      }
      const row = make('div');
      row.className = 'repeatable-row';
      row.dataset.targetRow = '';
      const value = make('input');
      value.type = 'hidden';
      value.name = group;
      value.value = JSON.stringify(target);
      const remove = make('button','Remove');
      remove.className = 'btn btn-outline-primary';
      remove.type = 'button';
      remove.dataset.removeTarget = '';
      row.append(value,make('span',label),remove);
      chosen.append(row);
      status.textContent = 'Added ' + label;
    }
    const search = () => {
      const active = ++generation;
      clearTimeout(timer);
      results.replaceChildren();
      const q = input.value.trim();
      if (q.length<2) { status.textContent='Enter at least two characters.'; return; }
      status.textContent='Searching…';
      timer = setTimeout(async () => {
        try {
          const response = await fetch(editor.dataset.searchUrl+'?q='+encodeURIComponent(q));
          if (!response.ok) throw new Error();
          const data = await response.json();
          if (active!==generation) return;
          for (const collection of group === 'exclusions' || mode?.value === 'products' ? [] : data.collections) {
            results.append(button(collection.name+' · collection',() => add(collection.target,collection.name+' · collection')));
          }
          for (const product of mode?.value === 'collections' ? [] : data.products) {
            const row = make('div');
            row.className = 'structured-editor-row';
            const choices = make('select');
            choices.setAttribute('aria-label','Eligibility for '+product.name);
            choices.className = 'form-select';
            const options = group === 'exclusions' ? product.options.filter(option => ['product','fixed'].includes(option.target.mode)) : product.options;
            for (const [index,option] of options.entries()) {
              const element = make('option',option.label);
              element.value=index;
              choices.append(element);
            }
            row.append(make('strong',product.name+' · '+product.sku),choices,button('Add selection',() => {
              const option = options[Number(choices.value)];
              if (option) add(option.target,product.name+' · '+option.label);
            }));
            results.append(row);
          }
          status.textContent = results.children.length ? 'Choose a result.' : 'No matching catalogue entries.';
        } catch {
          if (active===generation) status.textContent='Catalogue search is unavailable. Try again.';
        }
      },300);
    };
    input.addEventListener('input',search);
    mode?.addEventListener('change',search);
  }
}

for (const form of document.querySelectorAll('[data-promotion-quote]')) {
  const output = form.querySelector('[data-quote-result]');
  const offer = JSON.parse(form.querySelector('[data-quote-offer]').textContent);
  const money = n => '£'+(n/100).toFixed(2);
  const update = () => {
    const lines = [...form.querySelectorAll('[data-quote-id]')].filter(el => el.value.trim()!=='0' && el.value.trim()!=='')
      .map(el => ({id:el.dataset.quoteId,variant:el.dataset.quoteId,quantity:/^[0-9]+$/.test(el.value)?Number(el.value):NaN,
        pence:cents(el.dataset.quotePrice),cost:el.dataset.quoteCost===''?null:Number(el.dataset.quoteCost)}));
    const quote = exactQuote(lines,offer);
    const subtotal = lines.reduce((sum,line) => sum+line.quantity*line.pence,0);
    const gross=lines.every(line => line.cost!=null) ?
      ' · '+money(subtotal-(quote?.savings||0)-lines.reduce((sum,line) => sum+line.quantity*line.cost,0))+' after local cost, before fees and taxes' :
      ' · some local costs unavailable';
    output.textContent=quote ? 'Sample only: '+quote.groups+' qualifying sets · '+money(quote.savings)+
      ' discount · '+money(subtotal-quote.savings)+' eligible item total'+gross+'. Based on saved local list prices.' :
      'Cannot quote this cart. Enter whole quantities within the supported cart limit and check local GBP prices.';
  };
  form.addEventListener('input',update);
}
