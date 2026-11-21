for (const [name,label] of [['item_id','Add Material / Component'],['manual_name','Add Hypothetical Material'],['equipment_id','Add Production Process']]) {
  const first=document.querySelector(`[name="${name}"]`); if(!first) continue;
  const table=first.closest('table'), body=table.tBodies[0];
  const blank=body.lastElementChild.cloneNode(true);
  const heading=document.createElement('th'); heading.textContent='Action'; table.tHead.rows[0].append(heading);
  function paint(row) {
    for(const cell of row.cells) {
      cell.querySelector('[data-row-summary]')?.remove();
      const input=cell.querySelector('input,select');if(!input)continue;
      input.hidden=true;const summary=document.createElement('span');summary.dataset.rowSummary='';summary.className='text-break';summary.textContent=input.tagName==='SELECT'?input.selectedOptions[0]?.textContent:input.value||'—';cell.append(summary);
    }
  }
  function edit(row) {
    const dialog=document.createElement('dialog');dialog.className='packaging-dialog';dialog.setAttribute('aria-label',label.replace('Add','Edit'));
    const form=document.createElement('form'),title=document.createElement('h2');title.textContent=label.replace('Add','Edit');form.append(title);
    const grid=document.createElement('div');grid.className='workshop-fields';const pairs=[];
    for(const [index,cell] of Array.from(row.cells).entries())for(const original of cell.querySelectorAll('input,select')){
      const field=document.createElement('label');field.textContent=table.tHead.rows[0].cells[index].textContent;
      const input=original.cloneNode(true);input.hidden=false;input.value=original.value;field.append(input);grid.append(field);pairs.push([original,input]);
    }
    form.append(grid);const footer=document.createElement('div');footer.className='page-actions';
    const apply=document.createElement('button');apply.type='submit';apply.className='btn btn-primary';apply.textContent='Apply Row';
    const cancel=document.createElement('button');cancel.type='button';cancel.className='btn btn-ghost';cancel.textContent='Cancel';cancel.onclick=()=>dialog.close();footer.append(apply,cancel);form.append(footer);dialog.append(form);document.body.append(dialog);
    form.onsubmit=event=>{event.preventDefault();for(const [original,input]of pairs)original.value=input.value;paint(row);table.dispatchEvent(new Event('input',{bubbles:true}));dialog.close();};
    dialog.addEventListener('close',()=>dialog.remove());dialog.showModal();
  }
  function actions(row) { const cell=document.createElement('td'), button=document.createElement('button'),pencil=document.createElement('button');pencil.type='button';pencil.className='btn btn-sm btn-outline-primary';pencil.textContent='✎';pencil.setAttribute('aria-label',label.replace('Add','Edit'));pencil.title=label.replace('Add','Edit');pencil.onclick=()=>edit(row);button.type='button'; button.className='btn btn-sm btn-outline-danger'; button.textContent='Remove'; button.setAttribute('aria-label','Remove row'); button.addEventListener('click',()=>{row.remove();table.dispatchEvent(new Event('input',{bubbles:true}));});cell.append(pencil,button);row.append(cell);paint(row); }
  for(const row of body.rows) actions(row);
  const add=document.createElement('button');add.type='button';add.className='btn btn-outline-primary';add.textContent=label;
  add.addEventListener('click',()=>{const row=blank.cloneNode(true);for(const input of row.querySelectorAll('input')) input.value=input.name==='usable_units'?'1':input.name==='share_percent'?'100':'';for(const select of row.querySelectorAll('select'))select.selectedIndex=0;body.append(row);actions(row);edit(row);});
  table.closest('.table-responsive').before(add);
}
for(const form of document.querySelectorAll('[data-confirm-delete]')) form.addEventListener('submit',event=>{if(!confirm('Remove this scenario from the saved list? Its historical data will be retained.'))event.preventDefault();});
const planner = document.querySelector('form[data-local-costing-form]');
if (planner) {
  let timer, generation=0;
  planner.addEventListener('input', () => {
    const current=++generation; clearTimeout(timer);
    timer=setTimeout(async()=>{
      const data=new FormData(planner);data.set('action','preview');
      try {
        const response=await fetch(planner.getAttribute('action') || location.href,{method:'POST',body:data,credentials:'same-origin'});
        const html=new DOMParser().parseFromString(await response.text(),'text/html');
        if(current!==generation)return;
        const target=document.getElementById('live-costing');
        if(!response.ok){target.replaceChildren();const message=document.createElement('p');message.className='alert alert-warning';message.textContent=html.querySelector('[role=alert]')?.textContent||'Complete the scenario inputs to calculate.';target.append(message);return;}
        target.replaceChildren(...html.querySelector('#live-costing').childNodes);
      } catch {if(current===generation)document.getElementById('live-costing').textContent='Live costing unavailable. Use Calculate Preview to retry.';}
    },500);
  });
}
