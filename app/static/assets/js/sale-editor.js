(function (global) {
  "use strict";
  const partsFormat = new Intl.DateTimeFormat("en-GB", {timeZone:"Europe/London", year:"numeric", month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit", hourCycle:"h23"});
  function parts(date) { return Object.fromEntries(partsFormat.formatToParts(date).filter(p => p.type !== "literal").map(p => [p.type,p.value])); }
  function display(date) { const p=parts(date); return `${p.day}/${p.month}/${p.year} ${p.hour}:${p.minute}`; }
  function zone(date) {
    const p=parts(date), minute=60000;
    const offset=(Date.UTC(+p.year,+p.month-1,+p.day,+p.hour,+p.minute)-Math.floor(date.getTime()/minute)*minute)/minute;
    return offset===60 ? "BST" : "GMT";
  }
  function parse(value, selection="") {
    if (!value.trim()) return "";
    const m=/^(\d{2})\/(\d{2})\/(\d{4}) (\d{2}):(\d{2})$/.exec(value.trim());
    if (!m) throw new Error("Use DD/MM/YYYY HH:mm, for example 25/10/2026 01:30.");
    const [,day,month,year,hour,minute]=m;
    const wall=Date.UTC(+year,+month-1,+day,+hour,+minute), valid=new Date(wall);
    if (+year<100 || valid.getUTCFullYear()!==+year || valid.getUTCMonth()!==+month-1 || valid.getUTCDate()!==+day || +hour>23 || +minute>59) throw new Error("Enter a valid UK calendar date and time.");
    const candidates=[0,60].map(offset => new Date(wall-offset*60000)).filter(date => display(date)===value.trim());
    const selected=selection ? candidates.filter(date => zone(date)===selection) : candidates;
    if (!selected.length) throw new Error("This UK time does not exist, or the selected GMT/BST clock is incorrect. Choose another time.");
    if (selected.length!==1) throw new Error("This UK hour occurs twice. Select GMT or BST explicitly.");
    return selected[0].toISOString();
  }
  function stored(value, end=false) {
    if (!value) return {text:"",zone:"",note:""};
    let date, note="";
    if (/^\d{4}-\d{2}-\d{2}$/.test(value)) {
      const [y,m,d]=value.split("-").map(Number), wall=new Date(Date.UTC(y,m-1,d+(end ? 1 : 0)));
      const text=`${String(wall.getUTCDate()).padStart(2,"0")}/${String(wall.getUTCMonth()+1).padStart(2,"0")}/${wall.getUTCFullYear()} 00:00`;
      date=new Date(parse(text));
      note=end ? "Original date-only inclusive end is retained unchanged; the following midnight shown is its exclusive boundary." : "Original date-only midnight start is retained unchanged.";
    } else if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?$/.test(value)) {
      const [day,time]=value.split("T"), [y,m,d]=day.split("-");
      date=new Date(parse(`${d}/${m}/${y} ${time.slice(0,5)}`));
    } else date=new Date(value);
    if (!Number.isFinite(date.getTime())) throw new Error("Stored schedule is invalid; correct it in Advanced JSON before reviewing.");
    return {text:display(date),zone:zone(date),note:note || "Original stored precision/offset is retained unless this control is edited."};
  }
  function refreshDates(root) {
    root.querySelectorAll("[data-sale-date-field]").forEach(wrapper => {
      const hidden=wrapper.querySelector("[data-field]"), input=wrapper.querySelector("[data-sale-datetime]"), offset=wrapper.querySelector("[data-sale-offset]");
      try {
        const result=stored(hidden.value,wrapper.dataset.saleDateField==="sale_end_date");
        input.value=result.text; offset.value=result.zone;
        wrapper.querySelector("[data-sale-date-note]").textContent=result.note;
      } catch (error) { input.value=hidden.value; offset.value=""; wrapper.querySelector("[data-sale-date-note]").textContent=error.message; }
      wrapper.dataset.originalRaw=hidden.value; wrapper.dataset.originalText=input.value; wrapper.dataset.originalOffset=offset.value;
      input.setCustomValidity(""); wrapper.querySelector("[data-sale-date-error]").hidden=true;
    });
  }
  function syncDates(root) {
    root.querySelectorAll("[data-sale-date-field]").forEach(wrapper => {
      const hidden=wrapper.querySelector("[data-field]"), input=wrapper.querySelector("[data-sale-datetime]"), offset=wrapper.querySelector("[data-sale-offset]"), error=wrapper.querySelector("[data-sale-date-error]");
      try {
        hidden.value=input.value===wrapper.dataset.originalText && offset.value===wrapper.dataset.originalOffset ? wrapper.dataset.originalRaw : parse(input.value,offset.value);
        input.setCustomValidity(""); error.hidden=true;
      } catch (failure) { hidden.value="Invalid UK date/time"; input.setCustomValidity(failure.message); error.textContent=failure.message; error.hidden=false; }
    });
  }
  function reason(snapshot,pending=false) {
    if (!snapshot || !snapshot.can_save) return "Restore the current valid source and reload the editor before Review Sale.";
    if (snapshot.dirty) return "Unsaved changes: Save Locally before Review Sale.";
    if (snapshot.busy) return "Wait for the local save to finish before Review Sale.";
    if (pending) return "Saved JSON is awaiting scanner projection. Review Sale becomes available after completion.";
    return "";
  }
  global.TLCSaleDates={parse,stored,display,zone,refresh:refreshDates,sync:syncDates,reason};
  if (!global.document) return;
  const root=document.querySelector("[data-metadata-editor]"), modalElement=document.getElementById("saleReviewModal"), node=document.getElementById("metadata-editor-bootstrap");
  if (!root || !modalElement || !node) return;
  const boot=JSON.parse(node.textContent), button=root.querySelector("[data-review-sale]"), message=root.querySelector("[data-sale-readiness]"), confirm=modalElement.querySelector("[data-sale-authorise]"), ack=modalElement.querySelector("[data-sale-ack]"), modalMessage=modalElement.querySelector("[data-sale-modal-message]");
  let ready=false,pending=false,checking=false,confirming=false,review=null,sequence=0,scroll=null;
  const snapshot=()=>global.TLCMetadataSaleEditor?.snapshot();
  const signature=value=>JSON.stringify([value.revision,value.kind,value.data]);
  function refresh() {
    let why;
    try { why=reason(snapshot(),pending); } catch (_error) { why="Correct invalid JSON/date-time and Save Locally before Review Sale."; }
    if (why) { ready=false; review=null; ++sequence; message.textContent=why; }
    button.disabled=!!why || !ready || checking || confirming;
    confirm.disabled=!review || !ready || !ack.checked || confirming || !!why;
    if (why && !modalElement.hidden && modalElement.classList.contains("show")) modalMessage.textContent=why+" The old review is no longer executable.";
    return why;
  }
  async function request(action, value, extra={}) {
    const controller=new AbortController(), timeout=global.setTimeout(()=>controller.abort(),action==="status" ? 15000 : 120000);
    try {
      const response=await fetch(boot.sale_editor_url,{method:"POST",credentials:"same-origin",signal:controller.signal,headers:{"Content-Type":"application/json","X-CSRFToken":document.querySelector('meta[name="csrf-token"]')?.content || ""},body:JSON.stringify({action,kind:value.kind,revision:value.revision,data:value.data,...extra})});
      const result=await response.json().catch(()=>{throw new Error("Response unavailable. Check saved schedule status; do not repeat an uncertain confirmation.");});
      if (!response.ok) throw new Error(result.error || "Saved sale review is unavailable.");
      return result;
    } catch (error) {
      if (error.name==="AbortError") throw new Error("Sale response timed out. Check saved schedule status; no automatic retry.");
      throw error;
    } finally { global.clearTimeout(timeout); }
  }
  async function check() {
    let value;
    try { value=snapshot(); } catch (_error) { return refresh(); }
    if (reason(value)) return refresh();
    const before=signature(value); checking=true;ready=false;review=null;refresh();
    const ticket=++sequence;
    message.textContent="Checking saved JSON and completed scanner projection…";
    try {
      const result=await request("status",value);
      if (ticket!==sequence || snapshot().dirty || signature(snapshot())!==before) return;
      pending=false;ready=result.ready;message.textContent=result.message;
      root.querySelector("[data-sale-status]").textContent=result.schedule.state.replaceAll("_"," ");
    } catch (error) { if (ticket===sequence) { ready=false;message.textContent=error.message; } }
    finally { checking=false;refresh(); }
  }
  function render(result) {
    const set=(key,text)=>{modalElement.querySelector(`[data-sale-${key}]`).textContent=text;};
    set("product",`${result.product.title} · ${result.product.sku}`);set("start",result.start);set("end",result.end);
    set("schedule",`Current schedule: ${result.schedule.state.replaceAll("_"," ")} · ${result.schedule.message}`);set("action",result.action);set("variant-count",String(result.variants.length));
    const target=modalElement.querySelector("[data-sale-variants]");target.replaceChildren();
    result.variants.forEach(variant=>{
      const card=document.createElement("article");
      for (const text of [variant.sku,`Regular: ${variant.regular}`,`Sale: ${variant.sale ?? "No discount"}`,`After expiry: ${variant.regular}`]) { const line=document.createElement("p");line.textContent=text;card.appendChild(line); }
      target.appendChild(card);
    });
    const warnings=modalElement.querySelector("[data-sale-warnings]");warnings.replaceChildren();
    result.warnings.forEach(text=>{const item=document.createElement("li");item.textContent=text;warnings.appendChild(item);});
    modalElement.querySelector("[data-sale-review-content]").hidden=false;
  }
  async function open() {
    if (refresh() || button.disabled || !global.bootstrap?.Modal) return;
    const value=snapshot(), before=signature(value), ticket=++sequence;checking=true;
    scroll=[global.scrollX,global.scrollY];review=null;ack.checked=false;
    modalElement.querySelector("[data-sale-review-content]").hidden=true;modalMessage.textContent="Reading the saved product and Shopify comparison. No authorisation has been submitted.";
    global.bootstrap.Modal.getOrCreateInstance(modalElement).show();refresh();
    try {
      const result=await request("preview",value);
      if (ticket!==sequence || snapshot().dirty || signature(snapshot())!==before) return;
      render(result);review=result.review;modalMessage.textContent="Review the saved prices and dates, then confirm explicitly.";
    } catch (error) { if (ticket===sequence) { review=null;ready=false;modalMessage.textContent=message.textContent=error.message; } }
    finally { checking=false;refresh(); }
  }
  async function authorise() {
    if (refresh() || confirm.disabled || confirming) return;
    const value=snapshot(), token=review;
    confirming=true;confirm.textContent="Saving authorisation…";refresh();
    try {
      const result=await request("confirm",value,{review:token,acknowledge:ack.checked});
      modalMessage.textContent=message.textContent=result.message;
      root.querySelector("[data-sale-status]").textContent=result.schedule.state.replaceAll("_"," ");
      review=null;ack.checked=false;
    } catch (error) { review=null;ready=false;modalMessage.textContent=message.textContent=error.message+" Check saved schedule status before building another review; no automatic resubmission."; }
    finally { confirming=false;confirm.textContent="Authorise reviewed sale";refresh(); }
  }
  button.addEventListener("click",open);root.querySelector("[data-sale-check]").addEventListener("click",check);
  global.addEventListener("focus",()=>{try{if (!confirming && !checking && !modalElement.classList.contains("show") && !snapshot()?.dirty) check();}catch(_error){refresh();}});
  confirm.addEventListener("click",authorise);ack.addEventListener("change",refresh);
  modalElement.addEventListener("hidden.bs.modal",()=>{
    ++sequence;review=null;ack.checked=false;refresh();
    if (scroll) { global.scrollTo(...scroll);button.focus({preventScroll:true}); }
    // Deliberately do not abort/resubmit an in-flight confirmation.
  });
  root.querySelectorAll("[data-sale-date-field]").forEach(wrapper=>{
    wrapper.querySelector("[data-sale-now]").addEventListener("click",()=>{
      const date=new Date();wrapper.querySelector("[data-sale-datetime]").value=display(date);wrapper.querySelector("[data-sale-offset]").value=zone(date);
      wrapper.querySelector("[data-sale-datetime]").dispatchEvent(new Event("input",{bubbles:true}));
    });
    wrapper.querySelector("[data-sale-clear]").addEventListener("click",()=>{
      wrapper.querySelector("[data-sale-datetime]").value="";wrapper.querySelector("[data-sale-offset]").value="";
      wrapper.querySelector("[data-sale-datetime]").dispatchEvent(new Event("input",{bubbles:true}));
    });
  });
  function clock() { const date=new Date(), target=root.querySelector("[data-sale-clock]"); target.textContent=`${display(date)}:${String(date.getSeconds()).padStart(2,"0")} ${zone(date)}`;target.dateTime=date.toISOString(); }
  clock();let timer=global.setInterval(clock,1000);global.addEventListener("pagehide",()=>global.clearInterval(timer));
  global.addEventListener("pageshow",event=>{if(event.persisted){clock();timer=global.setInterval(clock,1000);}});
  global.TLCSaleEditor={refresh,check,saved:payload=>{pending=!!(payload.run_id || payload.scan_pending);ready=false;review=null;refresh();if (!pending) global.setTimeout(check,0);},scanned:success=>{pending=!success;ready=false;refresh();if (success) check();}};
})(globalThis);
