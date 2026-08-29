import { brotliCompressSync, gzipSync } from 'node:zlib';
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { extname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const assetsRoot = fileURLToPath(new URL('../dist/assets/', import.meta.url));
if (!existsSync(assetsRoot)) {
  console.error('dist/assets was not found. Run npm run build before checking budgets.');
  process.exit(1);
}

const limits = {
  '.js': { gzip: 90 * 1024, brotli: 75 * 1024 },
  '.css': { gzip: 28 * 1024, brotli: 23 * 1024 }
};

let failed = false;
let totalGzip = 0;
for (const name of readdirSync(assetsRoot).filter((file) => ['.js', '.css'].includes(extname(file)))) {
  const bytes = readFileSync(join(assetsRoot, name));
  const gzip = gzipSync(bytes, { level: 9 }).byteLength;
  const brotli = brotliCompressSync(bytes).byteLength;
  const limit = limits[extname(name)];
  totalGzip += gzip;
  console.log(`${name}: raw ${(bytes.byteLength / 1024).toFixed(1)} KiB · gzip ${(gzip / 1024).toFixed(1)} KiB · brotli ${(brotli / 1024).toFixed(1)} KiB`);
  if (gzip > limit.gzip || brotli > limit.brotli) {
    console.error(`Budget exceeded for ${name}.`);
    failed = true;
  }
}

const totalLimit = 112 * 1024;
console.log(`Initial JS + CSS gzip total: ${(totalGzip / 1024).toFixed(1)} KiB / ${(totalLimit / 1024).toFixed(0)} KiB`);
if (totalGzip > totalLimit) {
  console.error('Combined initial asset budget exceeded.');
  failed = true;
}

const legacyFonts = readdirSync(assetsRoot).filter((file) => extname(file) === '.woff');
if (legacyFonts.length) {
  console.error(`Unexpected legacy font assets: ${legacyFonts.join(', ')}`);
  failed = true;
}

const fontFiles = readdirSync(assetsRoot).filter((file) => extname(file) === '.woff2');
const fontBytes = fontFiles.reduce((total, name) => total + readFileSync(join(assetsRoot, name)).byteLength, 0);
const fontLimit = 75 * 1024;
console.log(`Self-hosted WOFF2 fonts: ${(fontBytes / 1024).toFixed(1)} KiB / ${(fontLimit / 1024).toFixed(0)} KiB`);
if (fontBytes > fontLimit) {
  console.error('Self-hosted font budget exceeded.');
  failed = true;
}
process.exit(failed ? 1 : 0);
