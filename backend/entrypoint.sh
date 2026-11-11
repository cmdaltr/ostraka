#!/bin/sh
# Bring the schema up to date, then hand over to the server. Alembic is the only thing that changes it.
set -e
alembic upgrade head
exec "$@"
