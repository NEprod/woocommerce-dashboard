// Presentation and read-only previews only. No auto-save or stock requests.
import {batchMaterial} from './recipe-quantity.mjs';
const form=document.querySelector('[data-inventory-item-form]');
if(form) {
  const recipeLines=form.querySelectorAll('[data-recipe-line]');
  function recipeTotals(){
    for(const line of recipeLines){
      const quantity=line.querySelector('[name="material_qty"]').value.trim();
      const unit=line.querySelector('[name="material_unit"]').value;
      const original=JSON.parse(line.dataset.recipeOriginal);
      const total=batchMaterial(quantity, form.elements.namedItem('yield_qty').value.trim(), unit,
        original.material===line.querySelector('[name="material_id"]').value?original:{});
      line.querySelector('[data-recipe-total]').textContent=total===null?
        'Enter a positive per-component quantity, whole batch yield and recipe unit.':`Total expected batch material: ${total} ${unit}`;
    }
  }
  if(recipeLines.length){form.addEventListener('input',recipeTotals);form.addEventListener('change',recipeTotals);recipeTotals();}
  const value=name=>form.elements.namedItem(name)?.value||'';
  const photo=form.querySelector('[data-image-upload]'), preview=form.querySelector('[data-image-preview]'), status=form.querySelector('[data-image-status]');
  const original=preview.getAttribute('src');let objectUrl;
  function release(){if(objectUrl)URL.revokeObjectURL(objectUrl);objectUrl=undefined;}
  function restore(){if(original){preview.src=original;preview.hidden=false;}else{preview.removeAttribute('src');preview.hidden=true;}}
  photo.addEventListener('change',()=>{
    release();photo.setCustomValidity('');status.textContent='';const file=photo.files[0];if(!file){restore();return;}
    if(file.size>25*1024*1024){status.textContent='Original image exceeds 25 MB. Choose a smaller photograph.';photo.setCustomValidity(status.textContent);restore();return;}
    const allowed=['image/jpeg','image/png','image/webp',...(photo.accept.includes('image/heic')?['image/heic','image/heif']:[])];
    if(file.type&&!allowed.includes(file.type)){status.textContent='Unsupported image format. Use one of the types listed above; the server also validates actual image contents.';photo.setCustomValidity(status.textContent);restore();return;}
    objectUrl=URL.createObjectURL(file);preview.src=objectUrl;preview.hidden=false;
    status.textContent='Selected photograph preview. The server will optimise and validate the original when you save.';
  });
  preview.addEventListener('error',()=>{if(objectUrl&&preview.src===objectUrl){status.textContent='This browser cannot preview the selected photograph. The server will validate it when you save; unsupported or corrupt images will not replace the saved image.';preview.hidden=true;}});
  window.addEventListener('pagehide',release);
  const display=form.querySelector('[data-material-display]');
  if(display) {
    function showDetails(){
      let mode=display.value;
      if(mode==='auto') {
        const filament=['filament_type','filament_colour','spool_grams'].some(name=>value(name).trim());
        const sheet=['sheet_length','sheet_width','sheet_thickness'].some(name=>value(name).trim());
        const category=value('category').trim().toLowerCase();
        mode=filament&&sheet?'both':['filament','3d printing filament','pla','petg','abs','asa','tpu'].includes(category)?'filament':
          ['sheet','sheet stock','plywood','acrylic','wood','card stock'].includes(category)?'sheet':
          ['adhesive','adhesives','hardware','packaging','other'].includes(category)?'other':
          filament?'filament':sheet||(value('unit')||form.dataset.stockUnit)==='sheet'?'sheet':'other';
      }
      for(const group of form.querySelectorAll('[data-material-fields]'))group.hidden=mode!=='both'&&group.dataset.materialFields!==mode;
    }
    display.addEventListener('change',showDetails);form.elements.namedItem('category').addEventListener('input',showDetails);form.elements.namedItem('unit')?.addEventListener('change',showDetails);showDetails();
  }
  const cost=form.querySelector('[data-unit-cost]');
  if(cost) {
    let timer,generation=0;
    async function calculate(){
      const current=++generation;cost.textContent='Calculating purchase unit cost…';
      const body={pack_qty:value('pack_qty'),pack_unit:value('pack_unit'),pack_price:value('pack_price'),unit:value('unit')||form.dataset.stockUnit};
      try {
        const response=await fetch(form.dataset.costPreview,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRFToken':value('csrf_token')},body:JSON.stringify(body)});
        const data=await response.json();if(current!==generation)return;
        cost.textContent=!response.ok?data.error:data.unit_cost===null?'Purchase cost is unknown — blank price is not £0.':`Purchase cost per stock unit: £${data.display_cost} per ${data.unit}.`;
        cost.title=data.unit_cost===null?'':`Unrounded unit cost: £${data.unit_cost} per ${data.unit}`;
      }catch{if(current===generation)cost.textContent='Unit-cost preview is unavailable. Saving still validates purchase quantities and compatible units.';}
    }
    for(const name of ['pack_qty','pack_unit','pack_price','unit'])form.elements.namedItem(name)?.addEventListener('input',()=>{++generation;clearTimeout(timer);timer=setTimeout(calculate,200);});
    calculate();
  }
}
