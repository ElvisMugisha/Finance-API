# Troubleshooting Guide: Auto-Promotion Workflows Not Running

## Issue: PR not being created automatically after CI passes

### Checklist to Debug:

## 1. ✅ Verify Staging Branch Exists

```bash
# Check if staging branch exists locally
git branch -a | grep staging

# If not, create it
git checkout -b staging
git push origin staging

# Go back to dev
git checkout dev
```

## 2. ✅ Check GitHub Actions Permissions

**CRITICAL**: The workflow needs permission to create PRs.

### Steps:
1. Go to: `https://github.com/ElvisMugisha/Finance-API/settings/actions`
2. Scroll to **"Workflow permissions"**
3. Select: **"Read and write permissions"**
4. ✅ Check: **"Allow GitHub Actions to create and approve pull requests"**
5. Click **Save**

**This is the most common reason workflows fail to create PRs!**

## 3. ✅ Check Workflow Run Logs

1. Go to: `https://github.com/ElvisMugisha/Finance-API/actions`
2. Look for workflow run: **"Promote to Staging"**
3. Click on it to see logs
4. Check for errors

### Common Errors:

#### Error: "Resource not accessible by integration"
**Solution**: Enable workflow permissions (see step 2 above)

#### Error: "refusing to allow a GitHub App to create or update workflow"
**Solution**: The workflow file itself is being modified. This is a GitHub security feature.

#### Workflow doesn't appear at all
**Solution**: The `workflow_run` trigger might not be set up correctly.

## 4. ✅ Verify CI Pipeline Name Matches

The `promote-to-staging.yml` listens for a workflow named **"CI Pipeline"**.

Check that `ci-pipeline.yml` has:
```yaml
name: CI Pipeline  # Must match exactly!
```

## 5. ✅ Manual Test

Test if the workflow can create PRs manually:

```bash
# Trigger the workflow manually (if we add workflow_dispatch)
# Or test the gh command locally:

# Install GitHub CLI if not installed
# Windows: winget install GitHub.cli
# Or download from: https://cli.github.com/

# Login
gh auth login

# Test creating a PR manually
gh pr create \
  --base staging \
  --head dev \
  --title "Test PR" \
  --body "Testing auto-promotion" \
  --label "test"
```

## 6. ✅ Check Workflow Trigger

The workflow triggers on:
```yaml
workflow_run:
  workflows: ["CI Pipeline"]
  types:
    - completed
  branches:
    - dev
```

This means:
- It runs AFTER "CI Pipeline" completes
- Only for pushes to `dev` branch
- Regardless of success/failure (we filter for success in the job)

## 7. ✅ Enable Workflow Dispatch for Testing

Add this to `promote-to-staging.yml` to allow manual triggering:

```yaml
on:
  workflow_run:
    workflows: ["CI Pipeline"]
    types:
      - completed
    branches:
      - dev
  workflow_dispatch:  # Add this for manual testing
```

Then you can manually trigger it from GitHub Actions UI.

## 8. ✅ Check Branch Protection

If staging has branch protection, ensure:
- The GitHub Actions bot is allowed to create PRs
- No restrictions preventing PR creation

## Quick Fix Commands:

```bash
# 1. Ensure staging branch exists
git checkout -b staging 2>/dev/null || git checkout staging
git push origin staging
git checkout dev

# 2. Verify workflow file is correct
cat .github/workflows/promote-to-staging.yml

# 3. Check CI pipeline name
grep "^name:" .github/workflows/ci-pipeline.yml

# 4. Push a test commit
git commit --allow-empty -m "test: trigger promotion workflow"
git push origin dev
```

## Expected Behavior:

1. Push to `dev` → Triggers "CI Pipeline"
2. CI Pipeline completes successfully
3. "Promote to Staging" workflow triggers automatically
4. PR is created from `dev` to `staging`
5. You see the PR in GitHub

## Still Not Working?

### Check the Actions tab:
1. Go to Actions tab in GitHub
2. Look for "Promote to Staging" workflow
3. If it's there but failed, click to see error
4. If it's not there at all, check:
   - Workflow permissions (step 2)
   - Workflow file syntax
   - Branch name matches

### Most Likely Issue:
**90% of the time it's the workflow permissions!**

Go to Settings → Actions → Workflow permissions → Enable "Read and write" and "Allow creating PRs"

---

**Last Updated**: 2025-12-09
