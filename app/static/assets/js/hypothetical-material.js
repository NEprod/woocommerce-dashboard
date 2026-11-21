function fields(controls) {
  const result={};
  for(const input of controls){const key=input.name.replace(/^(manual|pack)_/,'');result[key==='cost'?'unit_cost':key]=input.value;}
  return result;
}
async function calculate(url,body){
  const response=await fetch(url,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRFToken':document.querySelector('meta[name="csrf-token"]')?.content||''},body:JSON.stringify(body)});
  const data=await response.json();if(!response.ok)throw new Error(data.error||'Material calculation failed.');return data;
}
export function paintMaterial(row,table){
  const target=row.querySelector('[data-manual-total]');if(!target)return;
  const body=fields(row.querySelectorAll('input,select'));
  if(!body.name?.trim()&&!body.item_id){target.textContent='—';return;}
  const marker=JSON.stringify(body);target.dataset.calculation=marker;
  calculate(table.dataset.hypotheticalPreview,body).then(data=>{if(target.dataset.calculation===marker)target.textContent=data.display_amount===null?'Unknown':'£'+data.display_amount;}).catch(error=>{if(target.dataset.calculation===marker)target.textContent=error.message;});
}
export function materialDialog(form,table,apply){
  const grid=form.querySelector('.workshop-fields');
  const prefix=table.dataset.bomRows==='pack'?'pack':'manual';
  for(const suffix of ['item_id','name','cost_mode','purchase_cost','purchase_quantity','purchase_unit','quantity','unit','conversion_factor','cost','print_material']){
    const name=prefix+'_'+suffix;
    const input=form.elements.namedItem(name);if(input)grid.append(input.closest('label'));
  }
  const output=document.createElement('p');output.className='alert alert-info text-break';output.setAttribute('role','status');output.setAttribute('aria-live','polite');form.insertBefore(output,grid);
  let generation=0,ready=false,timer,lastFields;
  function update(){
    const mode=form.elements.namedItem(prefix+'_cost_mode').value;
    const inventory=Boolean(form.elements.namedItem(prefix+'_item_id')?.value);
    for(const input of form.querySelectorAll('input,select')){
      const suffix=input.name.replace(/^(manual|pack)_/,'');
      const purchase=['purchase_cost','purchase_quantity','purchase_unit','conversion_factor'].includes(suffix);
      if(purchase||suffix==='cost'||suffix==='cost_mode'||suffix==='name')input.closest('label').hidden=inventory||(purchase?mode!=='purchase':suffix==='cost'?mode!=='direct':false);
    }
    const signature=JSON.stringify(fields(form.querySelectorAll('input,select')));
    if(signature===lastFields)return;lastFields=signature;
    ready=false;apply.disabled=true;output.textContent='Calculating…';const current=++generation;clearTimeout(timer);
    timer=setTimeout(async()=>{try{
      const body=fields(form.querySelectorAll('input,select'));const data=await calculate(table.dataset.hypotheticalPreview,body);
      if(current!==generation)return;
      output.textContent=data.amount===null?'Cost unknown. Applying this row keeps the estimate incomplete.':data.inventory?`Current saved inventory cost for quantity used: £${data.display_amount}.`:`Normalised unit cost: £${data.normalized_unit_cost} per ${data.normalized_unit}. Material cost for quantity used: £${data.display_amount}.`;
      ready=Boolean(body.name?.trim()||body.item_id);apply.disabled=!ready;if(!ready)output.textContent+=' Enter a material name before applying.';
    }catch(error){if(current===generation){output.textContent=error.message;ready=false;apply.disabled=true;}}},200);
  }
  form.addEventListener('input',update);form.addEventListener('change',update);update();
  return ()=>ready;
}
