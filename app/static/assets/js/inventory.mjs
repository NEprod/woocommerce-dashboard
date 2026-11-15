import {browserBarcodeFallback} from './inventory-barcode-fallback.mjs';

const formats = ['qr_code', 'code_128'];

export const normalizeCode = (raw) => {
  const code = String(raw || '').trim();
  return /^[A-Za-z0-9][A-Za-z0-9-]{0,23}$/.test(code) ? code.toUpperCase() : null;
};

export function barcodeOption(options, raw) {
  const code = normalizeCode(raw);
  return code && Array.from(options).find((option) => option.dataset.code === code);
}

async function scannerFor(host, fallback) {
  let native, available = [];
  try {
    const supported = await host.BarcodeDetector?.getSupportedFormats?.();
    available = formats.filter((format) => supported?.includes(format));
    if (available.length) native = new host.BarcodeDetector({formats: available});
  } catch {
    available = [];
  }
  let local;
  const missing = formats.filter((format) => !available.includes(format));
  if (missing.length) {
    try {
      local = await fallback(host, missing);
    } catch (error) {
      if (!native) throw error;
    }
  }
  return {
    formats: local ? formats : available,
    async read(video) {
      const results = native && await native.detect(video);
      return results?.[0]?.rawValue || local?.(video);
    },
  };
}

// No stock actions are ever performed by the scanner. Its only output is a
// decoded code; camera frames are handled on this device and never uploaded.
export function cameraSession(host, video, status, onCode, onRunning, fallback = browserBarcodeFallback) {
  let stream, tick, expiry, generation = 0;
  function stop() {
    generation++;
    host.clearTimeout(tick);
    host.clearTimeout(expiry);
    stream?.getTracks().forEach((track) => track.stop());
    stream = undefined;
    video.srcObject = null;
    video.hidden = true;
    onRunning(false);
  }
  async function start() {
    stop();
    const request = generation;
    if (!host.isSecureContext) {
      status('Camera scanning requires trusted HTTPS. Enter the barcode or choose by name.');
      return;
    }
    if (!host.navigator?.mediaDevices?.getUserMedia) {
      status('This browser cannot access a camera. Enter the barcode or choose by name.');
      return;
    }
    try {
      onRunning(true);
      status('Waiting for camera permission…');
      expiry = host.setTimeout(() => {
        stop();
        status('The camera or barcode reader did not start in time. Try again or enter the code.');
      }, 30000);
      // Ask directly from the camera-button gesture, before loading the fallback.
      const media = await host.navigator.mediaDevices.getUserMedia({audio: false, video: {facingMode: {ideal: 'environment'}}});
      if (generation !== request) {
        media.getTracks().forEach((track) => track.stop());
        return;
      }
      stream = media;
      video.srcObject = media;
      video.hidden = false;
      await video.play();
      if (generation !== request) return;
      status('Preparing the barcode reader…');
      let reader;
      try {
        reader = await scannerFor(host, fallback);
      } catch {
        if (generation !== request) return;
        stop();
        status('The barcode reader could not start. Reload the page, try another browser or enter the code.');
        return;
      }
      if (generation !== request) return;
      const label = reader.formats.length === 2 ? 'a QR code or Code 128 label'
        : reader.formats[0] === 'qr_code' ? 'a QR code' : 'a Code 128 label';
      status(`Point the camera at ${label}.`);
      host.clearTimeout(expiry);
      expiry = host.setTimeout(() => {
        stop();
        status('No readable barcode found. Try again with better light, or enter the code.');
      }, 30000);
      async function detect() {
        if (generation !== request) return;
        try {
          if (video.readyState >= 2) {
            const code = await reader.read(video);
            if (generation !== request) return;
            if (code) {
              stop();
              onCode(code);
              return;
            }
          }
          tick = host.setTimeout(detect, 180);
        } catch {
          if (generation !== request) return;
          stop();
          status('The barcode could not be scanned. Try again or enter the code.');
        }
      }
      await detect();
    } catch (error) {
      if (generation !== request) return;
      stop();
      status(error?.name === 'NotAllowedError' || error?.name === 'SecurityError'
        ? 'Camera access was denied. Allow it in browser settings or enter the barcode.'
        : 'Could not open the camera. Check that it is available or enter the barcode.');
    }
  }
  return {start, stop};
}

export function initialize(doc, host) {
  let activeCamera;
  for (const block of doc.querySelectorAll('[data-workshop-scan]')) {
    const video = block.querySelector('[data-scan-video]');
    const start = block.querySelector('[data-scan-open]');
    const stop = block.querySelector('[data-scan-stop]');
    const status = (message) => { block.querySelector('[data-scan-status]').textContent = message; };
    const form = block.closest('form');
    const standalone = block.hasAttribute('data-lookup-scan');
    const input = standalone ? form.querySelector('[data-lookup-code]') : block.querySelector('[data-scan-manual]');
    const target = standalone ? null : doc.getElementById(block.dataset.workshopScan);
    function accept(raw) {
      const code = normalizeCode(raw);
      if (!code) {
        status('That is not a TLC label code. Enter the identifier printed on the label.');
        return;
      }
      input.value = code;
      if (standalone) {
        form.requestSubmit();
        return;
      }
      const match = barcodeOption(target.options, code);
      if (!match) {
        status(`${code} is not available for this field. Check that it is the right active item or location.`);
        return;
      }
      target.value = match.value;
      status(`Selected ${match.textContent.trim()}.`);
    }
    const session = cameraSession(host, video, status, accept, (running) => {
      start.hidden = running;
      stop.hidden = !running;
    });
    start.addEventListener('click', () => {
      activeCamera?.stop();
      activeCamera = session;
      void session.start();
    });
    stop.addEventListener('click', () => { session.stop(); status('Camera stopped.'); });
    block.addEventListener('toggle', () => { if (!block.open) session.stop(); });
    if (!standalone) {
      block.querySelector('[data-scan-select]').addEventListener('click', () => { session.stop(); accept(input.value); });
      input.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter') return;
        event.preventDefault(); // keyboard-wedge scans must not submit the stock form
        session.stop();
        accept(input.value);
      });
    }
    block.hidden = false;
  }
  host.addEventListener('pagehide', () => activeCamera?.stop());
  doc.addEventListener('visibilitychange', () => { if (doc.hidden) activeCamera?.stop(); });
  for (const button of doc.querySelectorAll('[data-inventory-print]')) {
    button.hidden = false;
    button.addEventListener('click', async () => {
      await Promise.allSettled(Array.from(doc.querySelectorAll('.workshop-label img'), (image) => image.decode()));
      host.print();
    });
  }
  const recipe = doc.querySelector('[data-production-estimate]');
  if (recipe) {
    const good = recipe.closest('form').querySelector('[name=qty]');
    function estimate() {
      const pieces = /^[0-9]+$/.test(good.value.trim()) ? Number(good.value) : 0;
      for (const output of recipe.querySelectorAll('[data-expected]')) {
        const amount = pieces * Number(output.dataset.expected) / Number(output.dataset.yield);
        output.textContent = amount && Number.isFinite(amount)
          ? `Estimated for this output: ${new Intl.NumberFormat('en-GB', {maximumFractionDigits: 6}).format(amount)} ${output.dataset.unit}.`
          : '';
      }
    }
    good.addEventListener('input', estimate);
    estimate();
  }
}

if (typeof document !== 'undefined') initialize(document, window);
