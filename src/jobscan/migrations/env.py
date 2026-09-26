"""Alembic environment.

Runs both from the CLI (`alembic revision --autogenerate`, using the dev
default in alembic.ini) and programmatically from jobscan.db, which builds
its own Config pointing sqlalchemy.url at whatever db_path the user's
config.yaml names — a user's DB is not always ./jobscan.db.
"""
from alembic import context
from sqlalchemy import engine_from_config, pool

from jobscan.models import Base

target_metadata = Base.metadata


def run_migrations_online():
    connectable = engine_from_config(
        context.config.get_section(context.config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # SQLite can't ALTER in place; batch mode recreates the table
        )
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
