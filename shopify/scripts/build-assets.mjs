// Local asset build only. Never invokes Shopify APIs or reads app credentials.
import {build} from 'esbuild';
import {readFile,stat} from 'node:fs/promises';
import {gzipSync} from 'node:zlib';
import {fileURLToPath} from 'node:url';

const root = new URL('../extensions/tlc-customisations/', import.meta.url);
const outfile = fileURLToPath(new URL('assets/tlc-customer-fields.js', root));
await build({
  entryPoints: [fileURLToPath(new URL('../src/tlc-customer-fields.js', import.meta.url))],
  outfile, bundle: true, minify: true, platform: 'browser', target: 'es2022',
  charset: 'utf8', legalComments: 'none',
  banner: {js:'/* Generated from src/tlc-customer-fields.js by npm run build:assets. */'}
});
const {size} = await stat(outfile);
// Shopify's suggested Theme Check budget measures compressed bytes, not source.
const compressed=gzipSync(await readFile(outfile)).length;
if (compressed > 10000) throw Error(`Shopify app-block JavaScript exceeds Theme Check's 10000-byte compressed budget: ${compressed}`);
console.log(`TLC theme asset: ${size} bytes; gzip ${compressed} / 10000 bytes`);
