import {batchMaterial} from './recipe-quantity.mjs';
const host=document.querySelector('[data-component-group]');
if(host){
  const saved=host.elements.namedItem('components'), body=host.querySelector('tbody');
  let parts=JSON.parse(saved.value);
  const make=(tag,text)=>{const el=document.createElement(tag);if(text!==undefined)el.textContent=text;return el;};
  const materialOptions=document.querySelector('[data-group-material-options]');
  const unitOptions=document.querySelector('[data-group-unit-options]');
  const names=new Map([...materialOptions.content.querySelectorAll('option')].map(o=>[o.value,o.textContent]));
  function paint(){
    body.replaceChildren();saved.value=JSON.stringify(parts);
    host.querySelector('[data-group-empty]').hidden=parts.length>0;
    host.querySelector('[data-add-component]').disabled=parts.length>=30;
    parts.forEach((part,index)=>{
      const row=body.insertRow();
      const values=[`${host.elements.group_name.value||'Group'} - ${part.name}`,part.code,
        part.yield_qty||host.elements.yield_qty.value||'Not set',
        part.materials.map(m=>`${names.get(String(m.item_id))||'Material'}: ${m.quantity} ${m.unit}`).join('; ')];
      for(const [i,value]of values.entries()){const cell=row.insertCell();cell.textContent=value;cell.dataset.column=host.querySelectorAll('thead th')[i].textContent;}
      const actions=row.insertCell();actions.dataset.column='Actions';actions.className='bom-row-actions';
      const edit=make('button','✎');edit.type='button';edit.className='btn btn-outline-primary';edit.title=`Edit ${part.name}`;edit.setAttribute('aria-label',edit.title);edit.onclick=()=>open(index);
      const remove=make('button','Remove');remove.type='button';remove.className='btn btn-outline-danger';remove.onclick=()=>{if(confirm(`Remove unsaved component ${part.name}?`)){parts.splice(index,1);paint();}};
      actions.append(edit,remove);
    });
  }
  function open(index){
    const draft=index===undefined?{name:'',code:'',yield_qty:'',materials:[]}:structuredClone(parts[index]);
    const dialog=make('dialog');dialog.className='packaging-dialog bom-dialog';dialog.setAttribute('aria-label',index===undefined?'Add Component':'Edit Component');
    const form=make('form');form.append(make('h2',index===undefined?'Add Component':'Edit Component'));
    const fields=make('div');fields.className='workshop-fields';
    function input(label,name,value,required=false){const wrap=make('label',label),control=make('input');control.className='form-control';control.name=name;control.value=value;control.required=required;control.maxLength=name==='code'?24:120;wrap.append(control);fields.append(wrap);return control;}
    input('Component name — e.g. Eyes','name',draft.name,true);
    const barcode=input('Unique component barcode','code',draft.code,true);barcode.pattern='[A-Za-z0-9][A-Za-z0-9\\-]{0,23}';
    input('Expected good pieces per batch — blank uses shared default','yield_qty',draft.yield_qty).inputMode='numeric';
    form.append(fields,make('p','Material quantities are per ONE good component. Batch totals below exclude the separate costing allowance.'));
    const materials=make('div');form.append(materials);
    const error=make('p');error.className='field-error';error.setAttribute('role','status');
    function addMaterial(line={item_id:'',quantity:'',unit:''}){
      const block=make('fieldset');block.className='workshop-panel';block.append(make('legend','Recipe material'));
      const grid=make('div');grid.className='workshop-fields';block.append(grid);
      function select(label,key,template,value){const wrap=make('label',label),el=make('select');el.className='form-select';el.name=key;el.setAttribute('aria-label',label);el.required=true;el.append(template.content.cloneNode(true));el.value=String(value);wrap.append(el);grid.append(wrap);return el;}
      const material=select('Raw material','item_id',materialOptions,line.item_id);
      const label=make('label','Quantity per good component'),quantity=make('input');quantity.className='form-control';quantity.name='quantity';quantity.inputMode='decimal';quantity.value=line.quantity;quantity.required=true;label.append(quantity);grid.append(label);
      const unit=select('Recipe unit','unit',unitOptions,line.unit);
      const total=make('p');total.className='field-helper';total.setAttribute('aria-live','polite');block.append(total);
      function update(){const amount=batchMaterial(quantity.value.trim(),form.elements.yield_qty.value.trim()||host.elements.yield_qty.value.trim(),unit.value);total.textContent=amount===null?'Enter a positive quantity, whole batch yield and unit.':`Total expected batch material: ${amount} ${unit.value}`;}
      material.onchange=()=>{unit.value=material.selectedOptions[0]?.dataset.unit||'';update();};
      form.addEventListener('input',update);unit.onchange=update;
      const remove=make('button','Remove material');remove.type='button';remove.className='btn btn-outline-danger';remove.onclick=()=>block.remove();block.append(remove);materials.append(block);update();
    }
    for(const line of draft.materials)addMaterial(line);
    if(!draft.materials.length)addMaterial();
    const add=make('button','Add Material');add.type='button';add.className='btn btn-outline-primary';add.onclick=()=>{if(materials.children.length<30)addMaterial();};form.append(add,error);
    const footer=make('div');footer.className='modal-actions';const apply=make('button','Apply Component');apply.className='btn btn-primary';apply.type='submit';const cancel=make('button','Cancel');cancel.className='btn btn-ghost';cancel.type='button';cancel.onclick=()=>dialog.close();footer.append(apply,cancel);form.append(footer);
    form.onsubmit=event=>{
      event.preventDefault();error.textContent='';
      const next={name:form.elements.name.value.trim(),code:form.elements.code.value.trim().toUpperCase(),yield_qty:form.elements.yield_qty.value.trim(),materials:[]};
      for(const block of materials.children){const line=Object.fromEntries([...block.querySelectorAll('input,select')].map(c=>[c.name,c.value.trim()]));if(batchMaterial(line.quantity,next.yield_qty||host.elements.yield_qty.value.trim(),line.unit)===null){error.textContent='Check each material quantity, unit and batch yield.';return;}next.materials.push(line);}
      if(!next.materials.length){error.textContent='Add at least one recipe material.';return;}
      if(new Set(next.materials.map(m=>m.item_id)).size!==next.materials.length){error.textContent='Combine duplicate raw materials into one recipe line.';return;}
      if(parts.some((p,i)=>i!==index&&(p.name.toLowerCase()===next.name.toLowerCase()||p.code.toUpperCase()===next.code))){error.textContent='Use a distinct component name and barcode.';return;}
      if(index===undefined)parts.push(next);else parts[index]=next;
      paint();dialog.close();
    };
    dialog.append(form);document.body.append(dialog);dialog.addEventListener('close',()=>dialog.remove(),{once:true});dialog.showModal();
  }
  host.querySelector('[data-add-component]').onclick=()=>open();
  for(const name of ['group_name','yield_qty'])host.elements.namedItem(name).addEventListener('input',paint);
  host.addEventListener('submit',event=>{saved.value=JSON.stringify(parts);if(!parts.length){event.preventDefault();host.querySelector('[data-group-empty]').textContent='Add at least one component before saving.';}});
  paint();
}
