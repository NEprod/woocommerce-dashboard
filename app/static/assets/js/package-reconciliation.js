(function(){
  'use strict';
  const form=document.querySelector('[data-package-reconcile]');if(!form)return;
  const api=window.TLCPackaging.api,feedback=document.querySelector('[data-package-reconcile-feedback]');
  const matches=document.querySelector('[data-package-matches]'),review=document.querySelector('[data-package-review]');
  const observations=form.querySelector('[data-package-observations]');let records=[],choices=[],token=null;
  const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;};
  const invalidate=()=>{token=null;review.hidden=true;document.querySelector('[data-package-approval]').checked=false;};
  const add=()=>{if(observations.children.length>=50){feedback.textContent='Compare at most 50 packages.';return;}
    matches.hidden=true;invalidate();
    const fieldset=document.getElementById('package-observation-template').content.firstElementChild.cloneNode(true);
    fieldset.querySelector('[data-remove-observation]').addEventListener('click',()=>{fieldset.remove();matches.hidden=true;invalidate();});observations.append(fieldset);
  };
  form.querySelector('[data-add-observation]').addEventListener('click',add);
  form.addEventListener('input',()=>{matches.hidden=true;invalidate();});add();
  function showFields(target,fields,direction){
    const table=node('table',undefined,'table shipping-packages-table'),head=node('thead'),hr=node('tr');
    const proposed=fields.some(f=>Object.hasOwn(f,'proposed'));
    ['Field','Current TLC','Observed Shopify',...(proposed?['Proposed local / manual value']:[])].forEach(s=>hr.append(node('th',s)));head.append(hr);table.append(head);const body=node('tbody');
    fields.forEach(field=>{const row=node('tr');row.append(node('th',field.field));
      for(const [key,label] of [['local','TLC'],['remote','Observed']]){const cell=node('td',field[key]??'Unknown');cell.dataset.label=label;row.append(cell);}
      if(proposed){const cell=node('td',field.proposed??'Unknown');cell.dataset.label='Proposed';row.append(cell);}
      if(field.changed)row.firstChild.append(node('small',direction==='export'?' Manual Shopify action':' Difference — review'));
      body.append(row);});table.append(body);target.append(table);
  }
  form.addEventListener('submit',async event=>{event.preventDefault();invalidate();feedback.textContent='Comparing local records with your observations…';
    const read=(row,name)=>row.querySelector(`[name="${name}"]`).value;
    records=[...observations.children].map(row=>({name:read(row,'name'),type:read(row,'type'),id:read(row,'remote_id')||null,
      dimensions:Object.fromEntries(['length','width','height','unit'].map(k=>[k,read(row,k)||null])),
      weight:{value:read(row,'weight')||null,unit:read(row,'weight_unit')}}));
    try{const result=await api('/packaging/reconcile/compare',{records});choices=[];const target=document.querySelector('[data-package-match-rows]');target.replaceChildren();
      result.records.forEach(record=>{const article=node('article',undefined,'package-observation');article.append(node('h3',record.remote.name));
        article.append(node('p',record.ambiguous?'Ambiguous name candidates — explicitly choose the correct existing TLC record.':'Check type, dimensions and identity; nothing is linked automatically.'));
        const label=node('label','Existing TLC package'),select=node('select',undefined,'form-select');select.append(new Option('Choose a TLC record',''));
        result.packages.forEach(p=>select.append(new Option(p.name,p.id)));label.append(select);article.append(label);
        const direction=node('select',undefined,'form-select');for(const [v,t] of [['skip','Skip'],['import','Shopify → TLC — import observed values'],['export','TLC → Shopify — manual checklist only'],['link','Keep Local — record checked relationship only']])direction.append(new Option(t,v));
        const dl=node('label','Direction');dl.append(direction);article.append(dl);
        const checked=node('input');checked.type='checkbox';checked.disabled=!record.remote.id;const cl=node('label',' Record this actual Shopify GID as a manually checked relationship (not API verified)');cl.prepend(checked);article.append(cl);
        const preview=node('div');article.append(preview);const unique=record.candidates.length===1;
        const complete=['length','width','height'].every(k=>record.remote.external_cm[k]!==null)&&record.remote.tare_g!==null;
        const choice={package_id:'',direction:'skip',retain_link:false,unambiguous:false,select,directionControl:direction};choices.push(choice);
        function changed(){choice.package_id=select.value;choice.direction=direction.value;choice.retain_link=checked.checked;invalidate();preview.replaceChildren();
          const candidate=record.candidates.find(c=>c.id===select.value);choice.unambiguous=unique&&complete&&!!candidate&&(candidate.identity_match||candidate.compatible);if(candidate){preview.append(node('p',candidate.reason));showFields(preview,candidate.fields,direction.value);}}
        select.addEventListener('change',changed);direction.addEventListener('change',changed);checked.addEventListener('change',changed);
        if(unique)select.value=record.candidates[0].id;changed();target.append(article);
      });matches.hidden=false;feedback.textContent=result.notice;
    }catch(error){feedback.textContent=error.message;}
  });
  document.querySelector('[data-all-directions]').addEventListener('change',event=>{invalidate();choices.filter(c=>c.unambiguous).forEach(c=>{c.direction=event.target.value;c.directionControl.value=c.direction;});});
  document.querySelector('[data-package-build-review]').addEventListener('click',async()=>{invalidate();
    try{const result=await api('/packaging/reconcile/preview',{records,choices:choices.map(c=>({package_id:c.package_id,direction:c.direction,retain_link:c.retain_link}))});token=result.token;
      const target=document.querySelector('[data-package-review-rows]');target.replaceChildren();result.plan.rows.forEach(row=>{const article=node('article',undefined,'package-observation');article.append(node('h3',row.before.name),node('p',row.result));showFields(article,row.fields,row.direction);
        if(row.direction==='import')article.append(node('p','Unknown observed values do not clear known measurements. Changed imported dimensions/weight require renewed physical verification.'));
        if(row.retain_link)article.append(node('p',`Record manual relationship: ${row.remote.id}. This is not API verification.`));
        if(row.direction==='export'){const pre=node('pre',JSON.stringify({name:row.before.name,type:row.before.type,external_cm:row.before.external_cm,tare_g:row.before.tare_g},null,2),'text-wrap text-break');article.append(pre,node('p','Use Shopify Admin; no remote update is sent by confirmation. Do not change the store default.'));}target.append(article);});review.hidden=false;feedback.textContent='Preview saved for 15 minutes. Confirm explicitly; no Shopify mutation is available.';
    }catch(error){feedback.textContent=error.message;}
  });
  document.querySelector('[data-package-confirm]').addEventListener('click',async event=>{if(!token||!document.querySelector('[data-package-approval]').checked){feedback.textContent='Check the reviewed confirmation before applying.';return;}
    event.currentTarget.disabled=true;try{const result=await api('/packaging/reconcile/confirm',{token,approve:true});feedback.textContent=`${result.plan.rows.length} reviewed package results saved locally. Shopify writes: ${result.plan.remote_writes}. Manual Shopify actions remain separate.`;}
    catch(error){feedback.textContent=error.message;}finally{event.currentTarget.disabled=false;}
  });
})();
