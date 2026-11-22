// Remote actions are separate forms: local edit/cancel never registers anything.
const dialog=document.getElementById('webhook-modal');
if(dialog){
  const edit=dialog.querySelector('form'),remote=dialog.querySelector('[data-webhook-remote]');
  for(const button of document.querySelectorAll('[data-costing-dialog="webhook-modal"]'))button.addEventListener('click',()=>{
    const record=JSON.parse(button.dataset.record||'{}');remote.hidden=!record.id;
    dialog.querySelector('#webhook-modal-title').textContent=record.id?'Edit Webhook':'Add Webhook';
    for(const fact of dialog.querySelectorAll('[data-webhook-fact]'))fact.textContent=record[fact.dataset.webhookFact]||'Not observed';
    for(const form of remote.querySelectorAll('form')){
      form.reset();form.action=`/settings/shopify-webhooks/${record.id}/${form.dataset.webhookOperation}`;
      form.elements.version.value=record.version||'';
      form.querySelector('button').disabled=false;
    }
  });
  edit.addEventListener('input',()=>{for(const button of remote.querySelectorAll('button'))button.disabled=true;});
  for(const form of remote.querySelectorAll('form'))form.addEventListener('submit',()=>{
    for(const button of remote.querySelectorAll('button'))button.disabled=true;
  });
}
