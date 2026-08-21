import { existsSync, statSync } from 'node:fs';
import { dirname, isAbsolute, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

import { DOCUMENTS, REPOSITORY_URL, routeForDocument } from './document-manifest.mjs';

const DEFAULT_REPOSITORY_ROOT = fileURLToPath(new URL('../../../../', import.meta.url));
const PASSTHROUGH_LINK = /^(?:[a-z][a-z\d+.-]*:|#|\/)/iu;

export function assertInsideRepository(repositoryRoot, targetPath, sourcePath) {
  const root = resolve(repositoryRoot);
  const target = resolve(targetPath);
  const relativeTarget = relative(root, target);
  if (relativeTarget === '..' || relativeTarget.startsWith(`..${sep}`) || isAbsolute(relativeTarget)) {
    throw new Error(`${formatSource(sourcePath, root)} links outside repository: ${targetPath}`);
  }
  return target;
}

export function resolveRepositoryLink({
  url,
  sourcePath,
  repositoryRoot = DEFAULT_REPOSITORY_ROOT,
  documents = DOCUMENTS,
}) {
  if (typeof url !== 'string' || url === '' || PASSTHROUGH_LINK.test(url)) return url;

  const { pathname, suffix } = splitLink(url);
  if (!pathname) return url;

  let decodedPath;
  try {
    decodedPath = decodeURIComponent(pathname);
  } catch {
    throw new Error(`${formatSource(sourcePath, repositoryRoot)} contains an invalid encoded link: ${url}`);
  }

  const absoluteTarget = assertInsideRepository(
    repositoryRoot,
    resolve(dirname(sourcePath), decodedPath),
    sourcePath,
  );
  const relativeTarget = toRepositoryPath(repositoryRoot, absoluteTarget);

  if (!existsSync(absoluteTarget)) {
    throw new Error(`${formatSource(sourcePath, repositoryRoot)} links to missing target: ${pathname}`);
  }

  const published = documents.find((document) => document.sourcePath === relativeTarget);
  if (published) return `${routeForDocument(published)}${suffix}`;

  const kind = statSync(absoluteTarget).isDirectory() ? 'tree' : 'blob';
  const encodedTarget = relativeTarget.split('/').map(encodeURIComponent).join('/');
  return `${REPOSITORY_URL}/${kind}/main/${encodedTarget}${suffix}`;
}

export function repositoryLinksRemark(options = {}) {
  const repositoryRoot = options.repositoryRoot ?? DEFAULT_REPOSITORY_ROOT;
  const documents = options.documents ?? DOCUMENTS;

  return (tree, file) => {
    if (!file.path) {
      throw new Error('Repository link processing requires a source file path.');
    }

    walk(tree, (node) => {
      if ((node.type === 'link' || node.type === 'definition') && typeof node.url === 'string') {
        node.url = resolveRepositoryLink({
          url: node.url,
          sourcePath: file.path,
          repositoryRoot,
          documents,
        });
      }
    });
  };
}

export function repositoryLinksSatteri(options = {}) {
  const repositoryRoot = options.repositoryRoot ?? DEFAULT_REPOSITORY_ROOT;
  const documents = options.documents ?? DOCUMENTS;

  function rewrite(node, context) {
    if (!context.fileURL) {
      throw new Error('Repository link processing requires a source file URL.');
    }

    context.setProperty(node, 'url', resolveRepositoryLink({
      url: node.url,
      sourcePath: fileURLToPath(context.fileURL),
      repositoryRoot,
      documents,
    }));
  }

  return {
    name: 'zhiyi-repository-links',
    link: rewrite,
    definition: rewrite,
  };
}

function splitLink(url) {
  const queryIndex = url.indexOf('?');
  const hashIndex = url.indexOf('#');
  const suffixIndex = [queryIndex, hashIndex].filter((index) => index >= 0).sort((a, b) => a - b)[0];
  if (suffixIndex === undefined) return { pathname: url, suffix: '' };
  return { pathname: url.slice(0, suffixIndex), suffix: url.slice(suffixIndex) };
}

function toRepositoryPath(repositoryRoot, targetPath) {
  return relative(resolve(repositoryRoot), resolve(targetPath)).split(sep).join('/');
}

function formatSource(sourcePath, repositoryRoot) {
  const absoluteSource = resolve(sourcePath);
  try {
    assertSourceInsideRoot(repositoryRoot, absoluteSource);
    return toRepositoryPath(repositoryRoot, absoluteSource);
  } catch {
    return sourcePath;
  }
}

function assertSourceInsideRoot(repositoryRoot, sourcePath) {
  const relativeSource = relative(resolve(repositoryRoot), resolve(sourcePath));
  if (relativeSource === '..' || relativeSource.startsWith(`..${sep}`) || isAbsolute(relativeSource)) {
    throw new Error('outside');
  }
}

function walk(node, visit) {
  if (!node || typeof node !== 'object') return;
  visit(node);
  if (Array.isArray(node.children)) {
    for (const child of node.children) walk(child, visit);
  }
}
