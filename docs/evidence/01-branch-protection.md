# Evidence 1: Branch Protection Configuration on GitHub

Repository: o0on/rahbar
Branch: main

Settings configured:
- Require a pull request before merging: Enabled
- Require approvals: 1 approval required
- Dismiss stale pull request approvals when new commits are pushed: False
- Require review from Code Owners: False
- Restrict who can push to matching branches: Enabled (Direct push disabled for all non-admins)

Evidence Capture:
Captured via GitHub API and Repository Settings page showing branch protection active on `main`.
API Verification:
gh api repos/o0on/rahbar/branches/main/protection
