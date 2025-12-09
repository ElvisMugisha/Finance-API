# Quick Setup Checklist

Use this checklist to quickly set up your GitHub repository for the automated deployment pipeline.

---

## 🔐 Part 1: Docker Hub Token (Do This First!)

### Get Your Docker Hub Token:

1. [ ] Go to https://hub.docker.com/
2. [ ] Log in
3. [ ] Click your username → **Account Settings**
4. [ ] Click **Security** (left sidebar)
5. [ ] Click **New Access Token**
6. [ ] Set description: `GitHub Actions - Finance API`
7. [ ] Set permissions: **Read, Write, Delete**
8. [ ] Click **Generate**
9. [ ] **COPY THE TOKEN** (you can't see it again!)
10. [ ] Save it somewhere safe temporarily

---

## 🛡️ Part 2: GitHub Branch Protection

### For `staging` Branch:

1. [ ] Go to https://github.com/ElvisMugisha/Finance-API/settings/branches
2. [ ] Click **Add branch protection rule**
3. [ ] Branch name pattern: `staging`
4. [ ] ✅ Require a pull request before merging
5. [ ] Set required approvals: `1`
6. [ ] ✅ Require status checks to pass before merging
7. [ ] ✅ Require branches to be up to date
8. [ ] Add status checks: `lint`, `unit-tests`, `docker-validation`
9. [ ] ✅ Require conversation resolution before merging
10. [ ] Click **Create**

### For `prod` Branch:

1. [ ] Click **Add branch protection rule** again
2. [ ] Branch name pattern: `prod`
3. [ ] ✅ Require a pull request before merging
4. [ ] Set required approvals: `2` (or more)
5. [ ] ✅ Dismiss stale pull request approvals when new commits are pushed
6. [ ] ✅ Require status checks to pass before merging
7. [ ] ✅ Require branches to be up to date
8. [ ] Add status checks: `lint`, `unit-tests`, `docker-validation`
9. [ ] ✅ Require conversation resolution before merging
10. [ ] ✅ Restrict who can push to matching branches
11. [ ] ✅ Do not allow bypassing the above settings
12. [ ] ✅ Include administrators
13. [ ] Click **Create**

---

## 🔑 Part 3: GitHub Secrets

### Add Docker Hub Credentials:

1. [ ] Go to https://github.com/ElvisMugisha/Finance-API/settings/secrets/actions
2. [ ] Click **New repository secret**
3. [ ] Name: `DOCKERHUB_USERNAME`
4. [ ] Secret: Your Docker Hub username
5. [ ] Click **Add secret**
6. [ ] Click **New repository secret** again
7. [ ] Name: `DOCKERHUB_TOKEN`
8. [ ] Secret: Paste the token from Part 1
9. [ ] Click **Add secret**
10. [ ] Verify both secrets are listed

---

## ⚙️ Part 4: GitHub Actions Permissions

1. [ ] Go to https://github.com/ElvisMugisha/Finance-API/settings/actions
2. [ ] Scroll to **Workflow permissions**
3. [ ] Select: **Read and write permissions**
4. [ ] ✅ Allow GitHub Actions to create and approve pull requests
5. [ ] Click **Save**

---

## 🌿 Part 5: Create Branches (If Needed)

### Create `staging` branch:
```bash
git checkout -b staging
git push origin staging
```

### Create `prod` branch:
```bash
git checkout -b prod
git push origin prod
```

---

## ✅ Part 6: Verification

### Test Branch Protection:
```bash
# This should fail (good!)
git checkout staging
echo "test" >> test.txt
git add test.txt
git commit -m "test"
git push origin staging
```

Expected: `error: GH006: Protected branch update failed`

### Test Workflow:
1. [ ] Push changes to `dev` branch
2. [ ] Wait for CI to complete
3. [ ] Check if PR to `staging` is auto-created
4. [ ] Merge the PR
5. [ ] Check if DRAFT PR to `prod` is auto-created

---

## 📊 Final Checklist

- [ ] Docker Hub token created and saved
- [ ] `staging` branch protected (1 review required)
- [ ] `prod` branch protected (2+ reviews required)
- [ ] `DOCKERHUB_USERNAME` secret added
- [ ] `DOCKERHUB_TOKEN` secret added
- [ ] GitHub Actions has write permissions
- [ ] `staging` branch exists
- [ ] `prod` branch exists
- [ ] Tested branch protection (push should fail)
- [ ] Tested workflow (PR auto-created)

---

## 🎉 You're Done!

Your automated deployment pipeline is now fully configured!

**Next**: Push your changes to `dev` and watch the magic happen! ✨

---

## 📞 Need Help?

If something doesn't work:
1. Check the detailed guide: `docs/GITHUB_SETUP_GUIDE.md`
2. Review workflow logs in the Actions tab
3. Verify all checkboxes above are completed

---

**Setup Time**: ~15 minutes
**Last Updated**: 2025-12-09
