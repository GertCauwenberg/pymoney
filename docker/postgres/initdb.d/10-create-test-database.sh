#!/bin/sh
# Create the database the store-layer tests drop and recreate freely, so they can
# never touch the development data in .
#
# Runs once, when the data volume is first initialised. To re-run it:
#   docker compose down -v && docker compose up -d
set -eu

test_db="pymoney_test"

# CREATE DATABASE cannot take a bind parameter, so the name is interpolated.
# It comes from the environment, so check it is an ordinary identifier first.
case "" in
    "" | *[!a-zA-Z0-9_]*)
        echo "refusing to use POSTGRES_TEST_DB=: not an identifier" >&2
        exit 1
        ;;
esac

psql --username "" --dbname "" -v ON_ERROR_STOP=1 <<SQL
CREATE DATABASE "" OWNER "";
SQL

echo "pymoney: created test database \"\""
