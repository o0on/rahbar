# Evidence 2: Blocked Pull Request Merge on CI Gate Failure

Repository: o0on/rahbar
PR: Demonstrating the evaluation quality gate blocking merge when recall drops or abstention fails.

Scenario:
1. Intentional degradation introduced in PR branch (e.g. relevance floor lowered or golden question abstention broken).
2. GitHub Actions `ci.yml` `e2e-eval` job runs `eval/run_eval.py`.
3. Quality gate fails with non-zero exit code.
4. Merge button on GitHub Pull Request is greyed out and blocked:
   "Required status check 'e2e-eval' failed. Merging is blocked."
5. Fix applied, pipeline turns green, merge is unlocked.
