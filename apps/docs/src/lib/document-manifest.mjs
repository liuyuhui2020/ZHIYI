export const REPOSITORY_URL = 'https://github.com/liuyuhui2020/ZHIYI';

const GROUPS = Object.freeze({
  product: '产品与范围',
  architecture: '架构与计划',
  development: '开发与治理',
});

const STATUS_PRESENTATION = Object.freeze({
  established: Object.freeze({ text: '已建立', variant: 'success' }),
  'design-target': Object.freeze({ text: '设计', variant: 'note' }),
  planned: Object.freeze({ text: '计划', variant: 'caution' }),
});

export const DOCUMENTS = Object.freeze([
  {
    sourcePath: 'doc/产品价值.md',
    id: 'docs/product-value',
    description: '了解 ZHIYI 为 Agent 开发、业务使用、平台运维与安全治理创造的核心价值。',
    sidebarLabel: '产品价值',
    group: 'product',
    order: 10,
    status: 'design-target',
  },
  {
    sourcePath: 'doc/需求文档.md',
    id: 'docs/requirements',
    description: '查看 ZHIYI 的产品范围、角色、功能需求、非功能目标与阶段验收标准。',
    sidebarLabel: '需求文档',
    group: 'product',
    order: 20,
    status: 'design-target',
  },
  {
    sourcePath: 'doc/功能文档.md',
    id: 'docs/features',
    description: '浏览 Agent、Run、Model、Context、Memory、RAG、Tool 与审批等功能设计。',
    sidebarLabel: '功能文档',
    group: 'product',
    order: 30,
    status: 'design-target',
  },
  {
    sourcePath: 'doc/技术方案.md',
    id: 'docs/architecture',
    description: '深入 ZHIYI 的分层架构、Agent Loop、持久化、可靠性、安全与可观测设计。',
    sidebarLabel: '技术方案',
    group: 'architecture',
    order: 40,
    status: 'design-target',
  },
  {
    sourcePath: 'doc/PROJECT.md',
    id: 'docs/roadmap',
    description: '掌握 ZHIYI 的当前状态、里程碑、已确认决策、风险登记与项目门禁。',
    sidebarLabel: '项目路线图',
    group: 'architecture',
    order: 50,
    status: 'established',
  },
  {
    sourcePath: 'doc/SDD开发规范.md',
    id: 'docs/development/sdd',
    description: '按 Spec Kit SDD、设计漂移报告、Git Hooks 与 CI 门禁开展受治理的开发。',
    sidebarLabel: 'SDD 开发规范',
    group: 'development',
    order: 60,
    status: 'established',
  },
  {
    sourcePath: 'doc/AGENTS.md',
    id: 'docs/development/agent-guidelines',
    description: '查看 AI 编程 Agent 必须遵守的架构、测试、安全、依赖与完成标准。',
    sidebarLabel: 'Agent 工作规范',
    group: 'development',
    order: 70,
    status: 'established',
  },
].map((entry) => Object.freeze(entry)));

export function validateDocumentManifest(documents = DOCUMENTS) {
  if (!Array.isArray(documents) || documents.length === 0) {
    throw new Error('Documentation manifest must contain at least one source.');
  }

  const sources = new Set();
  const routes = new Set();
  const orders = new Set();

  for (const document of documents) {
    const { sourcePath, id, description, sidebarLabel, group, order, status } = document ?? {};
    if (typeof sourcePath !== 'string' || !/^doc\/(?!.*(?:^|\/)\.\.(?:\/|$)).+\.md$/u.test(sourcePath)) {
      throw new Error(`Invalid documentation source path: ${String(sourcePath)}`);
    }
    if (sources.has(sourcePath)) {
      throw new Error(`Duplicate source path in documentation manifest: ${sourcePath}`);
    }
    sources.add(sourcePath);

    if (typeof id !== 'string' || !/^docs\/[a-z0-9]+(?:[/-][a-z0-9]+)*$/u.test(id)) {
      throw new Error(`Invalid documentation route ID for ${sourcePath}: ${String(id)}`);
    }
    if (routes.has(id)) {
      throw new Error(`Duplicate route ID in documentation manifest: ${id}`);
    }
    routes.add(id);

    if (typeof description !== 'string' || description.trim().length < 20) {
      throw new Error(`Description is too short for ${sourcePath}.`);
    }
    if (typeof sidebarLabel !== 'string' || sidebarLabel.trim() === '') {
      throw new Error(`Sidebar label is required for ${sourcePath}.`);
    }
    if (!Object.hasOwn(GROUPS, group)) {
      throw new Error(`Unknown documentation group for ${sourcePath}: ${String(group)}`);
    }
    if (!Number.isInteger(order) || order < 1 || orders.has(order)) {
      throw new Error(`Documentation order must be a unique positive integer: ${sourcePath}.`);
    }
    orders.add(order);
    if (!Object.hasOwn(STATUS_PRESENTATION, status)) {
      throw new Error(`Unknown content status for ${sourcePath}: ${String(status)}`);
    }
  }

  return true;
}

export function getStatusPresentation(status) {
  const presentation = STATUS_PRESENTATION[status];
  if (!presentation) throw new Error(`Unknown content status: ${String(status)}`);
  return presentation;
}

export function routeForDocument(document) {
  return `/${document.id}/`;
}

export function createSidebar(documents = DOCUMENTS) {
  validateDocumentManifest(documents);

  const entries = [
    {
      label: '开始',
      items: [
        { label: '官网首页', link: '/' },
      ],
    },
  ];

  for (const [group, label] of Object.entries(GROUPS)) {
    const items = documents
      .filter((document) => document.group === group)
      .sort((left, right) => left.order - right.order)
      .map((document) => ({
        slug: document.id,
        label: document.sidebarLabel,
        badge: getStatusPresentation(document.status),
      }));
    entries.push({ label, items });
  }

  return entries;
}

validateDocumentManifest(DOCUMENTS);
