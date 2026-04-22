GOAL:
Review code changes for bugs, regressions, and missing tests.

CONTEXT:
Briefly describe the change or feature intent.

DIFF OR CODE:
Paste the diff or only the touched code blocks.

TASK:
Review the changes and list findings only.

CONSTRAINTS:
- prioritize correctness, security, performance, and maintainability
- do not praise or summarize first
- order findings by severity
- include missing tests when relevant

OUTPUT:
- findings with file/function references
- open questions or assumptions
- short overall risk summary

PROJECT NOTES:
- Backend reviews should check validation, auth, async behavior, DB usage, provider failure handling.
- Frontend reviews should check state flow, effect correctness, request duplication, and UX regressions.
