# Git Workflow

Welcome! 👋  
This guide helps you contribute to the project — whether you're fixing a typo or adding a new feature.

## 🛠️ Before You Code: Should You Open an Issue?

- **For small changes** (typos, docs, minor fixes):  
  → You can skip the issue and go straight to a pull request.

- **For new features, big changes, or anything unclear**:  
  → **Please open an issue first** on GitHub.  
  → This avoids duplicate work and lets others help or give feedback.

> 💡 Tip: If you’re unsure, just open an issue! We’d rather discuss early than waste your time.

## 🌿 Branching Strategy

- The **main branch** (`main`) is always working and deployable.
- Create a **short-lived feature branch** from `main` for your work.
- **Branch names** follow Conventional Commits style:
  - `feat/user-profile`
  - `fix/layer-loading`
  - `docs/update-readme`
- **Don’t use long-running branches** like `develop` — we keep it simple!

## ✍️ Commit Messages

Use the [Conventional Commits](https://www.conventionalcommits.org/) format:

### Common types:

- `feat:` — new feature
- `fix:` — bug fix
- `docs:` — documentation only
- `chore:` — maintenance (deps, config, etc.)
- `refactor:` — code cleanup, no behavior change

✅ Good: `feat(auth): add Keycloak login`  
❌ Avoid: `fixed stuff`, `update file`, `try again`

> Why? These messages help us auto-generate changelogs and decide version bumps!

## 🔁 Pull Requests (PRs)

- Target your PR to `main`.
- Link to an issue if one exists (e.g., “Closes #42”).
- Keep PRs small and focused — easier to review!
- CI must pass, and a maintainer will approve before merging.

---

## 🚀 Quick Example for First-Timers

1. **Open an issue** (optional for small fixes):  
   → Go to [Issues](../../issues) → “New issue” → describe your idea.

2. **Clone & create your branch**:

   ```bash
   git clone https://github.com/your-org/tosca-web-api.git
   cd tosca-web-api
   git checkout -b feat/add-language-toggle
   ```

3. **Make changes, commit, push**:

   ```bash
   git add .
   git commit -m "feat(ui): add language toggle in header"
   git push origin feat/add-language-toggle
   ```

4. **Open a PR on GitHub** → done! 🎉

We’re happy to help — don’t hesitate to ask!
