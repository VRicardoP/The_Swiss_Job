GOAL:
Design or fix infrastructure changes with explicit current and desired state.

CONTEXT:
Describe the infra objective in one paragraph.

CURRENT STATE:
Paste the relevant part of `docker-compose.yml`, Dockerfile, env vars, deployment config, or topology summary.

DESIRED STATE:
Describe the target behavior precisely.

TASK:
1. Identify the gap between current and desired state.
2. Propose the minimal implementation plan.
3. Produce only the required config changes.

CONSTRAINTS:
- avoid unnecessary services or moving parts
- keep local developer workflow simple
- state risks around secrets, persistence, networking, and startup order
- concise output

OUTPUT:
- gap analysis
- exact config changes
- validation steps
- rollback notes if relevant

PROJECT NOTES:
- Prefer changes that work cleanly with local Docker Compose.
- Mention impacts on backend, frontend, database, workers, and env vars explicitly.
