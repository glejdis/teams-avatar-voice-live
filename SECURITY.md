# Security Policy

Thanks for helping keep **Teams Avatar Voice Live** and its users safe. This
project puts a governed AI agent into live Microsoft Teams meetings, so we take
security and privacy seriously.

## Supported versions

This is an actively developed pre-1.0 project. Security fixes are applied to the
latest release on `main` only.

| Version | Supported          |
| ------- | ------------------ |
| `0.1.x` | :white_check_mark: |
| `< 0.1` | :x:                |

## Reporting a vulnerability

**Please do not open a public issue for security vulnerabilities.**

Report privately through GitHub's built-in flow:

1. Go to the [**Security** tab](https://github.com/glejdis/teams-avatar-voice-live/security)
   of this repository.
2. Click **Report a vulnerability** (GitHub Private Vulnerability Reporting).
3. Describe the issue, the impact, and — if possible — reproduction steps or a
   proof of concept.

If you cannot use private reporting, open a regular issue that says only *"I
would like to report a security issue"* (no details) and a maintainer will
follow up with a private channel.

### What to expect

- **Acknowledgement:** within a few days.
- **Assessment & fix:** we will investigate, confirm severity, and work on a
  fix. Timelines depend on complexity and severity.
- **Disclosure:** we prefer **coordinated disclosure** — please give us a
  reasonable window to release a fix before any public write-up. We're happy to
  credit you (or keep you anonymous) in the release notes.

## Scope

Because this project orchestrates several external systems, please pay special
attention to — and report — issues in these areas:

- **Secrets & credentials** — leaked tokens, client secrets, or keys in code,
  logs, container images, or CI.
- **Governance / policy bypass** — ways to defeat the controls in
  [`agentgov/`](agentgov/) and [`governance/`](governance/) (DLP, prompt-injection
  filtering, entitlement/authorization checks, audit logging, data
  classification).
- **Injection** — prompt injection or command/data injection that changes the
  agent's behaviour or exfiltrates data.
- **AuthN / AuthZ** — issues in the Microsoft Graph / Entra sign-in and group
  entitlement paths ([`agentgov/auth/`](agentgov/auth/)).
- **Infrastructure** — insecure defaults in the Bicep/deployment assets under
  [`infra/`](infra/).

## Good-to-know: this is not an official Microsoft product

This is an independent, community project (see [DISCLAIMER.md](DISCLAIMER.md)).
Vulnerabilities in **Microsoft** services (Azure, Microsoft Teams, Microsoft
Graph, Azure AI Foundry, Azure Voice Live) should be reported to Microsoft via
the [Microsoft Security Response Center (MSRC)](https://msrc.microsoft.com/report),
not here. Report to us only issues in *this repository's* code and configuration.

## Handling secrets responsibly

Never include real secrets, tokens, tenant IDs, meeting join URLs, or personal
data in an issue, PR, or reproduction. Redact them first. See
[`.env.example`](.env.example) for the placeholders this project expects.
