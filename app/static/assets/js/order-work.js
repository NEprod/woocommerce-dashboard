// Preserve fixed navigation; measure its actual safe-area footprint, not a device height.
const scopes=[...document.querySelectorAll('.order-fulfilment')];
const nav=document.querySelector('.mobile-bottom-nav');
for(const select of document.querySelectorAll('[data-manual-stage]')){
 const update=()=>{for(const fields of select.form.querySelectorAll('[data-stage-fields]')){
  const shown=fields.dataset.stageFields.split(' ').includes(select.value);fields.hidden=!shown;
  for(const input of fields.querySelectorAll('input'))input.disabled=!shown;
 }};
 select.addEventListener('change',update);update();
}
document.querySelector('[data-copy-order-email]')?.addEventListener('click',async()=>{
 const status=document.querySelector('[data-copy-email-status]');
 try{await navigator.clipboard.writeText(document.getElementById('order-customer-email').textContent);status.textContent=' Email copied.';}
 catch{status.textContent=' Copy unavailable. Select and copy the email shown above.';}
});
if(scopes.length&&nav){
 const measure=()=>{
  const pixels=getComputedStyle(nav).position==='fixed'?Math.max(0,innerHeight-nav.getBoundingClientRect().top):0;
  for(const scope of scopes)scope.style.setProperty('--order-nav-clearance',`${pixels}px`);
 };
 measure();new ResizeObserver(measure).observe(nav);window.addEventListener('resize',measure);
 window.visualViewport?.addEventListener('resize',measure);
}
