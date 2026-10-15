/* Local authoring only. No customer upload, Shopify mutation or order endpoint. */
(function (global) {
  "use strict";
  const aliases = {text:"text",multiline:"textarea",textarea:"textarea",number:"number",choice:"dropdown",dropdown:"dropdown",radio:"radio",checkbox:"checkbox",checkbox_group:"checkbox_group",date:"date",colour:"colour",photo:"file_upload",file_upload:"file_upload"};
  const types = {text:"Single-line text",multiline:"Multiline message",number:"Number",choice:"Dropdown",radio:"Radio selection",checkbox:"Checkbox",checkbox_group:"Checkbox group",date:"Date",colour:"Colour",photo:"File upload definition"};
  const clone = value => value === undefined ? undefined : structuredClone(value);
  const order = definition => Object.entries(definition?.fields || {}).sort(([a,x],[b,y]) => (x.order || 0)-(y.order || 0) || a.localeCompare(b));
  function resolve(base, shared, own) {
    const result = clone(base || {});
    for (const layer of [shared || {}, own || {}]) {
      for (const [key,value] of Object.entries(layer)) if (key !== "fields") result[key] = clone(value);
      result.fields ||= {};
      for (const [id,patch] of Object.entries(layer.fields || {})) {
        if (patch.remove) delete result.fields[id];
        else result.fields[id] = {...result.fields[id], ...clone(patch)};
      }
    }
    delete result.template;
    return result;
  }
  function changeField(own, inherited, id, changes) {
    const result = clone(own || {}); result.fields ||= {};
    const patch = {...result.fields[id]}; delete patch.remove;
    for (const [key,value] of Object.entries(changes)) {
      if (value === undefined || JSON.stringify(value) === JSON.stringify(inherited?.fields?.[id]?.[key])) delete patch[key];
      else patch[key] = clone(value);
    }
    if (Object.keys(patch).length) result.fields[id] = patch; else delete result.fields[id];
    return result;
  }
  function exclude(own, inherited, id) {
    const result = clone(own || {}); result.fields ||= {};
    if (inherited?.fields?.[id]) result.fields[id] = {remove:true}; else delete result.fields[id];
    return result;
  }
  function reorder(own, effective, id, direction) {
    const rows = order(effective), index = rows.findIndex(([key])=>key===id), other=index+direction;
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
    constructor(root, {shared={},base={},value,onChange=()=>{},templatesURL="",reference=null}={}) {
      this.root=root;this.shared=clone(shared);this.base=clone(base);this.value=clone(value);this.onChange=onChange;this.templatesURL=templatesURL;this.reference=reference;
      this.cache=new Map();if(reference)this.cache.set(JSON.stringify(reference),clone(base));
      this.fieldDialog=root.querySelector("[data-custom-field-dialog]");this.previewDialog=root.querySelector("[data-custom-preview-dialog]");
      root.querySelector("[data-custom-add]").addEventListener("click",()=>this.edit());
      root.querySelector("[data-custom-reset]").addEventListener("click",()=>{if(global.confirm("Reset this layer's customisations to inherited values? Shared data is not changed.")){this.value=undefined;this.restoreBase();this.changed();}});
      root.querySelector("[data-custom-preview]").addEventListener("click",()=>this.preview());
      root.querySelector("[data-custom-close]").addEventListener("click",()=>this.fieldDialog.close());
      root.querySelector("[data-custom-preview-close]").addEventListener("click",()=>this.previewDialog.close());
      root.querySelector("[data-custom-apply-field]").addEventListener("click",()=>{if(Object.values(this.inputs).every(input=>input.reportValidity()))this.applyField();});
      root.querySelector("[data-custom-proof]").addEventListener("change",event=>{this.value ||= {};if(event.target.value)this.value.proof_policy=event.target.value;else delete this.value.proof_policy;this.changed();});
      root.querySelector("[data-custom-apply-template]").addEventListener("click",()=>this.assign());
      root.querySelector("[data-custom-no-template]").addEventListener("click",()=>{this.value ||= {};this.value.template=null;this.base={};this.changed();});
      root.querySelector("[data-custom-inherit-template]").addEventListener("click",()=>{this.value ||= {};delete this.value.template;this.restoreBase();this.changed();});
      this.render(); if(templatesURL)this.loadLibrary();
    }
    get() { return clone(this.value); }
    effective() { return resolve(this.base,this.shared,this.value); }
    inherited() { return resolve(this.base,this.shared,{}); }
    changed() {this.render();this.onChange(this.get());}
    error(message) {const node=this.root.querySelector("[data-custom-error]");node.hidden=!message;node.textContent=message;}
    async loadLibrary() {
      try {const {items}=await request(this.templatesURL);this.library=items;const select=this.root.querySelector("[data-custom-template]");select.replaceChildren(new Option("Choose a pinned revision",""));items.filter(t=>!t.archived).forEach(t=>select.add(new Option(`${t.name} · revision ${t.revision}`,JSON.stringify({id:t.id,revision:t.revision}))));}
      catch(error){this.error(error.message);}
    }
    currentReference() {return Object.hasOwn(this.value||{},"template")?this.value.template:this.shared.template;}
    async restoreBase() {
      const ref=this.currentReference(),key=JSON.stringify(ref); if(!ref){this.base={};return;}
      try{const base=await this.templateBase(ref);if(JSON.stringify(this.currentReference())!==key)return;this.base=base;this.render();}catch(error){this.error(error.message);}
    }
    async templateBase(ref) {
      const key=JSON.stringify(ref);if(!this.cache.has(key))this.cache.set(key,(await request(`${this.templatesURL}/${encodeURIComponent(ref.id)}?revision=${ref.revision}`)).definition);
      return clone(this.cache.get(key));
    }
    async assign() {
      const selected=this.root.querySelector("[data-custom-template]").value;if(!selected)return;
      try{const ref=JSON.parse(selected),base=await this.templateBase(ref);this.value ||= {};this.value.template=ref;this.base=base;this.changed();}
      catch(error){this.error(error.message);}
    }
    render() {
      const target=this.root.querySelector("[data-custom-fields]");target.replaceChildren();const inherited=this.inherited();
      for(const [id,field] of order(this.effective())) {
        const row=element("article",null,"customisation-field-row"),info=element("div"),own=this.value?.fields?.[id];
        info.append(element("strong",field.label || id),element("code",id),element("span",`${types[field.type]||field.type} · ${field.enabled===false?"Disabled":field.required?"Required":"Optional"} · ${own?inherited.fields?.[id]?"Overridden":"Local extension":"Inherited"}`,"field-helper"));
        const actions=element("div",null,"customisation-actions");
        actions.append(button(own?"Edit":"Override",()=>this.edit(id)),button("Duplicate",()=>this.edit(null,field)),button("Move Up",()=>{this.value=reorder(this.value,this.effective(),id,-1);this.changed();}),button("Move Down",()=>{this.value=reorder(this.value,this.effective(),id,1);this.changed();}),button("Remove / exclude",()=>{this.value=exclude(this.value,inherited,id);this.changed();}));
        if(own)actions.append(button("Reset to inherited",()=>{delete this.value.fields[id];this.changed();}));
        row.append(info,actions);target.append(row);
      }
      for(const [id,patch] of Object.entries(this.value?.fields||{}))if(patch.remove){const row=element("p",`${id} — excluded for this layer. `);row.append(button("Reset to inherited",()=>{delete this.value.fields[id];this.changed();}));target.append(row);}
      if(!target.children.length)target.append(element("p","No effective customer fields defined. Nothing is added to JSON until you edit."));
      const proof=this.root.querySelector("[data-custom-proof]");proof.options[0].textContent=`Inherited / unspecified${this.inherited().proof_policy?': '+this.inherited().proof_policy:''}`;proof.value=this.value?.proof_policy||"";
      const ref=this.currentReference();this.root.querySelector("[data-custom-template-status]").textContent=ref?`Pinned template ${ref.id}, revision ${ref.revision}. Library edits do not change this assignment. Local/shared patches take precedence.`:"No template base. Shared and local fields are still retained.";
    }
    edit(id=null,duplicate=null) {
      this.editID=id;this.editOriginal=clone(duplicate||this.effective().fields?.[id]||{type:"text",label:"",order:order(this.effective()).length});
      this.buildInputs(this.editOriginal);this.fieldDialog.showModal();
    }
    buildInputs(field) {
      const target=this.root.querySelector("[data-custom-field-inputs]");target.replaceChildren();this.inputs={};
      const input=(key,label,type="text",value=field[key])=>{
        const wrapper=element("label",label),control=element(type==="textarea"?"textarea":"input");if(type!=="textarea")control.type=type;control.className="form-control";
        if(type==="checkbox")control.checked=!!value;else control.value=value??"";
        wrapper.append(control);target.append(wrapper);this.inputs[key]=control;return control;
      };
      const id=input("id","Stable field ID","text",this.editID||"");id.required=true;id.pattern="[a-z][a-z0-9_]{0,63}";id.disabled=!!this.editID;
      const label=input("label","Customer-facing label");label.required=true;
      label.addEventListener("input",()=>{if(!this.editID&&!id.dataset.edited)id.value=label.value.toLowerCase().replace(/[^a-z0-9]+/g,"_").replace(/^\d+|^_+|_+$/g,"").slice(0,64);});id.addEventListener("input",()=>id.dataset.edited="true");
      const wrapper=element("label","Input type"),select=element("select");select.className="form-select";
      const entries={...types};if(!entries[field.type])entries[field.type]=field.type;for(const [value,label]of Object.entries(entries))select.add(new Option(label,value));select.value=field.type;wrapper.append(select);target.append(wrapper);this.inputs.type=select;
      input("required","Required","checkbox");input("enabled","Enabled","checkbox",field.enabled!==false);
      input("help_text","Customer help text","textarea");input("placeholder","Placeholder");
      const type=aliases[field.type];
      const numeric={text:["min_length","max_length"],textarea:["min_length","max_length"],number:["minimum","maximum"],checkbox_group:["min_selections","max_selections"],file_upload:["max_file_size_bytes","max_files"]};
      for(const key of numeric[type]||[])input(key,key.replaceAll("_"," "),"number");
      if(type==="number")input("whole_number","Whole number only","checkbox");
      if(type==="date")for(const key of ["min_date","max_date"])input(key,key.replaceAll("_"," "),"date");
      if(["dropdown","radio","checkbox_group","colour"].includes(type))input("choices","Options: one stable_id | Label | optional value per line (colour value: #RRGGBB)","textarea",(field.choices||[]).map(c=>typeof c==="string"?c:`${c.id} | ${c.label}${c.value!=null?" | "+c.value:""}`).join("\n"));
      if(type==="file_upload"){
        input("allowed_extensions","Allowed extensions, comma-separated (e.g. .png, .jpg)","text",(field.allowed_extensions||[]).join(", "));
        input("allowed_media_categories","Media categories: image, document","text",(field.allowed_media_categories||[]).join(", "));
      }else input("default","Default (checkbox: true/false; checkbox group: JSON array)","text",typeof field.default==="object"?JSON.stringify(field.default):field.default);
      select.addEventListener("change",()=>{const next=this.readInputs();next.type=select.value;for(const key of [...Object.keys(numeric).flatMap(k=>numeric[k]),"choices","min_date","max_date","whole_number","allowed_extensions","allowed_media_categories","default"])delete next[key];this.buildInputs(next);});
      this.root.querySelector("[data-custom-field-source]").textContent=this.editID?`ID ${this.editID} is stable. Blank constraints reset this layer's override; inherited constraints remain.`:"Choose a unique stable ID. Duplicating creates an independent field; labels may later change.";
      this.root.querySelector("[data-custom-dialog-error]").textContent="";
    }
    readInputs() {
      const values={};for(const [key,node]of Object.entries(this.inputs)){
        if(key==="id")continue;
        if(node.type==="checkbox")values[key]=node.checked;
        else if(node.type==="number"){if(node.value!=="")values[key]=Number(node.value);}
        else if(node.value!=="")values[key]=node.value;
      }
      if(values.choices)values.choices=values.choices.split("\n").filter(v=>v.trim()).map(line=>{const parts=line.split("|").map(v=>v.trim());return parts.length===1?parts[0]:{id:parts[0],label:parts[1],...(parts[2]?{value:parts[2]}:{})};});
      for(const key of ["allowed_extensions","allowed_media_categories"])if(values[key])values[key]=values[key].split(",").map(v=>v.trim()).filter(Boolean);
      if(Object.hasOwn(values,"default")){
        const type=aliases[values.type];if(["number","checkbox","checkbox_group"].includes(type))values.default=JSON.parse(values.default);
      }
      return values;
    }
    applyField() {
      try{
        const id=this.inputs.id.value,values=this.readInputs();if(!/^[a-z][a-z0-9_]{0,63}$/.test(id))throw new Error("Use a stable lowercase field ID.");
        if(!this.editID&&Object.hasOwn(this.effective().fields||{},id))throw new Error("That field ID already exists. Choose another ID.");
        const typeConstraints=new Set(['min_length','max_length','minimum','maximum','whole_number','min_date','max_date','min_selections','max_selections','choices','allowed_media_categories','allowed_extensions','max_file_size_bytes','max_files','default']);
        const changes={};for(const key of new Set([...Object.keys(this.inputs),...Object.keys(this.editOriginal)])){
          if(key==="id"||(!Object.hasOwn(this.inputs,key)&&(this.editOriginal.type===values.type||!typeConstraints.has(key))))continue;
          if(JSON.stringify(values[key])!==JSON.stringify(this.editOriginal[key]))changes[key]=values[key];
        }
        if(!this.editID){Object.assign(changes,values);for(const [key,value]of Object.entries(this.editOriginal))if(!typeConstraints.has(key)&&!Object.hasOwn(changes,key))changes[key]=value;}
        this.value=changeField(this.value,this.inherited(),id,changes);this.changed();this.fieldDialog.close();
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
          const controls=[];for(const option of field.choices||[]){const label=element("label",typeof option==="string"?option:option.label),control=element("input");control.type=type==="radio"?"radio":"checkbox";control.name=`preview_${id}`;control.value=optionID(option);control.checked=type==="radio"?field.default===control.value:(field.default||[]).includes(control.value);label.prepend(control);group.append(label);controls.push(control);}
          read=()=>type==="radio"?controls.find(c=>c.checked)?.value||"":controls.filter(c=>c.checked).map(c=>c.value);
        }else{
          const palette=type==="colour"&&field.choices?.length;
          const control=element(type==="textarea"?"textarea":type==="dropdown"||palette?"select":"input");control.className="form-control";control.setAttribute("aria-label",field.label);
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
  global.TLCCustomisations={resolve,order,changeField,exclude,reorder,validateAnswer,Designer,request};
})(typeof window==="undefined"?globalThis:window);
