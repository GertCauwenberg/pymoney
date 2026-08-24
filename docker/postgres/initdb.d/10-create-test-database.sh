#!/bin/sh
# Create the database the store-layer tests drop and recreate freely, so they can
# never touch the development data in $POSTGRES_DB.
#
# Runs once, when the data volume is first initialised. To re-run it:
#   docker compose down -v && docker compose up -d
set -eu

test_db="${POSTGRES_TEST_DB:-pymoney_test}"

# CREATE DATABASE cannot take a bind parameter, so the name is interpolated.
# It comes from the environment, so check it is an ordinary identifier first.
case "$test_db" in
    "" | *[!a-zA-Z0-9_]*)
        echo "refusing to use POSTGRES_TEST_DB=$test_db: not an identifier" >&2
        exit 1
        ;;
esac

psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -v ON_ERROR_STOP=1 <<SQL
CREATE DATABASE "$test_db" OWNER "$POSTGRES_USER";
SQL

echo "pymoney: created test database \"$test_db\""
