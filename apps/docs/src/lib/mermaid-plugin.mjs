export function createMermaidSatteriPlugin() {
  return {
    name: 'zhiyi-mermaid',
    code(node) {
      if (node.lang !== 'mermaid') return undefined;

      return {
        type: 'html',
        value: `<pre class="mermaid" data-mermaid-state="source">${escapeHtml(node.value)}</pre>`,
      };
    },
  };
}

export function escapeHtml(value) {
  return String(value).replace(/[&<>"']/gu, (character) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  })[character]);
}
