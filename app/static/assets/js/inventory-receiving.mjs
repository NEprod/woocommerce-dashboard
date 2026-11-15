import {cameraSession} from './inventory.mjs';

export function setupStockDestination(form, host, camera = cameraSession) {
  const find = (selector) => form.querySelector(selector);
  const receive = form.dataset.verifiedStock === 'receive';
  const keepUnassigned = find('[data-keep-unassigned]');
  const unassignedNote = find('[data-unassigned-note]');
  const source = find('[name=source]');
  const normalMove = find('[data-normal-move-destination]');
  const normalDestination = normalMove?.querySelector('select');
  const physical = find('[data-physical-destination]');
  const destination = find('[data-scanned-destination]');
  const proof = find('[data-location-proof]');
  const review = find('[data-review-verified-stock]');
  const scan = find('[data-scan-location]');
  const cancelScan = find('[data-cancel-location]');
  const switchLocation = find('[data-use-location]');
  const feedback = find('[data-destination-status]');
  const cameraFeedback = find('[data-scan-status]');
  const suggestions = [...physical.querySelectorAll('input[type=radio]')];
  const item = find('[name=item_id]');
  const quantity = find('[name=qty]');
  const entryUnit = find('[name=entry_unit]');
  let selected, verified, other, sequence = 0;

  const needsScan = () => receive ? !keepUnassigned.checked : source.value === 'unassigned';
  function updateReview() {
    if (!receive && !needsScan()) { review.disabled = false; return; }
    const value = quantity.value.trim();
    const validQuantity = /^\d{1,12}(\.\d{1,6})?$/.test(value) && Number(value) > 0 && Number(value) <= 100_000_000
      && (form.dataset.stockUnit !== 'piece' || entryUnit?.value === 'packs' || Number.isInteger(Number(value)));
    review.disabled = !item.value || !validQuantity || (needsScan() && (!verified || selected?.destination !== verified.destination));
  }
  function show() {
    destination.value = selected?.destination ?? '';
    proof.value = verified?.proof ?? '';
    switchLocation.hidden = !other;
    if (verified) feedback.textContent = `Verified: ${verified.label}`;
    else if (other) feedback.textContent = `Selected ${selected.label}, but scanned ${other.label}. Use the scanned location or rescan the selected label.`;
    else if (selected) feedback.textContent = `Selected: ${selected.label}. Scan its barcode to verify.`;
    else feedback.textContent = 'No destination verified.';
    for (const suggestion of suggestions) suggestion.checked = suggestion.value === destination.value;
    updateReview();
  }
  function clear() {
    sequence++;
    verified = other = undefined;
    cameraFeedback.textContent = '';
    show();
  }
  function switchMode() {
    session.stop();
    selected = undefined;
    clear();
    const scanning = needsScan();
    physical.hidden = destination.disabled = proof.disabled = !scanning;
    if (normalMove) normalMove.hidden = normalDestination.disabled = scanning;
    if (unassignedNote) unassignedNote.hidden = scanning;
    updateReview();
  }
  async function accept(code) {
    const attempt = ++sequence;
    const itemId = item.value;
    cameraFeedback.textContent = 'Checking the scanned barcode…';
    try {
      const reply = await host.fetch(form.dataset.locationCheck, {
        method: 'POST', credentials: 'same-origin',
        body: new URLSearchParams({code, item_id: itemId, request_id: find('[name=request_id]').value,
          csrf_token: find('[name=csrf_token]').value}),
      });
      const scanned = await reply.json();
      if (attempt !== sequence || item.value !== itemId || !needsScan()) return;
      if (!reply.ok || !scanned.proof) {
        cameraFeedback.textContent = scanned.error || 'Could not verify that location. Scan it again.';
        return;
      }
      cameraFeedback.textContent = '';
      if (selected && selected.destination !== scanned.destination) other = scanned;
      else selected = verified = scanned;
      show();
    } catch {
      if (attempt === sequence) cameraFeedback.textContent = 'Could not check the location. Check your connection and scan again.';
    }
  }
  const session = camera(host, physical.querySelector('[data-scan-video]'), (message) => { cameraFeedback.textContent = message; },
    (code) => { void accept(code); }, (running) => { scan.hidden = running; cancelScan.hidden = !running; });
  scan.addEventListener('click', () => { clear(); void session.start(); });
  cancelScan.addEventListener('click', () => { session.stop(); clear(); cameraFeedback.textContent = 'Scan cancelled.'; });
  switchLocation.addEventListener('click', () => {
    if (!other) return;
    selected = verified = other;
    other = undefined;
    show();
  });
  for (const suggestion of suggestions) suggestion.addEventListener('change', () => {
    session.stop();
    selected = {destination: suggestion.value, label: suggestion.dataset.locationLabel};
    clear();
  });
  keepUnassigned?.addEventListener('change', switchMode);
  source?.addEventListener('change', switchMode);
  item.addEventListener('change', switchMode);
  quantity.addEventListener('input', updateReview);
  entryUnit?.addEventListener('change', updateReview);
  const close = () => { session.stop(); clear(); };
  host.addEventListener('pagehide', close);
  host.document.addEventListener('visibilitychange', () => { if (host.document.hidden) close(); });
  switchMode();
}

if (typeof document !== 'undefined') {
  for (const form of document.querySelectorAll('[data-verified-stock]')) setupStockDestination(form, window);
}
