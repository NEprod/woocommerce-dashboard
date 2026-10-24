/* Run in CUA with a bound local tab after starting customisation_editor_fixture.py.
 * Uses real DOM controls and the existing CSRF/signed save + scanner. No page-side
 * application JS execution, Shopify writes, browser uploads or real source data.
 * Native Safari/WebKit and Chrome checkbox/focus checks additionally use CUA AX.
 */
async function customisationEditorUX(tab) {
  if(!(await tab.url()).startsWith('http://127.0.0.1:7495/'))throw new Error('Refusing a non-fixture browser target');
  const p=tab.playwright,done=[];
  const check=(condition,label)=>{if(!condition)throw new Error(label);done.push(label);};
  const modal=p.locator('[data-custom-field-dialog]');
  const value=key=>p.locator(`[data-custom-control="${key}"]`).evaluate(node=>node.value);
  const edit=async id=>{await p.locator(`[data-custom-edit="${id}"]`).click();};
  const cancel=()=>modal.getByRole('button',{name:'Cancel',exact:true}).click();
  const apply=()=>modal.getByRole('button',{name:'Apply to local draft',exact:true}).click();
  await edit('name_on_card');
  check(await value('id')==='name_on_card','existing ID loaded');
  check(await value('label')==='Name','existing label loaded');
  check(await value('type')==='text','existing type loaded');
  check((await p.locator('[data-custom-control="id"]').getAttribute('readonly'))!==null,'existing ID read-only');
  for(const key of ['required','enabled'])check(await p.locator(`[data-custom-control="${key}"]`).evaluate(node=>node.checked),`${key} loads true`);
  await modal.getByLabel('Required',{exact:true}).uncheck();
  check(await p.locator('[data-custom-control="enabled"]').evaluate(n=>n.checked),'required toggles independently');
  await modal.getByLabel('Enabled',{exact:true}).uncheck();
  check(!(await p.locator('[data-custom-control="enabled"]').evaluate(n=>n.checked)),'enabled toggles independently');
  await modal.getByLabel('Customer-facing label',{exact:true}).fill('Cancelled label');
  await cancel();await edit('name_on_card');
  check(await value('label')==='Name','Cancel discards modal edits');
  check(await p.locator('[data-custom-control="required"]').evaluate(n=>n.checked),'Cancel preserves Required');
  await modal.getByLabel('Customer-facing label',{exact:true}).fill('Name to appear on your card');await apply();await edit('name_on_card');
  check(await value('id')==='name_on_card','label edit preserves identity');
  check(await value('max_length')==='30','label edit preserves validation');await cancel();
  await edit('style');
  const options=modal.locator('.customisation-option-row');
  await options.nth(0).getByLabel('Option label',{exact:true}).fill('Decorative script');
  await options.nth(0).getByRole('button',{name:'Move option down',exact:true}).click();
  check(await modal.getByLabel('Default selection',{exact:true}).evaluate(n=>n.value)==='script','option reorder preserves default ID');
  await apply();await edit('style');
  check(await options.nth(1).getByLabel('Option ID',{exact:true}).evaluate(n=>n.value)==='script','option ID survives label and order changes');
  await modal.getByLabel('Input type',{exact:true}).selectOption('radio');
  check(await modal.getByRole('button',{name:'Add option',exact:true}).isVisible(),'type changes show option editor');await cancel();
  await edit('extras');await modal.getByLabel('Heart',{exact:true}).check();await apply();await edit('extras');
  check(await modal.getByLabel('Heart',{exact:true}).evaluate(n=>n.checked),'checkbox-group default uses real checkboxes');await cancel();
  for(const [id,label,expected]of [['age','Default number','4'],['delivery_date','Default date','2026-10-16'],['colour','Default colour','#ff0000']]){
    await edit(id);check(await modal.getByLabel(label,{exact:true}).evaluate(n=>n.value)===expected,`${id} typed default loaded`);await cancel();
  }
  await edit('gift_message');await modal.getByLabel('Default checked',{exact:true}).check();await apply();await edit('gift_message');
  check(await modal.getByLabel('Default checked',{exact:true}).evaluate(n=>n.checked),'checkbox default is a boolean control');await cancel();
  await edit('photo');check(await value('max_file_size_bytes')==='5000000','file size loaded');
  check(await modal.getByLabel('Allow images',{exact:true}).evaluate(n=>n.checked),'file category checkbox loaded');
  check(await modal.locator('input[type="file"]').count()===0,'no actual customer upload input');await cancel();
  await p.getByRole('button',{name:'Preview Customer Form',exact:true}).click();
  const preview=p.locator('[data-custom-preview-dialog]');
  await preview.getByLabel('Name to appear on your card',{exact:true}).fill('');
  await preview.getByRole('button',{name:'Test local validation',exact:true}).click();
  check((await preview.innerText()).includes('This field is required.'),'required preview validation');
  await preview.getByLabel('Name to appear on your card',{exact:true}).fill('Alex');
  await preview.getByRole('button',{name:'Test local validation',exact:true}).click();
  check(!(await preview.innerText()).includes('This field is required.'),'valid required answer accepted');
  await preview.getByRole('button',{name:'Close preview',exact:true}).click();
  await p.locator('[data-acknowledge-shared]').check();await p.locator('[data-save-metadata]').first().click();
  await p.getByText('Local scan completed',{exact:true}).waitFor({state:'visible',timeoutMs:20000});
  if(await p.locator('#scanModal').isVisible())await p.locator('#scanModal').getByRole('button',{name:'Done',exact:true}).click();
  await tab.reload();await edit('name_on_card');
  check(await value('label')==='Name to appear on your card','signed local save persists and reloads');await cancel();
  await tab.goto('http://127.0.0.1:7495/edit_products/1/edit/override');
  check(await p.locator('[data-custom-edit="name_on_card"]').count()===0,'inherited field has no edit control');
  await p.locator('[data-custom-add]').click();
  await modal.getByLabel('Customer-facing label',{exact:true}).fill('Local message');
  await modal.getByLabel('Maximum length',{exact:true}).fill('200');await apply();
  await p.getByRole('button',{name:'Advanced JSON',exact:true}).click();
  const draft=JSON.parse(await p.locator('[data-json-editor]').evaluate(n=>n.value));
  check(!Object.hasOwn(draft.customisation.fields,'name_on_card')&&draft.customisation.fields.local_message.max_length===200,'only distinct local field authored');
  return done;
}
