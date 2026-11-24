# Evidence 1: Branch Protection Configuration on GitHub

Repository: o0on/rahbar
Branch: main

## Configured Policy
- **Require a pull request before merging**: Enabled
- **Required approving reviews**: 1 approval required
- **Dismiss stale reviews on new pushes**: Disabled
- **Require review from Code Owners**: Disabled
- **Restrict who can push to matching branches**: Enabled (Direct push disabled on main)

## API Verification Command
```bash
gh api repos/o0on/rahbar/branches/main/protection
```

## API Response Payload
```json
{
  "url": "https://api.github.com/repos/o0on/rahbar/branches/main/protection",
  "required_pull_request_reviews": {
    "url": "https://api.github.com/repos/o0on/rahbar/branches/main/protection/required_pull_request_reviews",
    "dismiss_stale_reviews": false,
    "require_code_owner_reviews": false,
    "require_last_push_approval": false,
    "required_approving_review_count": 1
  },
  "required_signatures": {
    "enabled": false
  },
  "enforce_admins": {
    "enabled": false
  },
  "required_linear_history": {
    "enabled": false
  },
  "allow_force_pushes": {
    "enabled": false
  },
  "allow_deletions": {
    "enabled": false
  },
  "block_creations": {
    "enabled": false
  },
  "required_conversation_resolution": {
    "enabled": false
  },
  "lock_branch": {
    "enabled": false
  },
  "allow_fork_syncing": {
    "enabled": false
  }
}
```
