import {materialDialog,paintMaterial} from './hypothetical-material.js';
import {processDialog} from './production-process.js';
import {bulkComponentDialog} from './bulk-bom-components.js';
async function inventoryPreview(table, row) {
  const body={...Object.fromEntries([...row.querySelectorAll('input,select')].map(input=>[input.name,input.value])),purpose:table.dataset.bomRows==='packaging'?'packaging':'material'};
  if(!body.item_id)throw new Error('Choose an inventory material / component.');
  const response=await fetch(table.dataset.hypotheticalPreview,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRFToken':document.querySelector('meta[name="csrf-token"]')?.content||''},body:JSON.stringify(body)});
  const data=await response.json();if(!response.ok)throw new Error(data.error||'Check inventory item, quantity and compatible unit.');return data;
}
function inventoryDialog(form,table,apply) {
  const output=document.createElement('p');output.className='alert alert-info';output.setAttribute('role','status');form.prepend(output);
  let ready=false,generation=0,timer,lastSignature;
  function update(){const signature=JSON.stringify([...form.querySelectorAll('input,select')].map(input=>input.value));if(signature===lastSignature)return;lastSignature=signature;ready=false;apply.disabled=true;const current=++generation;clearTimeout(timer);timer=setTimeout(async()=>{try{const data=await inventoryPreview(table,form);if(current!==generation)return;output.textContent=data.amount===null?'Cost unknown; estimate will be incomplete.':`Material/component cost: £${data.display_amount}. Recipe/print allowances are calculated separately.`;ready=true;apply.disabled=false;}catch(error){if(current===generation)output.textContent=error.message;}},200);}
  form.addEventListener('input',update);form.addEventListener('change',update);update();return()=>ready;
}
export function initBomRows(scope=document,{readOnly=false}={}) {
for (const table of scope.querySelectorAll('table[data-bom-rows]')) {
  if(table.dataset.rowsInitialized)continue;
  table.dataset.rowsInitialized='yes';
  const kind=table.dataset.bomRows, label=table.dataset.addLabel, body=table.tBodies[0];
  const template=scope.querySelector(`template[data-row-template="${kind}"]`);
  if(!template)continue; // Product BOM component lines remain read-only.
  const heading=document.createElement('th');heading.textContent='Actions';table.tHead.rows[0].append(heading);
  function paint(row) {
    for(const [index,cell] of [...row.cells].entries()) {
      cell.dataset.column=table.tHead.rows[0].cells[index].textContent;
      cell.querySelector('[data-row-summary]')?.remove();
      const inputs=cell.querySelectorAll('input,select');if(!inputs.length)continue;
      for(const input of inputs){input.hidden=true;input.required=false;}
      const summary=document.createElement('span');summary.dataset.rowSummary='';summary.className='text-break';
      summary.textContent=[...inputs].filter(input=>!['cost_mode','purchase_cost','purchase_quantity','purchase_unit','conversion_factor','cost'].some(suffix=>input.name.endsWith('_'+suffix))).map(input=>input.tagName==='SELECT'?input.selectedOptions[0]?.textContent:input.value||'—').join(' · ');
      if(kind==='manual'||kind==='pack')summary.textContent=kind==='pack'&&index===0&&!inputs[0].value?cell.querySelector('[name=pack_name]').value||'—':inputs[0].tagName==='SELECT'?inputs[0].selectedOptions[0]?.textContent:inputs[0].value||'—';
      cell.append(summary);
    }
    if(kind==='manual'||kind==='pack')paintMaterial(row,table);
    if(['inventory','packaging'].includes(kind))inventoryPreview(table,row).then(data=>{row.querySelector('[data-inventory-total]').textContent=data.amount===null?'Unknown':'£'+data.display_amount;}).catch(error=>{row.querySelector('[data-inventory-total]').textContent=error.message;});
    if(kind==='process') {
      row.classList.toggle('bom-single-process',row.querySelector('[name=timing_mode]').value==='item');
      const values=Object.fromEntries([...row.querySelectorAll('input,select')].map(input=>[input.name,input.value]));
      const total=Number(values.minutes), payload={...values,runtime_hours:String(Math.floor(total/60)),runtime_minutes:String(total%60)};
      const target=row.querySelector('[data-process-cost]'), signature=JSON.stringify(values);target.dataset.signature=signature;
      fetch(table.dataset.processPreview,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRFToken':document.querySelector('meta[name="csrf-token"]')?.content||''},body:JSON.stringify(payload)}).then(async response=>{const data=await response.json();if(target.dataset.signature===signature)target.textContent=response.ok?(data.electricity===null?'Unknown':'£'+data.electricity):data.error;}).catch(()=>{target.textContent='Preview unavailable';});
    }
  }
  function edit(row,isNew=false) {
    const dialog=document.createElement('dialog');dialog.className='packaging-dialog bom-dialog';dialog.setAttribute('aria-label',label.replace('Add','Edit'));
    const form=document.createElement('form'),title=document.createElement('h2');title.textContent=label.replace('Add','Edit');form.append(title);
    const grid=document.createElement('div');grid.className='workshop-fields';const pairs=[];
    for(const [index,cell] of [...row.cells].entries())for(const original of cell.querySelectorAll('input,select')) {
      const field=document.createElement('label');field.textContent=original.dataset.label||table.tHead.rows[0].cells[index].textContent;
      const input=original.cloneNode(true);input.hidden=false;input.value=original.value;field.append(input);grid.append(field);pairs.push([original,input]);
    }
    form.append(grid);const footer=document.createElement('div');footer.className='page-actions';
    const apply=document.createElement('button');apply.type='submit';apply.className='btn btn-primary';apply.textContent='Apply Row';
    const cancel=document.createElement('button');cancel.type='button';cancel.className='btn btn-ghost';cancel.textContent='Cancel';cancel.onclick=()=>dialog.close();footer.append(apply,cancel);form.append(footer);dialog.append(form);document.body.append(dialog);
    const canApply=['manual','pack'].includes(kind)?materialDialog(form,table,apply):kind==='process'?processDialog(form,table,apply):()=>form.reportValidity()&&inventoryReady();
    const inventoryReady=['inventory','packaging'].includes(kind)?inventoryDialog(form,table,apply):()=>true;
    form.onsubmit=event=>{event.preventDefault();if(!canApply())return;if(!table.dispatchEvent(new CustomEvent('bom:before-apply',{bubbles:true,cancelable:true,detail:{row,isNew,values:Object.fromEntries(pairs.map(([original,input])=>[original.name,input.value]))}})))return;for(const [original,input]of pairs)original.value=input.value;if(isNew){body.append(row);actions(row);}else paint(row);table.dispatchEvent(new Event('input',{bubbles:true}));dialog.close();};
    dialog.addEventListener('click',event=>{if(event.target===dialog){const box=dialog.getBoundingClientRect();if(event.clientX<box.left||event.clientX>box.right||event.clientY<box.top||event.clientY>box.bottom)dialog.close();}});
    dialog.addEventListener('close',()=>dialog.remove());dialog.showModal();
  }
  function actions(row) {
    const cell=document.createElement('td');cell.className='bom-row-actions';
    if(readOnly||row.hasAttribute('data-inherited')){const badge=document.createElement('span');badge.className='status-badge';badge.textContent='Inherited';cell.append(badge);row.append(cell);for(const control of row.querySelectorAll('input,select'))control.disabled=true;paint(row);return;}
    const pencil=document.createElement('button');pencil.type='button';pencil.className='btn btn-sm btn-outline-primary';pencil.textContent='✎';pencil.setAttribute('aria-label',label.replace('Add','Edit'));pencil.title=label.replace('Add','Edit');pencil.onclick=()=>edit(row);
    const remove=document.createElement('button');remove.type='button';remove.className='btn btn-sm btn-outline-danger';remove.textContent='Remove';remove.onclick=()=>{row.remove();table.dispatchEvent(new Event('input',{bubbles:true}));};
    cell.append(pencil,remove);row.append(cell);paint(row);
  }
  for(const row of body.rows)actions(row);
  if(readOnly)continue;
  const add=document.createElement('button');add.type='button';add.className='btn btn-outline-primary bom-add';add.textContent=label;
  // A new row exists ONLY in detached modal state until successful Apply.
  add.onclick=()=>edit(template.content.firstElementChild.cloneNode(true),true);
  table.closest('.table-responsive').before(add);
  if(kind==='inventory'&&scope.matches?.('[data-catalogue-costing]')){
    const bulk=document.createElement('button');bulk.type='button';bulk.className='btn btn-outline-primary bom-add';bulk.textContent='Add Multiple Materials / Components';
    bulk.onclick=()=>bulkComponentDialog({table,template,preview:inventoryPreview,applyRows:rows=>{for(const row of rows){body.append(row);actions(row);}table.dispatchEvent(new Event('input',{bubbles:true}));}});
    table.closest('.table-responsive').before(bulk);
  }
}
}
initBomRows();
for(const form of document.querySelectorAll('[data-confirm-delete]'))form.onsubmit=event=>{if(!confirm('Remove this scenario from the saved list? Historical data is retained.'))event.preventDefault();};
const planner=document.querySelector('form[data-local-costing-form]');
if(planner) {
  const advanced=planner.querySelector('[data-advanced]');
  function overrides(){advanced.querySelector('[data-override-count]').textContent=`(${[...advanced.querySelectorAll('[data-override]')].filter(input=>input.value.trim()!==''&&(input.name!=='other'||Number(input.value)!==0)).length} active overrides)`;}
  overrides();let timer,generation=0;
  async function preview() {
    const current=++generation;clearTimeout(timer);const data=new FormData(planner);data.set('action','preview');
    try {
      const response=await fetch(planner.getAttribute('action')||location.href,{method:'POST',body:data,credentials:'same-origin'});
      const html=new DOMParser().parseFromString(await response.text(),'text/html');if(current!==generation)return;
      const target=document.getElementById('live-costing');
      if(!response.ok){advanced.open=true;target.textContent=html.querySelector('[role=alert]')?.textContent||'Complete the inputs to calculate.';return;}
      target.replaceChildren(...html.querySelector('#live-costing').childNodes);
    }catch{if(current===generation)document.getElementById('live-costing').textContent='Live costing unavailable. Use Calculate Preview to retry.';}
  }
  planner.addEventListener('input',()=>{overrides();++generation;clearTimeout(timer);timer=setTimeout(preview,500);});
  planner.addEventListener('change',()=>{overrides();++generation;clearTimeout(timer);timer=setTimeout(preview,500);});
  planner.addEventListener('submit',event=>{if(event.submitter?.value==='preview'){event.preventDefault();preview();}});
  // Measure the real fixed navigation instead of changing its position/visibility.
  const nav=document.querySelector('.mobile-bottom-nav');
  if(nav){const measure=()=>{const box=nav.getBoundingClientRect();document.querySelector('.bom-workspace').style.setProperty('--bom-nav-clearance',getComputedStyle(nav).position==='fixed'?`${Math.max(0,innerHeight-box.top)}px`:'0px');};new ResizeObserver(measure).observe(nav);window.addEventListener('resize',measure);window.visualViewport?.addEventListener('resize',measure);measure();}
}
