from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import create_engine, text

if TYPE_CHECKING:
    from conftest import PostgresTestContext


PRESENTATION_TABLES = (
    "core_file_render_jobs",
    "core_file_presentations",
    "core_derived_assets",
    "core_renderer_packs",
    "core_annotation_documents",
    "core_edit_recipes",
    "core_selection_references",
    "core_asset_sets",
    "core_asset_variants",
)


def test_presentation_state_forces_tenant_row_level_security(
    postgres_integration_context: PostgresTestContext,
) -> None:
    engine = create_engine(
        postgres_integration_context.admin_sync_dsn,
        pool_pre_ping=True,
    )
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
                    "count(p.policyname) AS policy_count "
                    "FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "LEFT JOIN pg_policies p "
                    "ON p.schemaname = n.nspname AND p.tablename = c.relname "
                    "WHERE n.nspname = 'public' AND c.relname = ANY(:tables) "
                    "GROUP BY c.relname, c.relrowsecurity, c.relforcerowsecurity"
                ),
                {"tables": list(PRESENTATION_TABLES)},
            ).mappings()
            state = {row["relname"]: row for row in rows}
    finally:
        engine.dispose()

    assert set(state) == set(PRESENTATION_TABLES)
    for table_name in PRESENTATION_TABLES:
        assert state[table_name]["relrowsecurity"] is True
        assert state[table_name]["relforcerowsecurity"] is True
        assert state[table_name]["policy_count"] >= 1
