# Evidence 2: Blocked Pull Request Merge on CI Gate Failure

Repository: o0on/rahbar
Target Branch: main

## The Quality Gate Mechanism
Section 5.4 mandates that the CI pipeline enforces strict non-regression gates against `eval/baseline.json`:
- `recall@5` drops by more than 5 percentage points below baseline
- Any must-abstain question returns `answered_from_context: true`
- Citation validity falls below 95%

## Gate Failure Simulation & Blocked Merge
1. **Triggering Gate Failure**:
   To demonstrate the gate blocking an ungrounded PR, a test branch `test/break-eval-gate` is pushed where `RELEVANCE_FLOOR` is suppressed, causing question `q13` (hostel rules) to answer from ungrounded context instead of abstaining.
2. **CI Execution**:
   The GitHub Actions workflow `ci.yml` runs the `e2e-eval` job on Kind:
   ```bash
   python eval/run_eval.py --ci
   ```
3. **Execution Trace & Exit 1**:
   ```
   [GATE FAILURE] Must-abstain question q13 returned answered_from_context: True
   Gate check failed: 1 constraint(s) violated.
   Process completed with exit code 1.
   ```
4. **GitHub Status Check Block**:
   Because `main` is protected with required status checks, GitHub blocks the PR merge:
   - Status badge: `e2e-eval — Failing after 5m 59s`
   - Merge button: Greyed out / Disabled: *"Required status check 'e2e-eval' failed. Merging can be performed automatically once checks pass."*
5. **Recovery**:
   Restoring correct abstention threshold re-runs the gate:
   ```
   [GATE PASS] recall@5: 1.0 (>= baseline 1.0), citation_validity: 1.0 (>= 0.95), abstention: 1.0 (100%)
   All gate constraints satisfied.
   ```
   The merge button turns green and is unlocked.
