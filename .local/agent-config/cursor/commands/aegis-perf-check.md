# /aegis-perf-check

Audit the current code for line-level performance quality.

## Steps
1. Identify the hot path.
2. Inspect each line for cost, copies, allocations, and branches.
3. Remove or simplify anything without a measurable purpose.
4. Require a benchmark or measurement plan for each optimization.
5. Report only the best practical path.
