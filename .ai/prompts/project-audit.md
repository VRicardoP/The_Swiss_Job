GOAL:
Perform a deep project audit to find issues, risks, and high-value improvements.

CONTEXT:
Analyze this project in depth. Use the existing repository structure, available documentation, configuration, tests, and any relevant agent skills or built-in workflows available in your environment.

DOCUMENTATION:
Review any existing documentation before drawing conclusions, including when available:
- README files
- docs/
- architecture notes
- setup and deployment docs
- agent or workflow docs
- contributor guides
- inline code comments only when relevant

TASK:
Audit the project comprehensively and identify:

1. Technical debt
2. Logic errors
3. Bugs or likely bugs
4. Security vulnerabilities
5. Performance issues
6. Reliability risks
7. Poor error handling
8. Code smells and maintainability problems
9. Refactor opportunities
10. Missing tests or weak coverage areas
11. Configuration or infrastructure risks
12. DX issues that slow safe development

REVIEW METHOD:
- start from existing documentation and stated architecture
- inspect the real implementation, not only the intended design
- compare documentation against actual code behavior
- prioritize concrete findings over general advice
- include only actionable issues
- flag uncertainty explicitly when evidence is incomplete

CONSTRAINTS:
- do not praise the project
- do not give generic best-practice lists without tying them to code
- prefer high-signal findings with file or module references
- separate confirmed issues from probable risks
- keep recommendations proportional to impact
- if the codebase is large, prioritize the highest-risk areas first

OUTPUT:
- findings ordered by severity
- for each finding:
  - title
  - severity
  - affected file, module, or area
  - why it is a problem
  - likely impact
  - recommended fix
- then include:
  - missing information or assumptions
  - top 5 highest-value fixes
  - suggested audit next steps

OPTIONAL FOCUS:
If needed, bias the audit toward one of these areas:
- backend correctness
- frontend correctness
- security
- performance
- infrastructure
- maintainability
- test quality

SHORT VERSION:
Analyze this project in depth using the repository, existing documentation, tests, configuration, and any relevant agent skills. Find technical debt, logic errors, bugs, vulnerabilities, performance issues, refactor opportunities, reliability risks, and missing tests. Compare docs against actual implementation. Return only concrete findings ordered by severity, with file references, impact, and recommended fixes.
