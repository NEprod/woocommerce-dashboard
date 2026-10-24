/* Local authoring only. No customer upload, Shopify mutation or order endpoint. */
(function (global) {
  "use strict";
  const aliases = {text:"text",multiline:"textarea",textarea:"textarea",number:"number",choice:"dropdown",dropdown:"dropdown",radio:"radio",checkbox:"checkbox",checkbox_group:"checkbox_group",date:"date",colour:"colour",photo:"file_upload",file_upload:"file_upload"};
  const types = {text:"Single-line text",multiline:"Multiline message",number:"Number",choice:"Dropdown",radio:"Radio selection",checkbox:"Checkbox",checkbox_group:"Checkbox group",date:"Date",colour:"Colour",photo:"File upload definition"};
  const clone = value => value === undefined ? undefined : structuredClone(value);
  const order = definition => Object.entries(definition?.fields || {}).sort(([a,x],[b,y]) => (x.order || 0)-(y.order || 0) || a.localeCompare(b));
  function appendFields(inherited, local) {
    const result=Object.fromEntries(order({fields:inherited}).map(([id,value])=>[id,clone(value)]));
    let previous=order({fields:result}).at(-1);
    for(const [id,field] of order({fields:local})) {
      if(Object.hasOwn(field,'remove'))throw Error(`customisation.fields.${id}.remove is obsolete. Remove the instruction manually; no draft was changed.`);
      if(Object.hasOwn(result,id))throw Error(`Duplicate effective customisation field ID: ${id}. Add a distinct field; inherited definitions are unchanged.`);
      if(!aliases[field.type]||!field.label?.trim())throw Error(`customisation.fields.${id} needs a complete type and label; inherited-field patches are not supported.`);
      const value=clone(field);
      if(previous && ((value.order||0)<(previous[1].order||0) || (value.order||0)===(previous[1].order||0)&&id.localeCompare(previous[0])<=0))value.order=(previous[1].order||0)+1;
      result[id]=value;previous=[id,value];
    }
    return result;
  }
  function resolve(base, shared, own, productOverride=false) {
    if(productOverride&&Object.hasOwn(own||{},'template'))throw Error('customisation.template is obsolete in Product Overrides. Assign templates on the Shared Collection/owning JSON only.');
    for(const layer of [shared,own])if(Object.hasOwn(layer||{},'template')&&layer.template===null)throw Error('customisation.template:null is obsolete. Remove the null key manually.');
    const result = clone(base || {});
    for (const layer of [shared || {}, own || {}]) {
      for (const [key,value] of Object.entries(layer)) if (key !== "fields") result[key] = clone(value);
      result.fields=appendFields(result.fields||{},layer.fields||{});
    }
    delete result.template;
    return result;
  }
  function changeField(own, inherited, id, changes) {
    if(inherited?.fields?.[id])throw Error(`Inherited field ${id} is read-only. Edit its collection or template.`);
    const result = clone(own || {}); result.fields ||= {};
    const field = {...result.fields[id]};
    for (const [key,value] of Object.entries(changes)) {
      if (value === undefined) delete field[key];
      else field[key] = clone(value);
    }
    result.fields[id] = field;
    return result;
  }
  function removeLocal(own, inherited, id) {
    if(inherited?.fields?.[id])throw Error(`Inherited field ${id} is read-only.`);
    const result = clone(own || {}); result.fields ||= {};
    delete result.fields[id];
    return result;
  }
  function reorder(own, effective, id, direction) {
    const rows = order(effective).filter(([key])=>Object.hasOwn(own?.fields||{},key)), index = rows.findIndex(([key])=>key===id), other=index+direction;
    if(index<0)throw Error('Inherited fields cannot be reordered in this layer.');
    if (other<0 || other>=rows.length) return clone(own);
    [rows[index],rows[other]]=[rows[other],rows[index]];
    const result=clone(own || {}); result.fields ||= {};
    rows.forEach(([key],position)=>{result.fields[key]={...result.fields[key],order:position};});
    return result;
  }
  const optionID = option => typeof option === "string" ? option : option.id;
  function validateAnswer(field, value) {
    const type=aliases[field.type], empty=value==null || value==="" || Array.isArray(value)&&!value.length || type==="checkbox"&&value===false;
    if (empty) return field.required ? "This field is required." : "";
    if (["text","textarea"].includes(type) && (value.length<(field.min_length||0)||value.length>(field.max_length??Infinity))) return "Text length is outside the configured limits.";
    if (type==="number" && (!Number.isFinite(value)||value<(field.minimum??-Infinity)||value>(field.maximum??Infinity)||field.whole_number&&!Number.isInteger(value))) return "Enter a number within the configured limits.";
    const ids=(field.choices||[]).map(optionID);
    if (["dropdown","radio"].includes(type)&&!ids.includes(value)) return "Choose an allowed option.";
    if (type==="checkbox_group"&&(!Array.isArray(value)||value.some(v=>!ids.includes(v))||value.length<(field.min_selections||0)||value.length>(field.max_selections??Infinity))) return "Choose the configured number of allowed options.";
    if (type==="date"&&(!/^\d{4}-\d{2}-\d{2}$/.test(value)||Number.isNaN(Date.parse(value+"T00:00:00Z"))||new Date(value+"T00:00:00Z").toISOString().slice(0,10)!==value||value<(field.min_date||value)||value>(field.max_date||value))) return "Choose a valid date within the limits.";
    if (type==="colour"&&(!/^#[0-9a-f]{6}$/i.test(value)||(ids.length&&!(field.choices||[]).some(c=>(typeof c==="string"?c:c.value)===value)))) return "Choose an approved colour.";
    return "";
  }
  const constraints = new Set(['min_length','max_length','minimum','maximum','whole_number','min_date','max_date','min_selections','max_selections','choices','allowed_media_categories','allowed_extensions','max_file_size_bytes','max_files','default']);
  function configurationError(field) {
    if (!field.label?.trim()) return 'Customer-facing label is required.';
    const type=aliases[field.type];
    const allowed={min_length:['text','textarea'],max_length:['text','textarea'],minimum:['number'],maximum:['number'],whole_number:['number'],min_date:['date'],max_date:['date'],min_selections:['checkbox_group'],max_selections:['checkbox_group'],choices:['dropdown','radio','checkbox_group','colour'],allowed_extensions:['file_upload'],allowed_media_categories:['file_upload'],max_files:['file_upload'],max_file_size_bytes:['file_upload']};
    for(const [key,kinds]of Object.entries(allowed))if(Object.hasOwn(field,key)&&!kinds.includes(type))return `${key.replaceAll('_',' ')} is not supported for this input type. Edit the owning collection/template for inherited fields.`;
    for (const [low,high,label] of [['min_length','max_length','length'],['minimum','maximum','value'],['min_selections','max_selections','selections'],['min_date','max_date','date']]) {
      if (field[low]!=null && field[high]!=null && field[low]>field[high]) return `Maximum ${label} must be greater than or equal to minimum ${label}.`;
    }
    for (const key of ['min_length','max_length','min_selections','max_selections','max_file_size_bytes','max_files']) {
      if (field[key]!=null && (!Number.isInteger(field[key]) || field[key] < (['min_length','min_selections'].includes(key)?0:1))) return `${({max_file_size_bytes:'Maximum file size',max_files:'Maximum number of files'})[key]||key.replaceAll('_',' ')} must be ${['min_length','min_selections'].includes(key)?'zero or greater':'greater than zero'} and a whole number.`;
    }
    const options=field.choices||[], ids=options.map(optionID);
    if (['dropdown','radio','checkbox_group'].includes(type) && !options.length) return 'Choose at least one option.';
    if (new Set(ids).size!==ids.length) return 'Option IDs must be unique.';
    for (const option of options) {
      if (typeof option==='string' ? !option.trim() : !/^[a-z][a-z0-9_]{0,63}$/.test(option.id)) return 'Choose a valid, unique stable option ID.';
      if (!(typeof option==='string'?option:option.label)?.trim()) return 'Every option needs a customer-facing label.';
      if (type==='colour' && !/^#[0-9a-f]{6}$/i.test(typeof option==='string'?option:option.value)) return 'Choose a valid six-digit colour for every palette option.';
    }
    for (const key of ['min_date','max_date']) if (field[key] && validateAnswer({type:'date'},field[key])) return 'Please choose a valid date.';
    if (type==='checkbox_group' && field.min_selections>options.length) return 'Minimum selections cannot exceed the number of options.';
    if (Object.hasOwn(field,'default')) {
      if (type==='number' && field.default!=null && field.default!=='' && (typeof field.default!=='number'||!Number.isFinite(field.default))) return 'Enter a valid numeric default or choose no default.';
      if (type==='checkbox' && typeof field.default!=='boolean' && field.default!==null) return 'Choose checked or unchecked for the default.';
      if (field.default!=null && validateAnswer({...field,required:false},field.default)) return ['dropdown','radio','checkbox_group'].includes(type)?'Default selection must match existing options and selection limits.':'Default value must satisfy this field’s validation rules.';
    }
    if (field.allowed_extensions?.some(extension=>!/^\.[a-z0-9]{1,10}$/.test(extension))) return 'Use extensions such as .png or .jpg, separated by commas.';
    if(field.allowed_extensions && new Set(field.allowed_extensions).size!==field.allowed_extensions.length)return 'Choose each allowed file extension only once.';
    return '';
  }
  function element(tag, text, className) {
    const node=document.createElement(tag); if(text!=null) node.textContent=text;
    if(className) node.className=className; return node;
  }
  function button(text, action) {
    const node=element("button",text,"btn btn-sm btn-ghost");node.type="button";node.addEventListener("click",action);return node;
  }
  async function request(url, options) {
    const response=await fetch(url,{credentials:"same-origin",...options});
    const data=await response.json(); if(!response.ok) throw new Error(data.error || "Local template request failed. Your draft is unchanged.");return data;
  }
  class Designer {
    constructor(root, {shared={},base={},value,onChange=()=>{},templatesURL="",reference=null,productOverride=false}={}) {
      this.root=root;this.shared=clone(shared);this.base=clone(base);this.value=clone(value);this.onChange=onChange;this.templatesURL=templatesURL;this.reference=reference;
      this.productOverride=productOverride;
      this.cache=new Map();if(reference)this.cache.set(JSON.stringify(reference),clone(base));
      this.fieldDialog=root.querySelector("[data-custom-field-dialog]");this.previewDialog=root.querySelector("[data-custom-preview-dialog]");
      root.querySelector("[data-custom-add]").addEventListener("click",()=>this.edit());
      root.querySelector("[data-custom-preview]").addEventListener("click",()=>this.preview());
      root.querySelector("[data-custom-close]").addEventListener("click",()=>this.fieldDialog.close());
      root.querySelector("[data-custom-preview-close]").addEventListener("click",()=>this.previewDialog.close());
      root.querySelector("[data-custom-apply-field]").addEventListener("click",()=>this.applyField());
      for (const dialog of [this.fieldDialog,this.previewDialog]) for (const event of ['input','change']) dialog.addEventListener(event,e=>e.stopPropagation());
      this.fieldDialog.addEventListener('keydown',event=>{if(event.key==='Enter' && event.target.tagName!=='TEXTAREA' && event.target.tagName!=='BUTTON'){event.preventDefault();this.applyField();}});
      this.fieldDialog.addEventListener('close',()=>{
        const target=this.editID?this.root.querySelector(`[data-custom-edit="${this.editID}"]`):this.root.querySelector('[data-custom-add]');
        (this.editTrigger?.isConnected?this.editTrigger:target)?.focus({preventScroll:true});
      });
      root.querySelector("[data-custom-proof]").addEventListener("change",event=>{this.value ||= {};if(event.target.value)this.value.proof_policy=event.target.value;else delete this.value.proof_policy;this.changed();});
      root.querySelector('[data-custom-storefront]')?.addEventListener('change', event=>{this.value ||= {};if(event.target.value==='')delete this.value.storefront_enabled;else this.value.storefront_enabled=event.target.value==='true';this.changed();});
      const templateControls=root.querySelector('[data-custom-template-controls]');
      if(templateControls)templateControls.hidden=this.productOverride;
      root.querySelector("[data-custom-apply-template]")?.addEventListener("click",()=>this.assign());
      this.render(); if(templatesURL&&!this.productOverride)this.loadLibrary();
    }
    get() { return clone(this.value); }
    effective() { return resolve(this.base,this.shared,this.value,this.productOverride); }
    inherited() { return resolve(this.base,this.shared,{}); }
    changed() {this.render();this.onChange(this.get());}
    error(message) {const node=this.root.querySelector("[data-custom-error]");node.hidden=!message;node.textContent=message;}
    async loadLibrary() {
      try {const {items}=await request(this.templatesURL);this.library=items;const select=this.root.querySelector("[data-custom-template]");select.replaceChildren(new Option("Choose a pinned revision",""));items.filter(t=>!t.archived).forEach(t=>select.add(new Option(`${t.name} · revision ${t.revision}`,JSON.stringify({id:t.id,revision:t.revision}))));}
      catch(error){this.error(error.message);}
    }
    currentReference() {return this.productOverride?this.shared.template:this.value?.template;}
    async restoreBase() {
      const ref=this.currentReference(),key=JSON.stringify(ref); if(!ref){this.base={};return;}
      try{const base=await this.templateBase(ref);if(JSON.stringify(this.currentReference())!==key)return;this.base=base;this.render();}catch(error){this.error(error.message);}
    }
    async templateBase(ref) {
      const key=JSON.stringify(ref);if(!this.cache.has(key))this.cache.set(key,(await request(`${this.templatesURL}/${encodeURIComponent(ref.id)}?revision=${ref.revision}`)).definition);
      return clone(this.cache.get(key));
    }
    async assign() {
      if(this.productOverride){this.error('Templates belong to the Shared Collection/owning editor. Product Overrides add distinct fields only.');return;}
      const selected=this.root.querySelector("[data-custom-template]").value;if(!selected)return;
      try{const ref=JSON.parse(selected),base=await this.templateBase(ref),next={...this.value,template:ref};resolve(base,this.shared,next);this.value=next;this.base=base;this.changed();}
      catch(error){this.error(error.message);}
    }
    render() {
      const target=this.root.querySelector("[data-custom-fields]");target.replaceChildren();const inherited=this.inherited();
      for(const [id,field] of order(this.effective())) {
        const row=element("article",null,"customisation-field-row"),info=element("div"),own=this.value?.fields?.[id];
        info.append(element("strong",field.label || id),element("code",id),element("span",`${types[field.type]||field.type} · ${field.enabled===false?"Disabled":field.required?"Required":"Optional"} · ${own?(this.productOverride?"Product addition":"Local field"):"Inherited"}`,"field-helper"));
        const actions=element("div",null,"customisation-actions");
        if(own){
          const editButton=button("Edit",()=>this.edit(id));editButton.dataset.customEdit=id;
          actions.append(editButton,button("Move Up",()=>{this.value=reorder(this.value,this.effective(),id,-1);this.changed();}),button("Move Down",()=>{this.value=reorder(this.value,this.effective(),id,1);this.changed();}),button("Remove local field",()=>{this.value=removeLocal(this.value,inherited,id);this.changed();}));
        }else info.append(element('span','Read-only — edit the owning collection or reusable template.','field-helper'));
        actions.append(button("Duplicate",()=>this.edit(null,field)));
        row.append(info,actions);target.append(row);
      }
      if(!target.children.length)target.append(element("p","No effective customer fields defined. Nothing is added to JSON until you edit."));
      const proof=this.root.querySelector("[data-custom-proof]");proof.options[0].textContent=`Inherited / unspecified${this.inherited().proof_policy?': '+this.inherited().proof_policy:''}`;proof.value=this.value?.proof_policy||"";
      const storefront=this.root.querySelector('[data-custom-storefront]');if(storefront){storefront.options[0].textContent=`Inherited / default: ${this.inherited().storefront_enabled===true?'enabled':'disabled'}`;storefront.value=typeof this.value?.storefront_enabled==='boolean'?String(this.value.storefront_enabled):'';}
      const ref=this.currentReference();this.root.querySelector("[data-custom-template-status]").textContent=ref?`Pinned template ${ref.id}, revision ${ref.revision}. Template fields are protected. Local fields append after inherited fields.`:"Shared fields are inherited and protected. Local fields append afterwards.";
    }
    edit(id=null,duplicate=null) {
      if(id!=null&&this.inherited().fields?.[id]){this.error('Inherited fields are read-only. Edit the owning collection or reusable template.');return;}
      const existing=id==null?null:this.effective().fields?.[id];
      if(id!=null&&!existing){this.error('This field is no longer available. Reload its definition before editing; no new field was created.');return;}
      this.editID=id;this.editTrigger=document.activeElement;
      this.inputs=null;
      this.editOriginal=clone(existing||duplicate||{type:"text",label:""});
      if(!existing)this.editOriginal.order=Math.max(-1,...Object.values(this.value?.fields||{}).map(field=>field.order||0))+1;
      this.root.querySelector('[data-custom-field-heading]').textContent=id?'Edit Field':duplicate?'Duplicate Field':'Add Field';
      this.buildInputs(this.editOriginal);
      this.fieldDialog.showModal();this.inputs[id?'label':'id'].focus();
    }
    buildInputs(field) {
      const previousID=this.inputs?.id?.value;
      const target=this.root.querySelector('[data-custom-field-inputs]');target.replaceChildren();this.inputs={};this.formValue=clone(field);this.defaultBox=null;
      const group=title=>{const box=element('fieldset',null,'customisation-config-group');box.append(element('legend',title));target.append(box);return box;};
      const basic=group('Basic'),behaviour=group('Behaviour'),validation=group('Validation');
      const input=(parent,key,label,type='text',value=field[key])=>{
        const wrapper=element('label',null,type==='checkbox'?'customisation-check':'customisation-control'),control=element(type==='textarea'?'textarea':'input');
        if(type!=='textarea')control.type=type;
        control.className=type==='checkbox'?'customisation-boolean':'form-control';control.dataset.customControl=key;
        if(type==='checkbox'){control.checked=!!value;control.dataset.initialChecked=String(control.checked);}
        else control.value=value??'';
        const text=element('span',label);wrapper.append(text,control);parent.append(wrapper);this.inputs[key]=control;return control;
      };
      const id=input(basic,'id','Stable field ID','text',this.editID||previousID||'');id.readOnly=!!this.editID;id.pattern='[a-z][a-z0-9_]{0,63}';id.maxLength=64;
      const label=input(basic,'label','Customer-facing label');label.maxLength=191;
      label.addEventListener('input',()=>{if(!this.editID&&!id.dataset.edited)id.value=label.value.toLowerCase().replace(/[^a-z0-9]+/g,'_').replace(/^\d+|^_+|_+$/g,'').slice(0,64);});id.addEventListener('input',()=>id.dataset.edited='true');
      const wrapper=element('label','Input type'),select=element('select');select.className='form-select';select.dataset.customControl='type';select.setAttribute('aria-label','Input type');
      const entries={...types};if(!entries[field.type])entries[field.type]=types[Object.keys(types).find(key=>aliases[key]===aliases[field.type])]||field.type;for(const [value,label]of Object.entries(entries))select.add(new Option(label,value));select.value=field.type;wrapper.append(select);basic.append(wrapper);this.inputs.type=select;
      input(behaviour,'enabled','Enabled','checkbox',field.enabled!==false);input(behaviour,'required','Required','checkbox');
      behaviour.append(element('p','Disabled fields are hidden in the local preview. Required is retained and applies when the field is enabled.','field-helper'));
      input(behaviour,'help_text','Customer help text','textarea').maxLength=4000;
      const type=aliases[field.type];if(['text','textarea','number'].includes(type))input(behaviour,'placeholder','Placeholder').maxLength=1000;
      const labels={min_length:'Minimum length',max_length:'Maximum length',minimum:'Minimum value',maximum:'Maximum value',min_selections:'Minimum selections',max_selections:'Maximum selections',max_file_size_bytes:'Maximum file size (bytes)',max_files:'Maximum number of files'};
      const numeric={text:['min_length','max_length'],textarea:['min_length','max_length'],number:['minimum','maximum'],checkbox_group:['min_selections','max_selections'],file_upload:['max_file_size_bytes','max_files']};
      for(const key of numeric[type]||[]){const node=input(validation,key,labels[key],'number');node.step=['minimum','maximum'].includes(key)?'any':'1';}
      if(type==='number')input(validation,'whole_number','Whole numbers only','checkbox');
      if(type==='date')for(const [key,label]of [['min_date','Earliest date'],['max_date','Latest date']])input(validation,key,label,'date');
      if(type==='file_upload'){
        input(validation,'allowed_extensions','Allowed extensions (e.g. .png, .jpg)','text',(field.allowed_extensions||[]).join(', '));
        this.mediaInputs=[];for(const category of ['image','document']){
          const node=input(validation,'media_'+category,category==='image'?'Allow images':'Allow documents','checkbox',(field.allowed_media_categories||[]).includes(category));this.mediaInputs.push([category,node]);delete this.inputs['media_'+category];
        }
        validation.append(element('p','File requirements only. No customer file can be selected or uploaded.','field-helper'));
      }
      this.optionValues=clone(field.choices||[]);this.newOptions=new WeakSet();this.defaultState={present:Object.hasOwn(field,'default'),value:clone(field.default)};
      if(['dropdown','radio','checkbox_group','colour'].includes(type)){
        this.optionBox=group(type==='colour'?'Approved colours (optional)':'Options');this.renderOptions();
      }
      if(type!=='file_upload'){this.defaultBox=group('Default');this.renderDefault();}
      select.addEventListener('change',()=>{
        const next=this.readInputs();next.type=select.value;for(const key of constraints)delete next[key];
        this.buildInputs(next);this.root.querySelector('[data-custom-dialog-error]').textContent='Input type changed. Review its options, default and validation before applying.';
      });
      this.root.querySelector('[data-custom-field-source]').textContent=(this.editID?`Field ${this.editID}. `:'Choose a unique field ID. ')+ 'Field IDs identify customer answers and remain stable after creation. You are editing only this layer’s local field; inherited fields remain unchanged.';
      this.root.querySelector('[data-custom-dialog-error]').textContent='';
    }
    renderOptions() {
      this.optionBox.replaceChildren(element('legend',aliases[this.formValue.type]==='colour'?'Approved colours (optional)':'Options'));
      this.optionValues.forEach((option,index)=>{
        const legacy=typeof option==='string',row=element('div',null,'customisation-option-row');
        const control=(label,value,type='text')=>{const wrapper=element('label',label),node=element('input');node.type=type;node.className='form-control';node.value=value;wrapper.append(node);row.append(wrapper);return node;};
        const id=control('Option ID',optionID(option));id.readOnly=legacy||!this.newOptions.has(option);id.maxLength=64;
        const label=control('Option label',legacy?option:option.label);label.readOnly=legacy;label.maxLength=191;
        if(legacy)row.append(element('p','Legacy option: its text is its identity. It is preserved unchanged; add a replacement and explicitly remove this option if necessary.','field-helper'));
        id.addEventListener('input',()=>{option.id=id.value;});id.addEventListener('change',()=>this.renderDefault());
        label.addEventListener('input',()=>{option.label=label.value;});label.addEventListener('change',()=>this.renderDefault());
        if(aliases[this.formValue.type]==='colour'){
          const colour=control('Option colour',legacy?option:option.value||'#000000','color');colour.disabled=legacy;colour.addEventListener('input',()=>{option.value=colour.value;this.renderDefault();});
        }
        row.append(button('Move option up',()=>{if(index>0){[this.optionValues[index-1],this.optionValues[index]]=[this.optionValues[index],this.optionValues[index-1]];this.renderOptions();this.renderDefault();}}),button('Move option down',()=>{if(index<this.optionValues.length-1){[this.optionValues[index+1],this.optionValues[index]]=[this.optionValues[index],this.optionValues[index+1]];this.renderOptions();this.renderDefault();}}),button('Remove option',()=>{this.optionValues.splice(index,1);this.renderOptions();this.renderDefault();}));
        this.optionBox.append(row);
      });
      this.optionBox.append(button('Add option',()=>{
        let n=1;while(this.optionValues.some(option=>optionID(option)===`option_${n}`))n++;
        const option={id:`option_${n}`,label:'',...(aliases[this.formValue.type]==='colour'?{value:'#000000'}:{})};this.newOptions.add(option);this.optionValues.push(option);this.renderOptions();this.renderDefault();
      }));
    }
    renderDefault() {
      if(!this.defaultBox)return;
      const type=aliases[this.formValue.type],state=this.defaultState;
      this.defaultBox.replaceChildren(element('legend','Default'));
      const label=element('label','Default value'),mode=element('select');mode.className='form-select';mode.dataset.customDefaultMode='';mode.setAttribute('aria-label','Default value');
      mode.add(new Option('No default value','none'));mode.add(new Option('Set a default','set'));
      if(state.present&&state.value===null)mode.add(new Option('Preserve legacy empty default','null'));
      mode.value=state.present?(state.value===null?'null':'set'):'none';label.append(mode);this.defaultBox.append(label);
      mode.addEventListener('change',()=>{state.present=mode.value!=='none';state.value=mode.value==='null'?null:undefined;this.renderDefault();});
      if(!state.present||state.value===null)return;
      if(type==='checkbox_group'){
        const values=Array.isArray(state.value)?state.value:[];
        const options=[...this.optionValues,...values.filter(id=>!this.optionValues.some(o=>optionID(o)===id)).map(id=>({id,label:`Unavailable default: ${id}`}))];
        for(const option of options){const label=element('label',null,'customisation-check'),check=element('input');check.type='checkbox';check.className='customisation-boolean';check.value=optionID(option);check.checked=values.includes(check.value);label.append(element('span',typeof option==='string'?option:option.label),check);this.defaultBox.append(label);check.addEventListener('change',()=>{state.value=Array.from(this.defaultBox.querySelectorAll('input:checked'),node=>node.value);});}
        if(state.value===undefined)state.value=[];
      }else{
        const palette=type==='colour'&&this.optionValues.length;
        const labelText=type==='checkbox'?'Default checked':({text:'Default text',textarea:'Default message',number:'Default number',date:'Default date',colour:'Default colour'})[type]||'Default selection';
        const label=element('label',labelText);
        const control=element(type==='textarea'?'textarea':['dropdown','radio'].includes(type)||palette?'select':'input');
        if(control.tagName==='SELECT'){
          control.className='form-select';control.add(new Option('Choose a default',''));
          for(const option of this.optionValues)control.add(new Option(typeof option==='string'?option:option.label,palette?(typeof option==='string'?option:option.value):optionID(option)));
          if(state.value!=null&&!Array.from(control.options).some(o=>o.value===String(state.value)))control.add(new Option(`Unavailable default: ${state.value}`,state.value));
        }else if(control.tagName==='INPUT')control.type=type==='checkbox'?'checkbox':type==='colour'?'color':type==='number'?'number':type==='date'?'date':'text';
        control.className=type==='checkbox'?'customisation-boolean':control.className||'form-control';if(type==='checkbox')label.className='customisation-check';
        control.setAttribute('aria-label',labelText);
        if(type==='number')control.step='any';
        if(type==='checkbox'){control.checked=state.value===true;if(state.value===undefined)state.value=false;}else if(state.value!==undefined)control.value=state.value;
        else if(!['number','dropdown','radio'].includes(type)&&!palette)state.value=control.value;
        label.append(control);this.defaultBox.append(label);
        const capture=()=>{state.value=type==='checkbox'?control.checked:type==='number'?(control.value===''?undefined:Number(control.value)):control.value;};
        control.addEventListener('input',capture);control.addEventListener('change',capture);
      }
    }
    readInputs() {
      const values=clone(this.formValue);for(const [key,node]of Object.entries(this.inputs)){
        if(key==="id")continue;
        if(node.type==='checkbox'){if(Object.hasOwn(this.formValue,key)||String(node.checked)!==node.dataset.initialChecked)values[key]=node.checked;else delete values[key];}
        else if(node.type==='number'){if(node.value!=='')values[key]=Number(node.value);else delete values[key];}
        else if(node.value!==''||Object.hasOwn(this.formValue,key))values[key]=node.value;else delete values[key];
      }
      const type=aliases[this.formValue.type];
      if(['dropdown','radio','checkbox_group','colour'].includes(type)){
        if(this.optionValues.length||Object.hasOwn(this.formValue,'choices'))values.choices=clone(this.optionValues);else delete values.choices;
      }
      if(type==='file_upload'){
        const categories=this.mediaInputs.filter(([,node])=>node.checked).map(([value])=>value);
        if(this.mediaInputs.some(([,node])=>String(node.checked)!==node.dataset.initialChecked)){
          if(categories.length)values.allowed_media_categories=categories;else delete values.allowed_media_categories;
        }
      }
      for(const key of ["allowed_extensions","allowed_media_categories"])if(typeof values[key]==='string')values[key]=values[key].split(",").map(v=>v.trim()).filter(Boolean);
      if(type!=='file_upload'){if(this.defaultState.present)values.default=clone(this.defaultState.value);else delete values.default;}
      return values;
    }
    applyField() {
      try{
        for(const node of Object.values(this.inputs))if(node.validity?.badInput)throw new Error(node.type==='date'?'Please choose a valid date.':'Please enter a valid number.');
        if(this.defaultState.present && this.defaultState.value===undefined)throw new Error('Choose a default value or select no default override.');
        const id=this.inputs.id.value,values=this.readInputs();if(!/^[a-z][a-z0-9_]{0,63}$/.test(id))throw new Error("Use a stable lowercase field ID.");
        if(this.editID&&id!==this.editID)throw new Error('Field IDs remain stable after creation. Reopen the existing field.');
        if(!this.editID&&Object.hasOwn(this.effective().fields||{},id))throw new Error("Field ID already exists. Choose another ID.");
        const problem=configurationError(values);if(problem)throw new Error(problem);
        const typeConstraints=constraints;
        const changes={};for(const key of new Set([...Object.keys(this.inputs),...Object.keys(this.editOriginal)])){
          if(key==='id')continue;
          if(JSON.stringify(values[key])!==JSON.stringify(this.editOriginal[key]))changes[key]=values[key];
        }
        for(const key of ['choices','default','allowed_media_categories'])if(JSON.stringify(values[key])!==JSON.stringify(this.editOriginal[key]))changes[key]=values[key];
        if(!this.editID){Object.assign(changes,values);for(const [key,value]of Object.entries(this.editOriginal))if(!typeConstraints.has(key)&&!Object.hasOwn(changes,key))changes[key]=value;}
        if(this.editID&&!Object.keys(changes).length){this.fieldDialog.close();return;}
        const next=changeField(this.value,this.inherited(),id,changes);
        const effective=resolve(this.base,this.shared,next,this.productOverride).fields[id];
        const inheritedProblem=configurationError(effective);if(inheritedProblem)throw new Error(inheritedProblem);
        this.value=next;this.changed();this.fieldDialog.close();
      }catch(error){this.root.querySelector("[data-custom-dialog-error]").textContent=error.message;}
    }
    preview() {
      const form=this.root.querySelector("[data-custom-preview-form]");form.replaceChildren();const readers=[];
      for(const [id,field]of order(this.effective())){
        if(field.enabled===false)continue;const type=aliases[field.type],group=element("fieldset"),legend=element("legend",`${field.label}${field.required?" *":""}`);group.append(legend);
        if(field.help_text)group.append(element("p",field.help_text,"field-helper"));
        if(type==="file_upload") {group.append(element("p",`Upload definition only — no file input or network upload. ${JSON.stringify({extensions:field.allowed_extensions,categories:field.allowed_media_categories,max_bytes:field.max_file_size_bytes,max_files:field.max_files})}`));form.append(group);continue;}
        let read;
        if(["radio","checkbox_group"].includes(type)){
          const controls=[];for(const option of field.choices||[]){const label=element("label",typeof option==="string"?option:option.label,'customisation-check'),control=element("input");control.type=type==="radio"?"radio":"checkbox";control.className='customisation-boolean';control.name=`preview_${id}`;control.value=optionID(option);control.checked=type==="radio"?field.default===control.value:(field.default||[]).includes(control.value);label.prepend(control);group.append(label);controls.push(control);}
          read=()=>type==="radio"?controls.find(c=>c.checked)?.value||"":controls.filter(c=>c.checked).map(c=>c.value);
        }else{
          const palette=type==="colour"&&field.choices?.length;
          const control=element(type==="textarea"?"textarea":type==="dropdown"||palette?"select":"input");control.className=type==='checkbox'?'customisation-boolean':'form-control';control.setAttribute("aria-label",field.label);
          if(control.tagName==="SELECT"){control.add(new Option("Choose…",""));for(const option of field.choices||[])control.add(new Option(typeof option==="string"?option:option.label,palette?(typeof option==="string"?option:option.value):optionID(option)));}
          else if(control.tagName==="INPUT")control.type=type==="checkbox"?"checkbox":type==="colour"?"color":type==="number"?"number":type==="date"?"date":"text";
          if(type==="number")control.step=field.whole_number?"1":"any";
          if(field.placeholder)control.placeholder=field.placeholder;
          if(type==="checkbox")control.checked=field.default===true;else if(field.default!=null)control.value=field.default;
          group.append(control);read=()=>type==="checkbox"?control.checked:type==="number"&&control.value!==""?Number(control.value):control.value;
        }
        const error=element("p",null,"field-error");error.setAttribute("role","status");group.append(error);readers.push(()=>{error.textContent=validateAnswer(field,read());return !error.textContent;});form.append(group);
      }
      const submit=element("button","Test local validation","btn btn-primary");submit.type="button";form.append(submit);
      submit.addEventListener('click',event=>{event.preventDefault();readers.forEach(read=>read());});this.previewDialog.showModal();
    }
  }
  global.TLCCustomisations={resolve,order,changeField,removeLocal,reorder,validateAnswer,configurationError,Designer,request};
})(typeof window==="undefined"?globalThis:window);
