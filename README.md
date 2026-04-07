# FountainPenDealFinder

Personal deal-finding system for Japanese fountain-pen marketplaces.

## Monorepo layout

- `apps/api`: FastAPI internal API
- `apps/worker`: scheduled collection/scoring worker scaffold
- `apps/dashboard`: lightweight static dashboard scaffold
- `apps/mcp-browser`: MCP marketplace browser tool scaffold (TypeScript)
- `apps/mcp-pricing`: MCP pricing/deal tool scaffold (TypeScript)
- `packages/*`: shared modules and domain contracts
- `data/*`: fixtures, taxonomy, labels
- `models/*`: model artifacts and training code placeholders
- `infra/*`: local infra files
- `docs/*`: arc- `docur- `docs/*`: arc- `docur- k - `docs/*`: arc- `docur- `docs/*`: arc- `docur- k - `docs/*`: arc- `docur- `docs/*`: arc- `docur- k - `docs/*`: arc- `docur- `docs/*`: arc- `docur- k - `docs/*`: arc- `docur- `docs/*`: arc- `docur- k - `docs/*`: arc- `docur- `docs/*`: arc- `docur- k - `docs/*`: arc- `docur- `docs/*`: arc- `docur- k - `docs/*`: arc- `docur- `docs/*`: arc- `docur- k - `dopen dashboard scaffold:

```bash
python -m http.server 8080 -d apps/dashboard/public
```

## Notes

- Source adapters and models are intentionally scaffolded with interfaces and placeholders.
- Keep all marketplace integrations behind adapters in `packages/source-adapters`.
