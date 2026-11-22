// Preserve fixed navigation; measure its actual safe-area footprint, not a device height.
const scopes=[...document.querySelectorAll('.order-fulfilment')];
const nav=document.querySelector('.mobile-bottom-nav');
if(scopes.length&&nav){
 const measure=()=>{
  const pixels=getComputedStyle(nav).position==='fixed'?Math.max(0,innerHeight-nav.getBoundingClientRect().top):0;
  for(const scope of scopes)scope.style.setProperty('--order-nav-clearance',`${pixels}px`);
 };
 measure();new ResizeObserver(measure).observe(nav);window.addEventListener('resize',measure);
 window.visualViewport?.addEventListener('resize',measure);
}
