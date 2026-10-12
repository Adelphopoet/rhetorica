#!/bin/sh
# Migrate both stores with their own role/search_path in the existing database.
set -eu
export SQL_HOST="$POSTGRES_SEEDS" SQL_PORT="$DB_PORT" SQL_DATABASE="$DBNAME"
export SQL_PLUGIN="$DB" SQL_PASSWORD="$POSTGRES_PWD"
until nc -z "$SQL_HOST" "$SQL_PORT"; do sleep 2; done
export SQL_USER="$POSTGRES_USER"
temporal-sql-tool setup-schema -v 0.0
temporal-sql-tool update-schema -d /etc/temporal/schema/postgresql/v12/temporal/versioned
export SQL_USER="$VISIBILITY_POSTGRES_USER"
temporal-sql-tool setup-schema -v 0.0
temporal-sql-tool update-schema -d /etc/temporal/schema/postgresql/v12/visibility/versioned
