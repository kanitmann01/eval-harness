"""In-place schema evolution for databases created by earlier tickets.

The runner is deliberately small and additive where SQLite allows it: a
schema_version table records what has been applied, and every nullable column
the ORM maps but the database lacks is added with ALTER TABLE.

One shape cannot be reached additively. Issue #2 created aggregates with a
primary key over (run_id, candidate, metric) and a NOT NULL value. Issue #4
needs a NULL tier on the overall row and a NULL value for an undefined
proportion, and neither SQLite nor a portable ALTER can relax those
constraints, so that one table is rebuilt and every row is copied across.
No row is ever dropped.
"""

from __future__ import annotations

from sqlalchemy import Column, MetaData, String, Table, inspect, text
from sqlalchemy.engine import Connection, Engine

from evalharness.errors import SchemaMigrationError
from evalharness.storage.models import AggregateRow, Base

SCHEMA_VERSION = 7
"""The schema this build writes; bump it when a new migration is added.

Version 2 adds the nullable runs.budget_override_usd column from issue #3.
Version 3 adds the nullable gates.candidate, comparison, threshold, observed
and n columns from issue #8. Version 4 adds the nullable cells cost-split and
judge-token columns from issue #7. Version 5 adds credentials.disabled from
issue #13. Version 6 adds the nullable cells.provider and
cells.judge_provider_usage columns from issue #17 plus the new provider_costs
table. Version 7 adds the nullable runs.operator column. The nullable columns
with no dedicated step need no hand-written migration: a database recorded
below the current version goes through the generic nullable-column pass, which
adds whatever the ORM maps and the database lacks, then records the current
version.
The issue #7 columns are nullable because a stored cell predates them; the
repository reads NULL as zero, and every new cell writes an explicit value.
disabled is NOT NULL, so it is added by a dedicated step that supplies the
false default an existing row needs. The issue #17 columns are nullable for the
same reason, and provider_costs needs no ALTER at all: `Database.create_all`
creates it on the next open, and its rows are rolled up per run rather than
migrated from data that never existed.
"""

_STAGING_TABLE = "aggregates_rebuild"
"""Temporary name for the rebuilt aggregates table."""


def migrate(engine: Engine) -> int:
    """Bring a database up to SCHEMA_VERSION and return the version reached.

    Safe to call on every start: a database already at the current version is
    left untouched, and a database with no tables yet only receives its
    version marker because create_all builds the current shape afterwards.
    """
    with engine.begin() as connection:
        _ensure_version_table(connection)
        version = _read_version(connection)
        if version >= SCHEMA_VERSION:
            return version
        _rebuild_aggregates(connection)
        _add_credential_disabled(connection)
        _add_missing_columns(connection)
        _write_version(connection, SCHEMA_VERSION)
        return SCHEMA_VERSION


def _ensure_version_table(connection: Connection) -> None:
    """Create the version marker table when the database predates it."""
    connection.exec_driver_sql(
        "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
    )


def _read_version(connection: Connection) -> int:
    """Return the highest applied version, or zero when none is recorded."""
    value = connection.execute(text("SELECT MAX(version) FROM schema_version")).scalar()
    return int(value or 0)


def _write_version(connection: Connection, version: int) -> None:
    """Append one applied version; the marker table stays append-only."""
    connection.execute(
        text("INSERT INTO schema_version (version) VALUES (:version)"),
        {"version": version},
    )


def _rebuild_aggregates(connection: Connection) -> None:
    """Rebuild aggregates when issue #2's primary key and NOT NULL remain."""
    inspector = inspect(connection)
    if "aggregates" not in inspector.get_table_names():
        return
    columns = {
        column["name"]: column
        for column in inspector.get_columns("aggregates")
    }
    if "id" in columns and columns["value"]["nullable"]:
        return
    staging_metadata = MetaData()
    # runs exists only so the copied foreign key resolves while the staging
    # table's DDL is compiled; the real table is already in the database.
    Table(
        "runs",
        staging_metadata,
        Column("run_id", String(36), primary_key=True),
    )
    staging = AggregateRow.__table__.to_metadata(
        staging_metadata, name=_STAGING_TABLE
    )
    staging.create(connection)
    preparer = connection.dialect.identifier_preparer
    copied = ", ".join(
        preparer.quote(column.name)
        for column in staging.columns
        if column.name in columns and column.name != "id"
    )
    connection.exec_driver_sql(
        f"INSERT INTO {_STAGING_TABLE} ({copied}) SELECT {copied} FROM aggregates"
    )
    connection.exec_driver_sql("DROP TABLE aggregates")
    connection.exec_driver_sql(f"ALTER TABLE {_STAGING_TABLE} RENAME TO aggregates")


def _add_credential_disabled(connection: Connection) -> None:
    """Add credentials.disabled to a database that predates issue #13.

    The column is NOT NULL, which the generic pass refuses because it would
    have to invent a value; a server default of false is that value, and it
    matches the ORM default every new row writes.
    """
    inspector = inspect(connection)
    if "credentials" not in inspector.get_table_names():
        return
    present = {column["name"] for column in inspector.get_columns("credentials")}
    if "disabled" in present:
        return
    false_literal = "FALSE" if connection.dialect.name == "postgresql" else "0"
    connection.exec_driver_sql(
        "ALTER TABLE credentials ADD COLUMN disabled BOOLEAN NOT NULL "
        f"DEFAULT {false_literal}"
    )


def _add_missing_columns(connection: Connection) -> None:
    """Add every nullable mapped column the database is missing.

    A missing NOT NULL column cannot be added without inventing a value for
    the rows already stored, so that case is refused with an actionable error
    instead of a silent default.
    """
    inspector = inspect(connection)
    existing_tables = set(inspector.get_table_names())
    preparer = connection.dialect.identifier_preparer
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        present = {column["name"] for column in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in present:
                continue
            if not column.nullable:
                raise SchemaMigrationError(
                    f"{table.name}.{column.name}",
                    "the column is missing from an existing database "
                    "and cannot be added as NOT NULL",
                    "rebuild the database or restore a backup written by "
                    "this harness version",
                )
            column_type = column.type.compile(dialect=connection.dialect)
            connection.exec_driver_sql(
                f"ALTER TABLE {preparer.format_table(table)} "
                f"ADD COLUMN {preparer.quote(column.name)} {column_type}"
            )
