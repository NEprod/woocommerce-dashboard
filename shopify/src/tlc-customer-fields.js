/* Shopify-hosted. No Dashboard requests, file uploads, secrets, pricing or cart replacement. */
(() => {
  'use strict';
  const kinds = new Set(['text','textarea','number','dropdown','radio','checkbox','checkbox_group','date','colour']);
  const idPattern = /^[a-z][a-z0-9_]{0,63}$/;
  const empty = value => value == null || typeof value === 'string' && !value.trim() || Array.isArray(value) && !value.length || value === false;
  const htmlText = value => String(value).replace(/[&<>"'/]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;','/':'&#47;'}[c]));
  function configuration(value) {
    if (!value || value.schema_version !== 1 || typeof value.enabled !== 'boolean' || !/^[a-f0-9]{64}$/.test(value.revision) || !Array.isArray(value.fields)) throw Error('Unsupported TLC fields. Contact us before ordering.');
    const seen = new Set();
    for (const f of value.fields) {
      if (!idPattern.test(f.id) || seen.has(f.id) || !kinds.has(f.type) || typeof f.label !== 'string' || !f.label.trim()) throw Error('Invalid or unsupported customer field. Please contact us before ordering.');
      seen.add(f.id);
      if (['dropdown','radio','checkbox_group'].includes(f.type) && !f.choices?.length) throw Error('Customer choices are unavailable.');
    }
    if (value.enabled && !value.fields.length) throw Error('Customer fields are unavailable.');
    return value;
  }
  function error(f, value) {
    if (empty(value)) return f.required ? 'Please complete this field.' : null;
    if (['text','textarea'].includes(f.type)) {
      if (typeof value !== 'string') return 'Enter text.';
      if ([...value].length < (f.min_length ?? 0) || [...value].length > (f.max_length ?? Infinity)) return 'Please check the text length.';
    } else if (f.type === 'number') {
      if (typeof value !== 'number' || !Number.isFinite(value) || value < (f.minimum ?? -Infinity) || value > (f.maximum ?? Infinity) || f.whole_number && !Number.isInteger(value)) return 'Please enter a valid number within the limits.';
    } else if (['dropdown','radio'].includes(f.type)) {
      if (!f.choices.some(c => c.id === value)) return 'Choose an available option.';
    } else if (f.type === 'checkbox_group') {
      if (!Array.isArray(value) || new Set(value).size !== value.length || value.some(v => !f.choices.some(c => c.id === v)) || value.length < (f.min_selections ?? 0) || value.length > (f.max_selections ?? Infinity)) return 'Please check your selections.';
    } else if (f.type === 'checkbox') {
      if (typeof value !== 'boolean') return 'Please check this option.';
    } else if (f.type === 'date') {
      if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(value) || Number.isNaN(Date.parse(value)) || new Date(value).toISOString().slice(0,10) !== value || value < (f.min_date ?? value) || value > (f.max_date ?? value)) return 'Please enter a valid date within the limits.';
    } else if (f.type === 'colour') {
      if (typeof value !== 'string' || !/^#[a-f0-9]{6}$/i.test(value) || f.choices?.length && !f.choices.some(c => c.value === value)) return 'Choose an available colour.';
    }
    return null;
  }
  function properties(config, product, answers, productionTime = '') {
    const result = Object.create(null), snapshot = [];
    if (productionTime.trim()) result['Production time'] = htmlText(productionTime);
    if (!config) return result;
    configuration(config);
    if (!config.enabled) return result;
    for (const field of config.fields) {
      const value = Object.hasOwn(answers,field.id) ? answers[field.id] : undefined;
      const problem = error(field,value);
      if (problem) throw Error(`${field.label}: ${problem}`);
      if (empty(value)) continue;
      const label = v => field.choices?.find(c => c.id === v || c.value === v)?.label ?? v;
      const readable = Array.isArray(value) ? value.map(label).join(', ') : value === true ? 'Yes' : label(value);
      // Visible values are entity-encoded: Horizon's current cart renders them
      // without explicit Liquid escaping. Raw typed answers stay in private data.
      const labelText = htmlText(field.label).replace(/[\[\]]/g,' ').trim() || 'Customisation';
      const base = labelText.startsWith('_') ? `Answer ${labelText}` : labelText;
      let key = base;
      for (let n=2; Object.hasOwn(result,key); n++) key=`${base} (${n})`;
      result[key] = htmlText(readable);
      const saved={id:field.id,type:field.type,label:field.label,property_key:key,value,display:readable};
      if(field.choices)saved.choice_ids=field.choices.filter(c=>Array.isArray(value)?value.includes(c.id):c.id===value||c.value===value).map(c=>c.id);
      snapshot.push(saved);
    }
    result._tlc_configuration = config.revision;
    result._tlc_personalisation = JSON.stringify({schema_version:1,product_id:String(product),revision:config.revision,
      visible_encoding:'html_entities',fields:snapshot});
    return result;
  }
  const api = {configuration,error,properties};
  globalThis.TLCStorefrontCustomisations = api;
  if (!globalThis.customElements || customElements.get('tlc-customer-fields')) return;
  const owners = new WeakMap();
  class CustomerFields extends HTMLElement {
    connectedCallback() {
      if (this.connected) return;
      this.connected = true;
      this.status = this.querySelector('[data-tlc-status]');
      this.container = this.querySelector('[data-tlc-fields]');
      this.answers ||= Object.create(null);
      this.controls = new Map();
      this.propertyInputs = [];
      try {
        this.config = this.dataset.configuration ? configuration(JSON.parse(this.dataset.configuration)) : null;
        if (this.config?.enabled) this.render();
        this.bind();
        const section = this.closest('.shopify-section') || this.parentElement;
        this.observer = new MutationObserver(() => {if (!this.form?.isConnected) {this.unbind();this.bind();}this.guardWallets();});
        this.observer.observe(section,{childList:true,subtree:true});
      } catch (exc) {this.status.textContent=exc.message;this.blockReason=exc.message;this.bind();}
    }
    disconnectedCallback() {this.observer?.disconnect();this.unbind();this.connected=false;}
    bind() {
      if (this.form) return;
      const form = document.getElementById(this.dataset.formId);
      const section = this.closest('.shopify-section');
      const component = form?.closest('product-form-component');
      if (!form || form.tagName !== 'FORM' || !/\/cart\/add(?:\.js)?\/?$/.test(new URL(form.getAttribute('action'),location.origin).pathname) || section && !section.contains(form) || component && component.dataset.productId !== this.dataset.productId) {
        this.status.textContent='Product details are not connected. Please contact us before ordering.';return;
      }
      if (owners.has(form) && owners.get(form) !== this) {this.status.textContent='Remove the duplicate TLC block.';return;}
      owners.set(form,this);this.form=form;
      form.querySelectorAll('[data-tlc-property]').forEach(input=>input.remove());
      this.productionInput=this.querySelector('[data-tlc-production-input]');
      if (this.productionInput) this.productionInput.disabled=true;
      this.guardWallets();
      // Shopify's input[name=id] shadows form.id. Always read the attribute.
      for (const inputs of this.controls.values()) for (const input of inputs) input.setAttribute('form',form.getAttribute('id'));
      // Horizon delegates submit from document capture: form capture is too late.
      // Window capture precedes it regardless of script registration order. Ignore
      // every other form; do not intercept clicks, fetch, checkout or payment APIs.
      this.submit = event => {
        if (event.target !== this.form) return;
        if (!this.validate()) {event.preventDefault();event.stopImmediatePropagation();}
        else this.sync();
      };
      this.formdata = event => {
        for (const input of this.propertyInputs) event.formData.delete(input.name);
        if (this.validate(false)) for (const [key,value] of Object.entries(this.values())) event.formData.set(`properties[${key}]`,value);
      };
      this.reset = event => queueMicrotask(()=>{if(event.defaultPrevented || !this.isConnected)return;this.answers=Object.create(null);if(this.config?.enabled)this.render();for(const inputs of this.controls.values())for(const input of inputs)input.setAttribute('form',this.form.getAttribute('id'));this.sync();});
      this.guardTarget = this.ownerDocument.defaultView;
      this.guardTarget.addEventListener('submit',this.submit,true);
      form.addEventListener('formdata',this.formdata);form.addEventListener('reset',this.reset);
      this.sync();
      if (!this.blockReason) this.status.textContent='';
    }
    unbind() {
      this.guardTarget?.removeEventListener('submit',this.submit,true);
      if (this.form) {this.form.removeEventListener('formdata',this.formdata);this.form.removeEventListener('reset',this.reset);owners.delete(this.form);}
      this.propertyInputs.forEach(n=>n.remove());this.propertyInputs=[];this.form=null;
      if (this.productionInput) this.productionInput.disabled=false;
      for (const [element,hidden] of this.shortcuts || []) element.hidden=hidden;
      this.shortcuts=new Map();
    }
    guardWallets() {
      if (!this.form || !this.config?.enabled) return;
      this.shortcuts ||= new Map();
      // Wallet buttons can bypass submit validation. Personalised items use the
      // guarded Add to cart path; cart checkout remains Shopify-controlled.
      for (const element of this.form.querySelectorAll('shopify-accelerated-checkout, .shopify-payment-button')) {
        if (!this.shortcuts.has(element)) this.shortcuts.set(element,element.hidden);
        element.hidden=true;
      }
    }
    values() {return properties(this.config,this.dataset.productId,this.answers,this.dataset.productionTime || '');}
    sync() {
      this.propertyInputs.forEach(n=>n.remove());this.propertyInputs=[];
      if (!this.form || this.blockReason || !this.validate(false)) return;
      for (const [key,value] of Object.entries(this.values())) {
        const input=document.createElement('input');input.type='hidden';input.name=`properties[${key}]`;input.value=value;input.setAttribute('data-tlc-property','');
        this.form.append(input);this.propertyInputs.push(input);
      }
    }
    validate(report=true) {
      if (this.blockReason) {if(report)this.status.textContent=this.blockReason;return false;}
      let first;
      for (const f of this.config?.enabled ? this.config.fields : []) {
        const inputs=this.controls.get(f.id) || [], input=inputs[0];
        if (input) this.answers[f.id]=f.type==='radio'?inputs.find(i=>i.checked)?.value || '':f.type==='checkbox_group'?inputs.filter(i=>i.checked).map(i=>i.value):f.type==='checkbox'?input.checked:f.type==='number'?(input.value===''?'':Number(input.value)):input.value;
        const message=error(f,this.answers[f.id]) || (input?.validity.badInput ? 'Please enter a valid value.' : null);
        input?.setCustomValidity(message || '');
        if (input) input.setAttribute('aria-invalid',message?'true':'false');
        if (message && !first) first={input,message:`${f.label}: ${message}`};
      }
      if(report || !first)this.status.textContent=first?.message || '';
      if(first && report){first.input?.reportValidity();first.input?.focus();}
      return !first;
    }
    render() {
      this.container.replaceChildren();
      for (const f of this.config.fields) {
        const group=['radio','checkbox_group'].includes(f.type);
        const wrap=document.createElement(group?'fieldset':'div');wrap.className='tlc-field';
        const label=document.createElement(group?'legend':'label');label.textContent=f.label+(f.required?' *':'');wrap.append(label);
        const key=`tlc-${this.dataset.productId}-${this.id || this.dataset.formId}-${f.id}`;
        const inputs=[];
        const value = this.answers[f.id] ?? f.default ?? (f.type==='checkbox'?false:f.type==='checkbox_group'?[]:'');
        this.answers[f.id]=value;
        const update=()=>this.sync();
        if (group) for (const [index,c] of f.choices.entries()) {
          const row=document.createElement('label'),input=document.createElement('input');
          input.type=f.type==='radio'?'radio':'checkbox';input.id=`${key}-${index}`;input.name=`tlc-ui-${key}`;input.value=c.id;
          input.checked=f.type==='radio'?value===c.id:Array.isArray(value)&&value.includes(c.id);
          row.append(input,document.createTextNode(c.label));wrap.append(row);inputs.push(input);
        } else {
          const input=document.createElement(f.type==='textarea'?'textarea':f.type==='dropdown'||f.type==='colour'&&f.choices?.length?'select':'input');
          input.id=key;label.htmlFor=key;
          if (input.tagName==='INPUT') input.type=({number:'number',checkbox:'checkbox',date:'date',colour:'color'})[f.type]||'text';
          if (input.tagName==='SELECT') {
            const blank=document.createElement('option');blank.value='';blank.textContent='Please choose';input.append(blank);
            for (const c of f.choices) {const option=document.createElement('option');option.value=f.type==='colour'?c.value:c.id;option.textContent=c.label;input.append(option);}
          }
          if (f.type==='checkbox') input.checked=value===true;else input.value=String(value);
          if(f.placeholder)input.setAttribute('placeholder',f.placeholder);
          for(const [source,target]of [['min_length','minlength'],['max_length','maxlength'],['minimum','min'],['maximum','max'],['min_date','min'],['max_date','max']])if(f[source]!=null)input.setAttribute(target,f[source]);
          if(f.type==='number')input.step=f.whole_number?'1':'any';
          if(f.required)input.required=true;
          wrap.append(input);inputs.push(input);
        }
        if(f.help_text){const help=document.createElement('small');help.id=`${key}-help`;help.textContent=f.help_text;wrap.append(help);inputs.forEach(i=>i.setAttribute('aria-describedby',help.id));}
        inputs.forEach(i=>i.addEventListener('input',update));inputs.forEach(i=>i.addEventListener('change',update));
        this.controls.set(f.id,inputs);this.container.append(wrap);
      }
    }
  }
  customElements.define('tlc-customer-fields',CustomerFields);
})();
