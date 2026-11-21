// UI-only hours/minutes and batch composition; saved rows retain the original fields.
export function processDialog(form, table, apply) {
  const grid=form.querySelector('.workshop-fields'), get=name=>form.elements.namedItem(name);
  const total=get('minutes'), mode=get('timing_mode'), allocation=get('allocation');
  // Split ordinary saved decimal minutes without rounding their fractional part.
  const saved=String(total.value||'0'), parts=saved.match(/^(\d+)(\.\d+)?$/);
  const initialHours=parts?String(BigInt(parts[1])/60n):String(Math.floor(Number(saved)/60));
  const initialMinutes=parts?String(BigInt(parts[1])%60n)+(parts[2]||''):String(Number(saved)%60);
  total.closest('label').hidden=true;
  const duration=document.createElement('fieldset');duration.className='border-0 p-0';
  const legend=document.createElement('legend');legend.className='fs-6';legend.textContent='Total machine runtime';duration.append(legend);
  const row=document.createElement('div');row.className='d-flex gap-2';
  for(const [name,label,value,max] of [
    ['runtime_hours','Hours',initialHours,16666666],
    ['runtime_minutes','Minutes',initialMinutes,59.99999999999999]
  ]) {
    const field=document.createElement('label');field.className='w-50';field.textContent=label;
    const input=document.createElement('input');input.className='form-control';input.type='number';input.name=name;input.min='0';input.max=String(max);input.step=name==='runtime_hours'?'1':'any';input.value=String(value);input.required=true;
    input.title=name==='runtime_hours'?'Whole hours of machine runtime.':'Remaining minutes, 0–59; fractional minutes are retained for existing records.';
    field.append(input);row.append(field);
  }
  duration.append(row);grid.insertBefore(duration,mode.closest('label'));
  const contents=document.createElement('label');contents.textContent='Batch contents';
  const kind=document.createElement('select');kind.name='batch_contents';kind.className='form-select';
  for(const [value,label] of [['','Choose batch contents'],['identical','Identical finished items'],['mixed','Mixed finished items']])kind.add(new Option(label,value));
  kind.value=allocation.value==='identical'||mode.value==='item'?'identical':['share','explicit_equal'].includes(allocation.value)?'mixed':'';
  contents.append(kind);grid.insertBefore(contents,allocation.closest('label'));
  // Identical is selected through Batch contents, not the mixed-item allocation selector.
  allocation.querySelector('option[value="identical"]').hidden=true;
  const output=document.createElement('p');output.className='alert alert-info text-break';output.setAttribute('role','status');output.setAttribute('aria-live','polite');form.insertBefore(output,grid);
  const help=document.createElement('p');help.className='text-muted';help.textContent='Live electricity estimate uses the current equipment profile and tariff. Saved snapshots stay unchanged until you explicitly save. Unattended machine time is not hands-on labour.';form.insertBefore(help,grid);
  let generation=0,ready=false,timer,lastSignature;
  function update() {
    const batch=mode.value==='batch', mixed=batch&&kind.value==='mixed';
    contents.hidden=!batch;
    get('usable_units').closest('label').hidden=!batch;
    allocation.closest('label').hidden=!mixed;
    get('share_percent').closest('label').hidden=!(mixed&&allocation.value==='share');
    // Only change allocation in response to an explicit batch configuration.
    if(batch&&kind.value==='identical')allocation.value='identical';
    if(mixed&&allocation.value==='identical')allocation.value='';
    const payload=Object.fromEntries([...form.querySelectorAll('input,select')].map(input=>[input.name,input.value]));
    const signature=JSON.stringify(payload);if(signature===lastSignature)return;lastSignature=signature;
    ready=false;apply.disabled=true;output.textContent='Calculating…';clearTimeout(timer);const current=++generation;
    timer=setTimeout(async()=>{
      try {
        if(batch&&!kind.value)throw new Error('Choose whether the batch contains identical or mixed finished items.');
        const response=await fetch(table.dataset.processPreview,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRFToken':document.querySelector('meta[name="csrf-token"]')?.content||''},body:JSON.stringify(payload)});
        const data=await response.json();if(!response.ok)throw new Error(data.error||'Check production process inputs.');
        if(current!==generation)return;
        total.value=data.minutes;
        // The hidden storage value does not constitute a new user edit.
        lastSignature=JSON.stringify({...payload,minutes:total.value});
        const display=value=>new Intl.NumberFormat(undefined,{maximumFractionDigits:6}).format(Number(value));
        output.textContent=`Total machine runtime: ${display(data.total_minutes)} minutes. Per finished product: ${display(data.attributable_minutes)} minutes. Electricity per product: ${data.electricity===null?'unknown':'£'+data.electricity}. ${[...data.missing,...data.provisional].join(' ')}`;
        ready=true;apply.disabled=false;
      } catch(error) {if(current===generation){output.textContent=error.message;ready=false;apply.disabled=true;}}
    },200);
  }
  get('usable_units').title='Accepted usable finished items, not the planned quantity. For a custom share, enter usable items sharing that percentage.';
  allocation.title='Mixed items require an explicit equal split or a custom share of total machine time.';
  get('share_percent').title='Percentage of total batch runtime attributable to this group, then divided by its usable finished items.';
  form.addEventListener('input',update);form.addEventListener('change',update);update();
  return ()=>ready&&form.reportValidity();
}
