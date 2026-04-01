import os
from logging.config import fileConfig
from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

user = os.environ["MYSQL_USER"]
password = os.environ["MYSQL_PASSWORD"]
host = os.environ["MYSQL_HOST"]
port = os.environ.get("MYSQL_PORT", "3306")
database = os.environ["MYSQL_DATABASE"]

url = f"mysql+mysqlconnector://{user}:{password}@{host}:{port}/{database}"
config.set_main_option("sqlalchemy.url", url)


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
