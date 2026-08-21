type MermaidApi = typeof import('mermaid')['default'];
type IdleWindow = Window & {
  requestIdleCallback?: (callback: () => void, options?: { timeout: number }) => number;
};

const diagrams = [...document.querySelectorAll<HTMLElement>('pre.mermaid')];
const sources = new WeakMap<HTMLElement, string>();
const visibleDiagrams = new Set<HTMLElement>();

let mermaidPromise: Promise<MermaidApi> | undefined;
let renderQueue = Promise.resolve();
let renderSequence = 0;

for (const diagram of diagrams) {
  sources.set(diagram, diagram.textContent ?? '');
}

if (diagrams.length > 0) {
  observeDiagrams();
  observeTheme();
}

function observeDiagrams() {
  if (!('IntersectionObserver' in window)) {
    diagrams.forEach((diagram) => scheduleRender(diagram));
    return;
  }

  const observer = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      const diagram = entry.target as HTMLElement;
      if (entry.isIntersecting) {
        visibleDiagrams.add(diagram);
        scheduleRender(diagram);
      } else {
        visibleDiagrams.delete(diagram);
      }
    }
  });

  diagrams.forEach((diagram) => observer.observe(diagram));
}

function observeTheme() {
  const observer = new MutationObserver((mutations) => {
    if (!mutations.some((mutation) => mutation.attributeName === 'data-theme')) return;
    diagrams
      .filter((diagram) => diagram.dataset.renderTheme)
      .forEach((diagram) => scheduleRender(diagram));
  });

  observer.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['data-theme'],
  });
}

function scheduleRender(diagram: HTMLElement) {
  const theme = currentTheme();
  if (diagram.dataset.renderTheme === theme || diagram.dataset.queuedTheme === theme) return;

  diagram.dataset.queuedTheme = theme;
  const enqueue = () => {
    renderQueue = renderQueue.then(() => renderDiagram(diagram, theme));
  };

  const idleWindow = window as IdleWindow;
  if (idleWindow.requestIdleCallback) {
    idleWindow.requestIdleCallback(enqueue, { timeout: 1_500 });
  } else {
    window.setTimeout(enqueue, 0);
  }
}

async function renderDiagram(diagram: HTMLElement, requestedTheme: 'dark' | 'light') {
  if (!document.contains(diagram)) return;

  const source = sources.get(diagram) ?? diagram.textContent ?? '';
  try {
    diagram.dataset.mermaidState = 'rendering';
    const mermaid = await loadMermaid();
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: 'strict',
      theme: requestedTheme === 'dark' ? 'dark' : 'default',
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
      flowchart: { curve: 'linear', htmlLabels: false },
    });

    const id = `zhiyi-mermaid-${++renderSequence}`;
    const { svg, bindFunctions } = await mermaid.render(id, source);
    diagram.innerHTML = svg;
    bindFunctions?.(diagram);
    diagram.dataset.renderTheme = requestedTheme;
    diagram.dataset.mermaidState = 'rendered';
  } catch (error) {
    diagram.textContent = source;
    diagram.dataset.mermaidState = 'error';
    console.error('[ZHIYI docs] Mermaid rendering failed.', error);
  } finally {
    delete diagram.dataset.queuedTheme;
    if (diagram.dataset.renderTheme !== currentTheme() && visibleDiagrams.has(diagram)) {
      scheduleRender(diagram);
    }
  }
}

function loadMermaid() {
  mermaidPromise ??= import('mermaid').then(({ default: mermaid }) => mermaid);
  return mermaidPromise;
}

function currentTheme(): 'dark' | 'light' {
  return document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
}
