const wasmURL = new URL('./vendor/zxing-wasm-3.1.6/dist/reader/zxing_reader.wasm', import.meta.url).href;
const names = {qr_code: 'QRCode', code_128: 'Code128', ean_13: 'EAN13', ean_8: 'EAN8', upc_a: 'UPCA', upc_e: 'UPCE'};
export const scanFormats = Object.freeze(Object.keys(names));
const fromZXing = new Map(Object.entries(names).map(([native, zxing]) => [zxing, native]));
const retailDigits = {ean_13: 13, ean_8: 8, upc_a: 12, upc_e: 8};
export const scanFrameEdge = 1280;
let readerReady;

export function decodedBarcodeValue(value, format) {
  if (typeof value !== 'string' || !value || !scanFormats.includes(format)) return;
  if (!retailDigits[format]) return value;

  // Native and ZXing both validate retail checksums. Also reject malformed
  // adapter output. UPC-A bars are indistinguishable from a zero-led EAN-13;
  // keep the decoder's representation rather than aliasing either inventory key.
  const zeroLedUPC = format === 'upc_a' && /^0\d{12}$/.test(value);
  if (!zeroLedUPC && (value.length !== retailDigits[format] || !/^\d+$/.test(value))) return;
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
  let frame = 0;
  return async (video) => {
    const {videoWidth, videoHeight} = video;
    if (!videoWidth || !videoHeight) return;
    const scale = Math.min(1, scanFrameEdge / Math.max(videoWidth, videoHeight));
    const width = Math.max(1, Math.round(videoWidth * scale));
    const height = Math.max(1, Math.round(videoHeight * scale));
    if (canvas.width !== width) canvas.width = width;
    if (canvas.height !== height) canvas.height = height;
    // On occasional later attempts level a slightly tilted label. Reuse the
    // same canvas and one decode per tick; most frames remain unmodified.
    const attempt = frame++ % 8;
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
    const [result] = await read(context.getImageData(0, 0, width, height), options);
    return zxingValue(result);
  };
}
