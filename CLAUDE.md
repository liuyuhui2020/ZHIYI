# Claude Code Instructions

Read and follow [AGENTS.md](./AGENTS.md) and
[the ZHIYI constitution](./.specify/memory/constitution.md) before using tools.

Use the installed `speckit-*` skills for every implementation change. The
project Stop Hook runs the shared design-drift checker. If it blocks completion,
continue by synchronizing Spec/Plan/Tasks/documents or reverting the drift;
never weaken or bypass the Hook.
