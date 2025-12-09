# GitHub Branch Protection & Secrets Setup Guide

This guide will walk you through setting up branch protection rules and Docker Hub secrets for your Finance API project.

---

## Part 1: Setting Up Branch Protection Rules

### Step 1: Navigate to Repository Settings

1. Go to your GitHub repository: `https://github.com/ElvisMugisha/Finance-API`
2. Click on **Settings** tab (top right, near "Insights")
3. In the left sidebar, scroll down to **Code and automation** section
4. Click on **Branches**

### Step 2: Protect the `staging` Branch

1. Click the **Add branch protection rule** button (or **Add rule**)

2. **Branch name pattern**: Enter `staging`

3. **Configure the following settings**:

   #### ✅ Require a pull request before merging
   - [x] Check this box
   - **Required approvals**: Set to `1`
   - [x] Dismiss stale pull request approvals when new commits are pushed (optional but recommended)

   #### ✅ Require status checks to pass before merging
   - [x] Check this box
   - [x] Require branches to be up to date before merging
   - **Search for and add these status checks**:
     - `lint`
     - `unit-tests`
     - `docker-validation`

   > **Note**: These checks will only appear after you've run the CI pipeline at least once. If they don't appear yet, you can add them later.

   #### ⚠️ Optional but Recommended
   - [x] Require conversation resolution before merging
   - [x] Do not allow bypassing the above settings

4. Click **Create** at the bottom

### Step 3: Protect the `prod` Branch (CRITICAL)

1. Click **Add branch protection rule** again

2. **Branch name pattern**: Enter `prod`

3. **Configure the following settings**:

   #### ✅ Require a pull request before merging
   - [x] Check this box
   - **Required approvals**: Set to `2` (or more for extra safety)
   - [x] Dismiss stale pull request approvals when new commits are pushed
   - [x] Require review from Code Owners (if you have a CODEOWNERS file)

   #### ✅ Require status checks to pass before merging
   - [x] Check this box
   - [x] Require branches to be up to date before merging
   - **Add these status checks**:
     - `lint`
     - `unit-tests`
     - `docker-validation`

   #### ✅ Require conversation resolution before merging
   - [x] Check this box

   #### ✅ Restrict who can push to matching branches (IMPORTANT)
   - [x] Check this box
   - Add specific users/teams who can push (e.g., senior developers, tech leads)
   - Leave empty to allow only PR merges (recommended)

   #### ✅ Do not allow bypassing the above settings
   - [x] Check this box
   - [x] Include administrators (recommended for production)

   #### ⚠️ Additional Production Safety (Optional)
   - [x] Require deployments to succeed before merging (if you have deployment environments set up)
   - [x] Lock branch (prevents all pushes - use only if needed)

4. Click **Create** at the bottom

### Step 4: Verify Protection Rules

1. Go back to **Settings → Branches**
2. You should see both `staging` and `prod` listed under **Branch protection rules**
3. Click on each to verify the settings

---

## Part 2: Setting Up Docker Hub Secrets

### Prerequisites: Get Your Docker Hub Token

Before adding secrets to GitHub, you need to create a Docker Hub access token:

#### A. Create Docker Hub Access Token

1. Go to [Docker Hub](https://hub.docker.com/)
2. Log in to your account
3. Click on your **username** (top right) → **Account Settings**
4. Click on **Security** in the left sidebar
5. Click **New Access Token**
6. Configure the token:
   - **Description**: `GitHub Actions - Finance API`
   - **Access permissions**: Select `Read, Write, Delete` (or `Read & Write` if available)
7. Click **Generate**
8. **IMPORTANT**: Copy the token immediately - you won't be able to see it again!
9. Save it temporarily in a secure location (password manager or text file)

### Step 5: Add Secrets to GitHub

1. Go to your GitHub repository: `https://github.com/ElvisMugisha/Finance-API`
2. Click on **Settings** tab
3. In the left sidebar, expand **Secrets and variables**
4. Click on **Actions**

### Step 6: Add DOCKERHUB_USERNAME Secret

1. Click **New repository secret** button
2. Fill in the form:
   - **Name**: `DOCKERHUB_USERNAME`
   - **Secret**: Your Docker Hub username (e.g., `elvismugisha`)
3. Click **Add secret**

### Step 7: Add DOCKERHUB_TOKEN Secret

1. Click **New repository secret** button again
2. Fill in the form:
   - **Name**: `DOCKERHUB_TOKEN`
   - **Secret**: Paste the access token you copied from Docker Hub
3. Click **Add secret**

### Step 8: Verify Secrets

1. You should now see both secrets listed:
   - `DOCKERHUB_USERNAME`
   - `DOCKERHUB_TOKEN`
2. The values will be hidden (showing only `***`)
3. You can update them anytime by clicking **Update**

---

## Part 3: Testing Your Setup

### Test Branch Protection

1. Try to push directly to `staging` or `prod`:
   ```bash
   git checkout staging
   git push origin staging
   ```

2. You should see an error like:
   ```
   remote: error: GH006: Protected branch update failed
   ```

3. This confirms protection is working! ✅

### Test Docker Hub Secrets

1. Push a commit to the `prod` branch (via PR)
2. The `prod-deploy.yml` workflow should run
3. Check the workflow logs - it should successfully:
   - Log in to Docker Hub
   - Build the image
   - Push to Docker Hub

---

## Quick Reference: Protection Settings Summary

| Setting | Staging | Production |
|---------|---------|------------|
| Required Reviews | 1 | 2+ |
| Status Checks Required | ✅ | ✅ |
| Conversation Resolution | ✅ | ✅ |
| Restrict Push Access | ❌ | ✅ |
| Include Administrators | ❌ | ✅ |
| Bypass Allowed | ❌ | ❌ |

---

## Troubleshooting

### Issue: Status checks not appearing

**Solution**:
- Run the CI pipeline at least once on each branch
- The checks will appear in the dropdown after the first run
- You can add them retroactively

### Issue: Can't push to protected branch

**Solution**:
- This is expected! Use PRs instead
- Create a feature branch and open a PR to the protected branch

### Issue: Docker Hub login fails in workflow

**Solution**:
- Verify secrets are named exactly: `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN`
- Check that the Docker Hub token hasn't expired
- Ensure the token has write permissions

### Issue: "Resource not accessible by integration" error

**Solution**:
- Go to Settings → Actions → General
- Under "Workflow permissions", select:
  - ✅ Read and write permissions
  - ✅ Allow GitHub Actions to create and approve pull requests

---

## Security Best Practices

1. **Never commit secrets** to your repository
2. **Rotate Docker Hub tokens** every 90 days
3. **Use separate tokens** for different projects
4. **Limit token permissions** to only what's needed
5. **Monitor workflow runs** for suspicious activity
6. **Enable 2FA** on both GitHub and Docker Hub accounts

---

## Next Steps After Setup

1. ✅ Create `staging` and `prod` branches if they don't exist
2. ✅ Push your current code to `dev`
3. ✅ Wait for CI to pass
4. ✅ Verify auto-PR to `staging` is created
5. ✅ Test the full deployment pipeline

---

**Need Help?**
- GitHub Branch Protection: https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches
- GitHub Secrets: https://docs.github.com/en/actions/security-guides/encrypted-secrets
- Docker Hub Tokens: https://docs.docker.com/docker-hub/access-tokens/

---

**Last Updated**: 2025-12-09
