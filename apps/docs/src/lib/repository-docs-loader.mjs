import { readFile } from 'node:fs/promises';
import { relative, resolve, sep } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

import {
  DOCUMENTS,
  REPOSITORY_URL,
  getStatusPresentation,
  validateDocumentManifest,
} from './document-manifest.mjs';
import { assertInsideRepository } from './repository-links.mjs';

const DEFAULT_REPOSITORY_ROOT = fileURLToPath(new URL('../../../../', import.meta.url));

export function extractTitleAndBody(contents, sourcePath) {
  const lines = contents.replaceAll('\r\n', '\n').split('\n');
  let fence = null;
  let titleIndex = -1;
  let title = '';

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const fenceMatch = line.match(/^\s{0,3}(`{3,}|~{3,})/u);
    if (fenceMatch) {
      const marker = fenceMatch[1][0];
      if (!fence) fence = marker;
      else if (fence === marker) fence = null;
      continue;
    }
    if (fence) continue;

    const headingMatch = line.match(/^\s{0,3}#(?!#)\s+(.+?)\s*#*\s*$/u);
    if (!headingMatch) continue;
    if (titleIndex >= 0) {
      throw new Error(`${sourcePath} must contain only one level-one heading.`);
    }
    titleIndex = index;
    title = normalizeTitle(headingMatch[1]);
  }

  if (titleIndex < 0 || !title) {
    throw new Error(`${sourcePath} must contain a level-one heading.`);
  }

  lines.splice(titleIndex, 1);
  return { title, body: lines.join('\n') };
}

export function repositoryDocsLoader({
  documents = DOCUMENTS,
  repositoryRoot = DEFAULT_REPOSITORY_ROOT,
} = {}) {
  validateDocumentManifest(documents);
  const root = resolve(repositoryRoot);

  return {
    name: 'zhiyi-repository-docs-loader',
    load: async (context) => {
      const markdownEntryType = context.entryTypes.get('.md');
      if (!markdownEntryType?.getRenderFunction) {
        throw new Error('Astro Markdown entry renderer is unavailable for the ZHIYI documentation loader.');
      }

      const render = await markdownEntryType.getRenderFunction(context.config);
      const untouched = new Set(context.store.keys());
      const sourceByAbsolutePath = new Map();

      const syncDocument = async (document) => {
        const absolutePath = assertInsideRepository(root, resolve(root, document.sourcePath), document.sourcePath);
        sourceByAbsolutePath.set(absolutePath, document);
        const contents = await readFile(absolutePath, 'utf8').catch((error) => {
          throw new Error(`${document.sourcePath} cannot be read: ${error.message}`, { cause: error });
        });
        const { title, body: sourceBody } = extractTitleAndBody(contents, document.sourcePath);
        const fileUrl = pathToFileURL(absolutePath);
        const { body, data: sourceData } = await markdownEntryType.getEntryInfo({
          contents: sourceBody,
          fileUrl,
        });
        const data = createFrontmatter(document, title, sourceData);
        const parsedData = await context.parseData({
          id: document.id,
          data,
          filePath: absolutePath,
        });
        const digest = context.generateDigest(`${contents}\0${JSON.stringify(document)}`);
        const rendered = await render({
          id: document.id,
          data,
          body,
          filePath: absolutePath,
          digest,
        }).catch((error) => {
          throw new Error(`${document.sourcePath} failed to render: ${error.message}`, { cause: error });
        });

        context.store.set({
          id: document.id,
          data: parsedData,
          body,
          filePath: relative(fileURLToPath(context.config.root), absolutePath).split(sep).join('/'),
          digest,
          rendered,
          assetImports: rendered?.metadata?.imagePaths,
        });
        untouched.delete(document.id);
      };

      for (const document of documents) await syncDocument(document);
      for (const staleId of untouched) context.store.delete(staleId);

      if (!context.watcher) return;
      const sourcePaths = [...sourceByAbsolutePath.keys()];
      context.watcher.add(sourcePaths);

      const reload = async (changedPath) => {
        const document = sourceByAbsolutePath.get(resolve(changedPath));
        if (!document) return;
        try {
          await syncDocument(document);
          context.logger.info(`Reloaded ${document.sourcePath}`);
        } catch (error) {
          context.logger.error(error instanceof Error ? error.message : String(error));
        }
      };
      context.watcher.on('change', reload);
      context.watcher.on('add', reload);
      context.watcher.on('unlink', (deletedPath) => {
        const document = sourceByAbsolutePath.get(resolve(deletedPath));
        if (!document) return;
        context.store.delete(document.id);
        context.logger.error(`${document.sourcePath} was removed; its documentation route is unavailable.`);
      });
    },
  };
}

function createFrontmatter(document, title, sourceData) {
  const status = getStatusPresentation(document.status);
  const encodedSource = document.sourcePath.split('/').map(encodeURIComponent).join('/');
  const banner = document.status === 'design-target'
    ? { content: '<strong>方案基线</strong>：本文描述已批准的设计目标，不代表 Runtime 已交付。' }
    : sourceData.banner;

  return {
    ...sourceData,
    title,
    description: document.description,
    editUrl: `${REPOSITORY_URL}/edit/main/${encodedSource}`,
    lastUpdated: false,
    pagefind: true,
    contentStatus: document.status,
    sourcePath: document.sourcePath,
    sidebar: {
      ...sourceData.sidebar,
      label: document.sidebarLabel,
      order: document.order,
      badge: status,
    },
    ...(banner ? { banner } : {}),
  };
}

function normalizeTitle(rawTitle) {
  const title = rawTitle
    .replace(/!?(?:\[([^\]]+)\])\([^)]*\)/gu, '$1')
    .replace(/[*_~`]/gu, '')
    .replace(/<[^>]+>/gu, '')
    .trim();
  if (!title) throw new Error('Documentation level-one heading cannot be empty.');
  return title;
}
