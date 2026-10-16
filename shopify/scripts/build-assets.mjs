// Local asset build only. Never invokes Shopify APIs or reads app credentials.
import {build} from 'esbuild';
import {stat} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';

const root = new URL('../extensions/tlc-customisations/', import.meta.url);
const outfile = fileURLToPath(new URL('assets/tlc-customer-fields.js', root));
await build({
  entryPoints: [fileURLToPath(new URL('src/tlc-customer-fields.js', root))],
  outfile, bundle: true, minify: true, platform: 'browser', target: 'es2022',
  charset: 'utf8', legalComments: 'none',
  banner: {js:'/* Generated from src/tlc-customer-fields.js by npm run build:assets. */'}
});
const {size} = await stat(outfile);
if (size > 10000) throw Error(`Shopify app-block JavaScript exceeds Theme Check's 10000-byte limit: ${size}`);
console.log(`TLC theme asset: ${size} / 10000 bytes`);
