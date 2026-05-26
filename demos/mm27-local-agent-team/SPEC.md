# local-agent-demo-dashboard Specification

The team must build a fully local static dashboard generator.

## Required Files

1. `SPEC.md` — project requirements and acceptance criteria.
2. `data/sample_run.jsonl` — sample multi-agent event data.
3. `src/build_dashboard.py` — stdlib-only Python dashboard generator.
4. `tests/test_build_dashboard.py` or equivalent runnable smoke test.
5. `public/index.html` — generated dashboard.
6. `DEMO_CAPTION.md` — short public caption for the recording.
7. `REPORT.md` — orchestrator final report with worker contributions and QA verdict.

## Functional Requirements

1. Running the generator must succeed:
   ```bash
   python3 src/build_dashboard.py data/sample_run.jsonl public/index.html
   ```
2. The generated HTML must include:
   - title or heading containing `Local Agent Team`
   - r0b0tlab brand colors `#00ff88`, `#ff00e5`, and `#00e5ff`
   - at least one table or card summarizing worker contributions
   - no external network dependencies
3. Tests must pass from the project root.
4. QA must run `acceptance-check.sh` before signing off.

## Orchestrator Rules

- Assign work through kanban.
- Review every deliverable.
- Accept a task only when evidence is present.
- Reject incomplete work with specific improved instructions.
- Require QA PASS before writing final PASS in `REPORT.md`.
