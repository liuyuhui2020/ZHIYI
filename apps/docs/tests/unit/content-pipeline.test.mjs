import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile, mkdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

import {
  DOCUMENTS,
  createSidebar,
  validateDocumentManifest,
} from '../../src/lib/document-manifest.mjs';
import {
  assertInsideRepository,
  resolveRepositoryLink,
  repositoryLinksSatteri,
} from '../../src/lib/repository-links.mjs';
import {
  extractTitleAndBody,
} from '../../src/lib/repository-docs-loader.mjs';
import { createMermaidSatteriPlugin } from '../../src/lib/mermaid-plugin.mjs';

test('publishing manifest contains seven unique, stable documentation routes', () => {
  assert.equal(validateDocumentManifest(DOCUMENTS), true);
  assert.equal(DOCUMENTS.length, 7);
  assert.deepEqual(
    DOCUMENTS.map(({ id }) => id),
    [
      'docs/product-value',
      'docs/requirements',
      'docs/features',
      'docs/architecture',
      'docs/roadmap',
      'docs/development/sdd',
      'docs/development/agent-guidelines',
    ],
  );
});

test('manifest rejects duplicate source paths and route IDs', () => {
  const duplicateSource = [DOCUMENTS[0], { ...DOCUMENTS[1], sourcePath: DOCUMENTS[0].sourcePath }];
  const duplicateId = [DOCUMENTS[0], { ...DOCUMENTS[1], id: DOCUMENTS[0].id }];

  assert.throws(() => validateDocumentManifest(duplicateSource), /duplicate source/i);
  assert.throws(() => validateDocumentManifest(duplicateId), /duplicate route/i);
});

test('manifest rejects empty and malformed publishing entries', () => {
  assert.throws(() => validateDocumentManifest([]), /at least one source/i);

  const valid = DOCUMENTS[0];
  const invalidCases = [
    [{ ...valid, sourcePath: '../private.md' }, /invalid documentation source path/i],
    [{ ...valid, id: 'Docs/Invalid' }, /invalid documentation route ID/i],
    [{ ...valid, description: 'too short' }, /description is too short/i],
    [{ ...valid, sidebarLabel: '' }, /sidebar label is required/i],
    [{ ...valid, group: 'unknown' }, /unknown documentation group/i],
    [{ ...valid, order: 0 }, /unique positive integer/i],
    [{ ...valid, status: 'released' }, /unknown content status/i],
  ];

  for (const [entry, expectedError] of invalidCases) {
    assert.throws(() => validateDocumentManifest([entry]), expectedError);
  }
});

test('sidebar is derived from the manifest and exposes every document once', () => {
  const sidebar = createSidebar(DOCUMENTS);
  const slugs = sidebar.flatMap((group) => group.items ?? []).flatMap((item) => item.slug ?? []);

  assert.equal(new Set(slugs).size, DOCUMENTS.length);
  assert.deepEqual(slugs, DOCUMENTS.map(({ id }) => id));
});

