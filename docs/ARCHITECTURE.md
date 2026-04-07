# Architecture Summary

This scaffold follows the hybrid API plus MCP design from FountainPenProject.md:

- Marketplace adapters behind internal contracts
- Yahoo! JAPAN Auctions adapter is implemented in `apps/api/app/adapters/yahoo_auctions.py`
- Fixture adapter remains as explicit fallback for resilience and local testing
- Classification and valuation as separate services/modules
- Proxy/coupon logic as data-driven rules
- Deal scoring with confidence-based bucketing
- Postgres-first data model with Alembic migrations under `alembic/`
