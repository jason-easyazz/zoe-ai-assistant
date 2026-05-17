# Archived: SQLite Migration Scripts (May 17, 2026)

These scripts were used to migrate Zoe's primary data store from SQLite (aiosqlite) to PostgreSQL (asyncpg). Migration is complete as of Phase 4.

## Files

- `migrate_sqlite_to_postgres.py` - One-shot data migration tool (reads SQLite, writes PostgreSQL)
- `scripts_migrate_mcp_sqlite_to_asyncpg.py` - Code transformation script (rewrote mcp_server.py from aiosqlite to asyncpg)

## Why archived

- Migration complete; PostgreSQL is the sole primary database
- `scripts_temp_migrate_mcp.py` violated naming convention (prohibited `*_temp_*` per .cursorrules)
- No longer needed at runtime

## Reference

See `services/zoe-data/alembic/` for schema management and `services/zoe-data/database.py` for the current DB layer.
