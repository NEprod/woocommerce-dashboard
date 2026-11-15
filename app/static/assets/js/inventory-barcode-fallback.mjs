const wasmURL = new URL('./vendor/zxing-wasm-3.1.6/dist/reader/zxing_reader.wasm', import.meta.url).href;
const names = {qr_code: 'QRCode', code_128: 'Code128'};
const maxFrameEdge = 960;
let readerReady;

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
  return async (video) => {
    const {videoWidth, videoHeight} = video;
    if (!videoWidth || !videoHeight) return;
    const scale = Math.min(1, maxFrameEdge / Math.max(videoWidth, videoHeight));
    const width = Math.max(1, Math.round(videoWidth * scale));
    const height = Math.max(1, Math.round(videoHeight * scale));
    if (canvas.width !== width) canvas.width = width;
    if (canvas.height !== height) canvas.height = height;
    context.drawImage(video, 0, 0, width, height);
    const [result] = await read(context.getImageData(0, 0, width, height), options);
    return result?.isValid ? result.text : undefined;
  };
}
