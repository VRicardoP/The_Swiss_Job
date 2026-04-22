GOAL:
Improve structure and readability with minimal behavioral risk.

CONTEXT:
Describe the module and why it needs refactor.

CODE:
Paste the target function, class, or small module only.

TASK:
1. Analyze the code.
2. List concrete problems only.
3. Propose a step-by-step refactor plan.
4. Apply only step 1 unless I ask for more.

CONSTRAINTS:
- keep same external API
- no new dependencies
- preserve tests and behavior
- prefer smaller functions and clearer naming
- avoid broad rewrites

OUTPUT:
- problems
- refactor plan
- updated code for step 1 only
- short rationale

PROJECT NOTES:
- In backend, prefer isolating business logic from routers.
- In frontend, preserve current UX unless requested otherwise.
