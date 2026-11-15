const wasmURL = new URL('./vendor/zxing-wasm-3.1.6/dist/reader/zxing_reader.wasm', import.meta.url).href;
const names = {qr_code: 'QRCode', code_128: 'Code128', ean_13: 'EAN13', ean_8: 'EAN8', upc_a: 'UPCA', upc_e: 'UPCE',
  data_matrix: 'DataMatrix', code_39: 'Code39Std', itf: 'ITF14'};
export const scanFormats = Object.freeze(Object.keys(names));
const fromZXing = new Map(Object.entries(names).map(([native, zxing]) => [zxing, native]));
// The pinned reader can report the parent symbology for both requested variants.
fromZXing.set('Code39', 'code_39');
fromZXing.set('ITF', 'itf');
const extraFormats = new Set(['data_matrix', 'code_39', 'itf']);
const checkedDigits = {ean_13: 13, ean_8: 8, upc_a: 12, upc_e: 8, itf: 14};
export const scanFrameEdge = 1280;
let readerReady;

export function decodedBarcodeValue(value, format) {
  if (typeof value !== 'string' || !value || !scanFormats.includes(format)) return;
  if (!checkedDigits[format]) return value;

  // Native and ZXing both validate retail checksums. Also reject malformed
  // adapter output. UPC-A bars are indistinguishable from a zero-led EAN-13;
  // keep the decoder's representation rather than aliasing either inventory key.
  const zeroLedUPC = format === 'upc_a' && value.length === 13 && value.startsWith('0');
  // Even ZXing's ITF14 request can return generic ITF of any supported length.
  if (/\D/.test(value) || (!zeroLedUPC && value.length !== checkedDigits[format])) return;
  let digits = value;
  if (format === 'upc_e') {
    const [system, a, b, c, d, e, last, check] = value;
    if (system !== '0' && system !== '1') return;
    // Expand only for checksum validation. The returned value stays UPC-E.
    if (last <= '2') digits = `${system}${a}${b}${last}0000${c}${d}${e}${check}`;
    else if (last === '3') digits = `${system}${a}${b}${c}00000${d}${e}${check}`;
    else if (last === '4') digits = `${system}${a}${b}${c}${d}00000${e}${check}`;
    else digits = `${system}${a}${b}${c}${d}${e}0000${last}${check}`;
  }
  let sum = 0;
  for (let i = digits.length - 1, weight = 1; i >= 0; i--, weight = 4 - weight) sum += (digits.charCodeAt(i) - 48) * weight;
  if (sum % 10 === 0) return value;
}

function zxingValue(result) {
  if (!result?.isValid) return;
  const format = fromZXing.get(result.format) ?? fromZXing.get(result.symbology);
  if (format !== 'upc_e') return decodedBarcodeValue(result.text, format);
  // ZXing exposes the original eight digits separately from its expanded text.
  try {
    return decodedBarcodeValue(JSON.parse(result.extra).UPCE, format);
  } catch {
    return undefined;
  }
}

function loadReader() {
  readerReady ??= import('./vendor/zxing-wasm-3.1.6/dist/es/reader/index.js').then(async (reader) => {
    try {
      await reader.prepareZXingModule({overrides: {locateFile: () => wasmURL}, fireImmediately: true});
      return reader.readBarcodes;
    } catch (error) {
      reader.purgeZXingModule();
      throw error;
    }
  }).catch((error) => {
    readerReady = undefined;
    throw error;
  });
  return readerReady;
}

export async function browserBarcodeFallback(host, formats) {
  const canvas = host.document.createElement('canvas');
  const context = canvas.getContext('2d', {willReadFrequently: true});
  if (!context) throw new Error('Camera frame capture is unavailable.');
  const read = await loadReader();
  const options = {formats: formats.map((format) => names[format]), maxNumberOfSymbols: 1, tryRotate: true};
  const fastOptions = {...options, formats: formats.filter((format) => !extraFormats.has(format)).map((format) => names[format])};
  let frame = 0;
  return async (video) => {
    const {videoWidth, videoHeight} = video;
    if (!videoWidth || !videoHeight) return;
    const frameIndex = frame++;
    // Clutter makes the extra searches noticeably heavier. Keep the original
    // formats on every tick and include the added ones on every third tick.
    const frameOptions = frameIndex % 3 === 0 ? options : fastOptions;
    if (!frameOptions.formats.length) return; // ZXing would otherwise search every format.
    const scale = Math.min(1, scanFrameEdge / Math.max(videoWidth, videoHeight));
    const width = Math.max(1, Math.round(videoWidth * scale));
    const height = Math.max(1, Math.round(videoHeight * scale));
    if (canvas.width !== width) canvas.width = width;
    if (canvas.height !== height) canvas.height = height;
    // On occasional later attempts level a slightly tilted label. Reuse the
    // same canvas and one decode per tick; most frames remain unmodified.
    const attempt = frameIndex % 8;
    if (attempt === 3 || attempt === 7) {
      context.save();
      context.fillStyle = '#fff';
      context.fillRect(0, 0, width, height);
      context.translate(width / 2, height / 2);
      context.rotate((attempt === 3 ? 2 : -2) * Math.PI / 180);
      context.drawImage(video, -width / 2, -height / 2, width, height);
      context.restore();
    } else {
      context.drawImage(video, 0, 0, width, height);
    }
    const pixels = context.getImageData(0, 0, width, height);
    let [result] = await read(pixels, frameOptions);
    if (result?.isValid && result.format === 'DataMatrix' && result.contentType !== 'Text') {
      // HRI adds presentation formatting to e.g. GS1. Read only this valid
      // Data Matrix again as plain content, leaving existing formats' HRI alone.
      [result] = await read(pixels, {...options, formats: ['DataMatrix'], textMode: 'Plain'});
    }
    return zxingValue(result);
  };
}
