# Security Policy

## Reporting a vulnerability

Please do not open public GitHub issues for security vulnerabilities.

Email: [use GitHub's private vulnerability reporting](https://github.com/robsavage619/padres-analytics/security/advisories/new)

## Scope

This repository contains the open-source engine for a Padres analytics X account.
It does not handle user authentication, payments, or personal data. The primary
risk surface is credential exposure (X API keys, database paths).

## What is intentionally not in this repository

- X / Twitter API credentials (use `env.example` as a template; store in `.env`)
- The DuckDB databases (`data/` is gitignored)
- The editorial model, interest weights, and full detector SQL arsenal (`private/` is gitignored)
- Any data files derived from MLB API, Baseball Savant, Retrosheet, or Baseball-Reference

## Automated protections

- `gitleaks` runs on every commit via pre-commit and in CI
- `dependabot` opens weekly PRs for dependency updates
- `pip-audit` runs in CI to flag known CVEs
- `.gitignore` is hardened to block `*.db`, `*.duckdb`, `data/`, `private/`, `*.pem`, `*.key`
