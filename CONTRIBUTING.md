# Contributing

Thanks for your interest in **Teams Avatar Voice Live**! 🎉 Contributions of all
kinds are welcome — bug fixes, new personas, docs, tests, and governance rules.

This is a community project (not an official Microsoft product — see
[DISCLAIMER.md](DISCLAIMER.md)). By contributing, you agree that your
contributions are licensed under the repository's [MIT License](LICENSE).

---

## Ways to contribute

- 🐛 **Report a bug** — open a [Bug report](https://github.com/glejdis/teams-avatar-voice-live/issues/new/choose).
- 🤖 **Request a persona / agent** — use the *Agent request* issue template.
- 🎭 **Add a persona** — the fastest, highest-value contribution (see below).
- 🛡️ **Harden governance** — add a policy, DLP rule, or test under
  [`agentgov/`](agentgov/) / [`governance/`](governance/).
- 📖 **Improve docs** — README, [`docs/`](docs/), or runbooks.

For anything security-related, **do not open a public issue** — follow
[SECURITY.md](SECURITY.md).

---

## Development setup

You need **Python 3.11+**. The core runtime and launcher are Python; the
`hosted-agent/` and `browser-fallback/` apps ship their own `requirements.txt`,
and the `graph_bot` transport ([`bot/`](bot/) submodule) is C#/.NET.

```bash
# 1. Clone (with the graph_bot submodule if you'll work on that transport)
git clone --recurse-submodules https://github.com/glejdis/teams-avatar-voice-live.git
cd teams-avatar-voice-live
# already cloned? pull the submodule with:
#   git submodule update --init --recursive

# 2. Create a virtual environment
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1

# 3. Install with dev extras
pip install -e ".[test,web]"

# 4. Copy the environment template and fill in your values
cp .env.example .env               # Windows: Copy-Item .env.example .env
```

> 💡 You do **not** need the `bot/` submodule for the browser (`browser_webrtc`)
> transport — that path runs fully in the browser via ACS WebRTC and is the
> quickest way to try things locally.

---

## Before you open a pull request

Please run the same checks CI runs:

```bash
# Lint & format (Python)
ruff check .
ruff format --check .

# Unit tests
pytest

# Governance registry validation (must pass — it gates CI)
python governance/validate_registry.py
```

If you touched the C# `graph_bot` bot, also run `dotnet format` in that project.

**Checklist:**

- [ ] `ruff`, `pytest`, and `governance/validate_registry.py` pass locally.
- [ ] Tests added/updated for behaviour changes.
- [ ] Docs updated where relevant (README / `docs/` / docstrings).
- [ ] **No secrets, tokens, tenant IDs, meeting URLs, or PII** in the diff.
- [ ] The PR description explains *what* and *why* (the template will prompt you).

---

## Adding a persona (most welcome contribution 🎭)

Personas are the whole point of the platform: a persona is **one Markdown file**.
"Lisa" (an HR screener) is just the shipped example — the platform is
general-purpose.

1. Copy [`hosted-agent/personas/generic.md`](hosted-agent/personas/generic.md)
   to a new file, e.g. `hosted-agent/personas/support-agent.md`.
2. Edit the persona's role, tone, guardrails, and opening line.
3. Point the app at it (see the *Personas* section of the [README](README.md)).
4. Keep it **safe and appropriate** — no content that impersonates a real person
   without consent, and respect the governance rules.

See [`hosted-agent/personas/README.md`](hosted-agent/personas/README.md) for the
persona format.

---

## Commit & PR conventions

- Keep PRs focused; smaller is easier to review.
- Conventional-commit-style messages are appreciated but not required
  (e.g. `docs(readme): ...`, `feat(governance): ...`, `fix(auth): ...`).
- Reference related issues in the PR body (`Closes #123`).
- Be kind and constructive — see [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

---

## Questions?

Open a [Discussion or issue](https://github.com/glejdis/teams-avatar-voice-live/issues)
and we'll help. Thanks for making this project better! 🙌