test('heading extraction ignores fenced examples and removes only the first real H1', () => {
  const source = [
    '```markdown',
    '# Not the page title',
    '```',
    '',
    '# Real Title',
    '',
    'Body text.',
    '',
    '## Detail',
  ].join('\n');

  const result = extractTitleAndBody(source, 'doc/example.md');

  assert.equal(result.title, 'Real Title');
  assert.match(result.body, /# Not the page title/);
  assert.doesNotMatch(result.body, /^# Real Title$/m);
  assert.match(result.body, /## Detail/);
});

test('heading extraction reports the source when no H1 exists', () => {
  assert.throws(
    () => extractTitleAndBody('## Only a subsection', 'doc/missing.md'),
    /doc\/missing\.md.*level-one heading/i,
  );
});

test('heading extraction handles CRLF and tilde fences and rejects ambiguous titles', () => {
  const source = '~~~markdown\r\n# Fenced title\r\n~~~\r\n\r\n# Real Title\r\n\r\nBody.';
  const result = extractTitleAndBody(source, 'doc/example.md');

  assert.equal(result.title, 'Real Title');
  assert.match(result.body, /# Fenced title/);
  assert.doesNotMatch(result.body, /\r/);
  assert.throws(
    () => extractTitleAndBody('# First\n\n# Second', 'doc/duplicate.md'),
    /only one level-one heading/i,
  );
  assert.throws(
    () => extractTitleAndBody('# **` `**', 'doc/empty.md'),
    /cannot be empty/i,
  );
});

test('source body changes are reflected without changing the stable route', () => {
  const first = extractTitleAndBody('# Stable\n\nVersion one.', 'doc/example.md');
  const second = extractTitleAndBody('# Stable\n\nVersion two.', 'doc/example.md');

  assert.equal(first.title, second.title);
  assert.notEqual(first.body, second.body);
});

test('repository containment rejects paths outside the repository', () => {
  assert.throws(
    () => assertInsideRepository('/repo', '/private/secret.md', 'doc/source.md'),
    /doc\/source\.md.*outside repository/i,
  );
});

test('published Markdown links become site routes and preserve fragments', async () => {
  const sandbox = await createFixtureRepository();
  try {
    const result = resolveRepositoryLink({
      url: './target.md#section-two',
      sourcePath: join(sandbox, 'doc/source.md'),
      repositoryRoot: sandbox,
      documents: [
        { sourcePath: 'doc/target.md', id: 'docs/target' },
      ],
    });

    assert.equal(result, '/docs/target/#section-two');
  } finally {
    await rm(sandbox, { recursive: true, force: true });
  }
});

test('existing unpublished repository links become GitHub source links', async () => {
  const sandbox = await createFixtureRepository();
  try {
    const result = resolveRepositoryLink({
      url: '../README.md',
      sourcePath: join(sandbox, 'doc/source.md'),
      repositoryRoot: sandbox,
      documents: [],
    });

    assert.equal(result, 'https://github.com/liuyuhui2020/ZHIYI/blob/main/README.md');
  } finally {
    await rm(sandbox, { recursive: true, force: true });
  }
});

test('broken relative links fail with source and target evidence', async () => {
  const sandbox = await createFixtureRepository();
  try {
    assert.throws(
      () => resolveRepositoryLink({
        url: './missing.md',
        sourcePath: join(sandbox, 'doc/source.md'),
        repositoryRoot: sandbox,
        documents: [],
      }),
      /doc\/source\.md.*missing\.md/i,
    );
  } finally {
    await rm(sandbox, { recursive: true, force: true });
  }
});

test('external and hash-only links are unchanged', () => {
  for (const url of ['https://example.com/docs', 'mailto:team@example.com', '#local']) {
    assert.equal(resolveRepositoryLink({
      url,
      sourcePath: '/repo/doc/source.md',
      repositoryRoot: '/repo',
      documents: [],
    }), url);
  }
});

test('repository links reject invalid encoding and preserve query and directory targets', async () => {
  const sandbox = await createFixtureRepository();
  try {
    assert.throws(
      () => resolveRepositoryLink({
        url: './%ZZ.md',
        sourcePath: join(sandbox, 'doc/source.md'),
        repositoryRoot: sandbox,
        documents: [],
      }),
      /invalid encoded link/i,
    );

    assert.equal(resolveRepositoryLink({
      url: './target.md?view=compact#section-two',
      sourcePath: join(sandbox, 'doc/source.md'),
      repositoryRoot: sandbox,
      documents: [{ sourcePath: 'doc/target.md', id: 'docs/target' }],
    }), '/docs/target/?view=compact#section-two');

    assert.equal(resolveRepositoryLink({
      url: '.',
      sourcePath: join(sandbox, 'doc/source.md'),
      repositoryRoot: sandbox,
      documents: [],
    }), 'https://github.com/liuyuhui2020/ZHIYI/tree/main/doc');
  } finally {
    await rm(sandbox, { recursive: true, force: true });
  }
});

test('Satteri adapter rewrites links using the current source file URL', async () => {
  const sandbox = await createFixtureRepository();
  try {
    const plugin = repositoryLinksSatteri({
      repositoryRoot: sandbox,
      documents: [{ sourcePath: 'doc/target.md', id: 'docs/target' }],
    });
    const node = { type: 'link', url: './target.md#section-two', children: [] };
    const context = {
      fileURL: new URL(`file://${join(sandbox, 'doc/source.md')}`),
      setProperty(target, property, value) {
        target[property] = value;
      },
    };

    plugin.link(node, context);

    assert.equal(node.url, '/docs/target/#section-two');
  } finally {
    await rm(sandbox, { recursive: true, force: true });
  }
});

test('Satteri adapter requires source context before rewriting links', () => {
  const plugin = repositoryLinksSatteri({ repositoryRoot: '/repo', documents: [] });
  assert.throws(
    () => plugin.link({ type: 'link', url: './target.md' }, { setProperty() {} }),
    /requires a source file URL/i,
  );
});

test('Mermaid Satteri adapter preserves a readable, escaped no-script fallback', () => {
  const plugin = createMermaidSatteriPlugin();
  const transformed = plugin.code({
    type: 'code',
    lang: 'mermaid',
    value: 'flowchart LR\n  A["<script>alert(1)</script>"] --> B',
  });

  assert.equal(transformed.type, 'html');
  assert.match(transformed.value, /<pre class="mermaid"/);
  assert.match(transformed.value, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);
  assert.doesNotMatch(transformed.value, /<script>/);
  assert.equal(plugin.code({ type: 'code', lang: 'text', value: 'plain' }), undefined);
});

async function createFixtureRepository() {
  const sandbox = await mkdtemp(join(tmpdir(), 'zhiyi-docs-'));
  await mkdir(join(sandbox, 'doc'));
  await writeFile(join(sandbox, 'README.md'), '# Repository\n', 'utf8');
  await writeFile(join(sandbox, 'doc/source.md'), '# Source\n', 'utf8');
  await writeFile(join(sandbox, 'doc/target.md'), '# Target\n', 'utf8');
  return sandbox;
}
