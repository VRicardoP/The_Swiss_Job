GOAL:
Find the root cause, not only the symptom.

CONTEXT:
Describe the failing workflow in 2-4 lines.

ERROR:
Paste the exact error, traceback, failing response, or broken behavior.

CODE:
Paste only the smallest relevant function, route, service, test, or config.

TASK:
1. Analyze the failure.
2. List likely root causes ordered by probability.
3. Identify the most probable root cause.
4. Propose the minimal safe fix.

CONSTRAINTS:
- do not rewrite unrelated code
- preserve current public behavior unless the bug requires change
- mention assumptions explicitly
- concise output

OUTPUT:
- root cause
- why it happens
- minimal fix
- optional hardening steps

PROJECT NOTES:
- If backend: consider router -> service -> model/schema boundaries.
- If provider or scraper: check selectors, payload shape, timeout, retry, normalization.
- If async task: check idempotency, scheduling, serialization, side effects.
