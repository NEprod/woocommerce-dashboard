// Detached modal state only; validation and row application use existing BOM rules.
const make=(tag,text)=>{const el=document.createElement(tag);if(text!=null)el.textContent=text;return el;};
export function bulkComponentDialog({table,template,preview,applyRows}){
  const dialog=make('dialog');dialog.className='packaging-dialog bom-dialog';dialog.setAttribute('aria-label','Add multiple materials / components');
  const form=make('form'),entries=new Map();let applying=false,closed=false;
  form.append(make('h2','Add multiple materials / components'),make('p','Select registered items, enter the quantity and unit for each, then apply together. Nothing is added until Apply All.'));
  const searchLabel=make('label','Find materials / components'),search=make('input');search.type='search';search.className='form-control';searchLabel.append(search);form.append(searchLabel);
  const choices=make('div');choices.className='bom-bulk-options';form.append(choices);
  const details=make('div');form.append(details);
  const message=make('p');message.setAttribute('role','status');form.append(message);
  const footer=make('div');footer.className='page-actions';const apply=make('button','Apply All'),cancel=make('button','Cancel');
  apply.type='submit';apply.className='btn btn-primary';cancel.type='button';cancel.className='btn btn-ghost';cancel.onclick=()=>dialog.close();footer.append(apply,cancel);form.append(footer);
  const refresh=()=>{apply.disabled=applying||!entries.size||[...entries.values()].some(e=>!e.ready);};
  async function validate(entry){
    const generation=++entry.generation;entry.ready=false;refresh();
    try{
      if(entry.fields.querySelector(':invalid')){entry.output.textContent='Enter a valid quantity and compatible unit.';return;}
      const data=await preview(table,entry.fields);
      if(closed||generation!==entry.generation)return;
      entry.output.textContent=data.amount===null?'Cost unknown; estimate will be incomplete.':`Material/component cost: £${data.display_amount}. Recipe/print allowances are calculated separately.`;
      entry.ready=true;
    }catch(error){if(generation===entry.generation)entry.output.textContent=error.message;}
    finally{refresh();}
  }
  for(const option of template.content.querySelector('[name=item_id]').options){
    if(!option.value)continue;
    const label=make('label'),checkbox=make('input');checkbox.type='checkbox';checkbox.value=option.value;checkbox.dataset.bulkChoice='';label.append(checkbox,document.createTextNode(option.textContent));choices.append(label);
    checkbox.onchange=()=>{
      if(!checkbox.checked){const entry=entries.get(option.value);if(entry){clearTimeout(entry.timer);entry.generation++;entry.fields.remove();entries.delete(option.value);}refresh();return;}
      const row=template.content.firstElementChild.cloneNode(true),fields=make('fieldset');fields.className='workshop-panel';fields.setAttribute('aria-label',option.textContent);fields.append(make('legend',option.textContent));
      const grid=make('div');grid.className='workshop-fields';fields.append(grid);
      const pairs=[];
      for(const [index,cell]of [...row.cells].entries())for(const original of cell.querySelectorAll('input,select')){
        const input=original.cloneNode(true);input.hidden=false;input.value=original.value;pairs.push([original,input]);
        if(original.name==='item_id'){input.value=option.value;input.hidden=true;fields.append(input);continue;}
        const field=make('label',original.dataset.label||table.tHead.rows[0].cells[index].textContent);field.append(input);grid.append(field);
      }
      const output=make('p');output.setAttribute('role','status');fields.append(output);details.append(fields);
      const entry={row,fields,pairs,output,ready:false,generation:0,timer:null};entries.set(option.value,entry);
      const signature=()=>JSON.stringify(pairs.map(([,input])=>input.value));entry.signature=signature();
      const update=()=>{const next=signature();if(next===entry.signature)return;entry.signature=next;entry.generation++;entry.ready=false;refresh();clearTimeout(entry.timer);entry.timer=setTimeout(()=>validate(entry),200);};
      fields.addEventListener('input',update);fields.addEventListener('change',update);validate(entry);
    };
  }
  search.oninput=()=>{for(const label of choices.children)label.hidden=!label.textContent.toLowerCase().includes(search.value.trim().toLowerCase());};
  form.onsubmit=async event=>{
    event.preventDefault();if(applying||!entries.size||!form.reportValidity())return;
    applying=true;refresh();message.textContent='Validating selected items…';
    for(const control of form.querySelectorAll('input,select'))control.disabled=true;
    try{
      const selected=[...entries.values()];for(const entry of selected)clearTimeout(entry.timer);
      await Promise.all(selected.map(validate));
      if(closed||!dialog.open)return;
      if(selected.some(e=>!e.ready)){message.textContent='No rows added. Correct the highlighted quantities or units and try again.';return;}
      for(const entry of selected){
        const values=Object.fromEntries(entry.pairs.map(([original,input])=>[original.name,input.value]));
        if(!table.dispatchEvent(new CustomEvent('bom:before-apply',{bubbles:true,cancelable:true,detail:{row:entry.row,isNew:true,values}}))){message.textContent='No rows added. Review the overlapping requirements before applying.';return;}
        for(const[original,input]of entry.pairs)original.value=input.value;
      }
      applyRows(selected.map(e=>e.row));dialog.close();
    }finally{for(const control of form.querySelectorAll('input,select'))control.disabled=false;applying=false;refresh();}
  };
  dialog.addEventListener('close',()=>{closed=true;for(const e of entries.values()){clearTimeout(e.timer);e.generation++;}dialog.remove();});
  dialog.addEventListener('click',event=>{if(event.target===dialog){const b=dialog.getBoundingClientRect();if(event.clientX<b.left||event.clientX>b.right||event.clientY<b.top||event.clientY>b.bottom)dialog.close();}});
  dialog.append(form);document.body.append(dialog);refresh();dialog.showModal();
}
