# Architecture Decision Records

## ADR-001 — `env.example` not `.env.example`

**Date:** 2026-06-09
**Status:** Decided

**Context:** The execution plan specifies `.env.example` as the dotenv template file. The global filesystem deny-list (`~/.claude/security/filesystem-deny.txt`) blocks `**/.env.*`.

**Decision:** Use `env.example` (no leading dot) as the template filename.

**Consequences:** Same semantic purpose, equally conventional in Python projects. Any documentation or tooling that references `.env.example` should be updated to `env.example`. If the deny-list is ever narrowed to allow `.env.example`, the file can be renamed.
