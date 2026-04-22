GOAL:
Optimize for performance or resource usage without damaging correctness.

CONTEXT:
Describe the workload, scale, and current bottleneck.

CURRENT STATE:
Include current behavior, latency, memory, query count, or throughput if known.

CODE:
Paste only the hot path, query, loop, task, or component involved.

TASK:
1. Identify the main bottlenecks.
2. Rank improvements by impact vs complexity.
3. Apply the highest-value low-risk improvement first.

CONSTRAINTS:
- preserve behavior
- no speculative micro-optimizations
- no new dependencies unless justified
- explain tradeoffs briefly

OUTPUT:
- bottlenecks
- chosen optimization
- updated code
- expected impact

PROJECT NOTES:
- Backend: watch N+1 queries, repeated normalization, duplicate fetches, blocking I/O.
- Frontend: watch over-rendering, large lists, blocking effects, unnecessary requests.
- Scrapers/providers: watch serial network calls and duplicated parsing work.
