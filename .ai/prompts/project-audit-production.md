GOAL:
Perform a production-grade audit before release or deployment.

CONTEXT:
Analyze this project as if it were about to be deployed to production or exposed to real users. Use the repository, existing documentation, tests, configuration, infrastructure files, and any relevant agent skills or built-in workflows available in your environment.

DOCUMENTATION:
Review available documentation first, including when present:
- README files
- docs/
- deployment and runbooks
- architecture notes
- environment setup
- security notes
- agent or workflow documentation

TASK:
Audit the project with a strict production mindset and identify:

1. Release blockers
2. Security vulnerabilities
3. Data loss or corruption risks
4. Authentication and authorization flaws
5. Reliability and availability risks
6. Concurrency, async, or race-condition issues
7. Error handling and observability gaps
8. Performance bottlenecks under realistic load
9. Misconfiguration risks
10. Infrastructure and deployment weaknesses
11. Weak or missing tests in critical paths
12. Refactors required to reduce operational risk

REVIEW METHOD:
- start from docs, then verify against the actual code
- treat undocumented assumptions as risks
- focus on critical paths first
- prioritize evidence-backed findings
- identify what can fail in production, not just what looks imperfect
- distinguish confirmed issues, probable risks, and missing safeguards

CRITICAL AREAS TO CHECK:
- secrets handling
- input validation
- auth/authz boundaries
- database safety and migrations
- external service failures and retries
- idempotency of background jobs
- timeout and circuit-breaker behavior
- logging, metrics, and traceability
- rollback safety
- startup and deploy failure modes

CONSTRAINTS:
- no praise
- no generic advice unless tied to a concrete risk
- prioritize severity and blast radius
- prefer minimal safe fixes over large rewrites unless a rewrite is necessary
- include file, module, or config references whenever possible

OUTPUT:
- findings ordered by severity:
  - title
  - severity
  - affected file, module, config, or subsystem
  - evidence
  - production impact
  - recommended fix
- then include:
  - release blockers
  - top 5 fixes before production
  - risks that need manual verification
  - residual risk after applying fixes

SHORT VERSION:
Audit this project as a production-ready system. Use the repository, existing documentation, tests, configuration, and any relevant agent skills. Find release blockers, vulnerabilities, logic flaws, reliability risks, data integrity issues, performance bottlenecks, deployment weaknesses, and missing safeguards. Compare documentation against the real implementation. Return only concrete findings ordered by severity, with evidence, impact, and recommended fixes.
