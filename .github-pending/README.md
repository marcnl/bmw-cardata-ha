# Pending GitHub Actions workflow

`workflows/validate.yml` runs Home Assistant **hassfest** and **HACS** validation.

It lives here (not under `.github/workflows/`) because pushing a workflow file
needs a Personal Access Token with the `workflow` scope. To enable it:

```bash
git mv .github-pending/workflows/validate.yml .github/workflows/validate.yml
git commit -m "ci: enable hassfest + HACS validation"
git push   # with a token that has the `workflow` scope
```

Or paste the file into the repo via the GitHub web UI (Add file → Create new file
→ `.github/workflows/validate.yml`), which doesn't need the scope.
