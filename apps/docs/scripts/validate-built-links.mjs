import { access, readFile, readdir } from 'node:fs/promises';
import { dirname, extname, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

import { DOCUMENTS, routeForDocument } from '../src/lib/document-manifest.mjs';

const PROJECT_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const DIST_ROOT = join(PROJECT_ROOT, 'dist');
const LOCAL_ORIGIN = 'http://localhost:4321';
const REQUIRED_ROUTES = ['/', ...DOCUMENTS.map(routeForDocument)];
const PASS_THROUGH_PROTOCOLS = new Set(['mailto:', 'tel:']);

const errors = [];
const allOutputFiles = new Set(await listFiles(DIST_ROOT));
const htmlByPath = new Map();

await assertRequiredOutput();

for (const filePath of await listFiles(DIST_ROOT)) {
  if (extname(filePath) !== '.html') continue;
  htmlByPath.set(filePath, await readFile(filePath, 'utf8'));
}

for (const [filePath, html] of htmlByPath) {
  const route = routeForHtml(filePath);
  validateCanonical(filePath, route, html);

  for (const reference of extractReferences(html)) {
    await validateReference(filePath, route, reference);
  }
}

if (errors.length > 0) {
  console.error(`Built-site validation failed with ${errors.length} finding(s):`);
  for (const error of errors) console.error(`- ${error}`);
  process.exitCode = 1;
} else {
  console.log(`Validated ${htmlByPath.size} HTML pages, ${REQUIRED_ROUTES.length} required routes, internal targets, anchors, canonical URLs, sitemap, and Pagefind output.`);
}

async function assertRequiredOutput() {
  for (const route of REQUIRED_ROUTES) {
    await requireFile(fileForRoute(route), `missing required route ${route}`);
  }
  await requireFile(join(DIST_ROOT, 'sitemap-index.xml'), 'missing sitemap-index.xml');
  await requireFile(join(DIST_ROOT, 'pagefind/pagefind.js'), 'missing Pagefind runtime');
  await requireFile(join(DIST_ROOT, 'pagefind/pagefind-entry.json'), 'missing Pagefind index entry');
}

function validateCanonical(filePath, route, html) {
  const canonicalMatches = [...html.matchAll(/<link\b[^>]*\brel=["']canonical["'][^>]*\bhref=["']([^"']+)["'][^>]*>/giu)];
  if (canonicalMatches.length !== 1) {
    errors.push(`${displayPath(filePath)} must contain exactly one canonical link; found ${canonicalMatches.length}`);
    return;
  }

  const canonical = new URL(decodeHtml(canonicalMatches[0][1]), LOCAL_ORIGIN);
  const expectedRoute = route;
  if (canonical.origin !== LOCAL_ORIGIN || canonical.pathname !== expectedRoute) {
    errors.push(`${displayPath(filePath)} canonical is ${canonical.href}; expected ${LOCAL_ORIGIN}${expectedRoute}`);
  }
}

async function validateReference(sourceFile, sourceRoute, rawReference) {
  const reference = decodeHtml(rawReference.trim());
  if (!reference) return;

  let url;
  try {
    url = new URL(reference, `${LOCAL_ORIGIN}${sourceRoute}`);
  } catch {
    errors.push(`${displayPath(sourceFile)} contains invalid reference ${JSON.stringify(reference)}`);
    return;
  }

  if (url.protocol === 'javascript:') {
    errors.push(`${displayPath(sourceFile)} contains forbidden javascript URL ${JSON.stringify(reference)}`);
    return;
  }
  if (PASS_THROUGH_PROTOCOLS.has(url.protocol) || url.origin !== LOCAL_ORIGIN) return;

  const targetFile = fileForUrlPath(url.pathname);
  if (!targetFile) {
    errors.push(`${displayPath(sourceFile)} points to missing target ${url.pathname}`);
    return;
  }

  if (url.hash && extname(targetFile) === '.html') {
    const targetHtml = htmlByPath.get(targetFile) ?? await readFile(targetFile, 'utf8');
    const targetId = safeDecode(url.hash.slice(1));
    const ids = new Set([...targetHtml.matchAll(/\bid=["']([^"']+)["']/giu)].map((match) => decodeHtml(match[1])));
    if (!ids.has(targetId)) {
      errors.push(`${displayPath(sourceFile)} points to missing anchor ${url.pathname}${url.hash}`);
    }
  }
}

function extractReferences(html) {
  return [...html.matchAll(/\b(?:href|src)=["']([^"']+)["']/giu)].map((match) => match[1]);
}

function fileForRoute(route) {
  return route === '/' ? join(DIST_ROOT, 'index.html') : join(DIST_ROOT, route, 'index.html');
}

function fileForUrlPath(pathname) {
  const decodedPath = safeDecode(pathname);
  const normalized = resolve(DIST_ROOT, `.${decodedPath}`);
  if (!isInsideDist(normalized)) return undefined;

  const candidates = decodedPath.endsWith('/')
    ? [join(normalized, 'index.html')]
    : extname(decodedPath)
      ? [normalized]
      : [`${normalized}.html`, join(normalized, 'index.html')];

  return candidates.find((candidate) => htmlByPath.has(candidate) || fileExists(candidate));
}

function routeForHtml(filePath) {
  const outputPath = relative(DIST_ROOT, filePath).split(sep).join('/');
  if (outputPath === 'index.html') return '/';
  if (outputPath.endsWith('/index.html')) return `/${outputPath.slice(0, -'index.html'.length)}`;
  return `/${outputPath}`;
}

function isInsideDist(filePath) {
  const relativePath = relative(DIST_ROOT, filePath);
  return relativePath !== '..' && !relativePath.startsWith(`..${sep}`);
}

function fileExists(filePath) {
  return allOutputFiles.has(filePath);
}

async function requireFile(filePath, message) {
  try {
    await access(filePath);
  } catch {
    errors.push(`${message}: ${displayPath(filePath)}`);
  }
}

async function listFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const nested = await Promise.all(entries.map((entry) => {
    const path = join(directory, entry.name);
    return entry.isDirectory() ? listFiles(path) : [path];
  }));
  return nested.flat();
}

function safeDecode(value) {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

function decodeHtml(value) {
  return value
    .replaceAll('&amp;', '&')
    .replaceAll('&quot;', '"')
    .replaceAll('&#39;', "'")
    .replaceAll('&lt;', '<')
    .replaceAll('&gt;', '>');
}

function displayPath(filePath) {
  return relative(PROJECT_ROOT, filePath).split(sep).join('/');
}
