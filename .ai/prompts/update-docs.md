GOAL:
Update the project documentation and the local AI workflow layer as a single maintenance task.

CONTEXT:
This repository uses local documentation and AI workflow assets under `.ai/`, plus editor and agent config such as `.claude/` and `.vscode/`.

TASK:
Perform a maintenance pass with this scope:

1. Update all relevant project documentation affected by the current codebase.
2. Remove obsolete, duplicated, or misleading content.
3. Reorganize documents that have grown inefficient or noisy.
4. Review and optimize the local AI workflow assets:
   - prompts
   - commands
   - checklists
   - stable context files
   - relevant hooks, permissions, or supporting tool config if present
5. Keep the system efficient for token usage, reuse, and long-term maintainability.

CONSTRAINTS:
- do not keep obsolete content for historical reasons unless explicitly requested
- prefer consolidation over proliferation of files
- preserve only the instructions that still improve output quality
- keep naming predictable and reusable
- if a file is too broad, split only when that reduces maintenance cost

OUTPUT:
- files to update
- obsolete content to delete
- optimizations to apply
- final changes made

SUCCESS CRITERIA:
- documentation matches the real repo state
- redundant guidance is removed
- the `.ai/` layer is shorter, clearer, and easier to reuse
- tooling instructions are aligned with the current project
