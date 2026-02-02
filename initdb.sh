#!/bin/sh
set -eux

for db in fhir jhe; do
  createdb -U ${POSTGRES_USER} ${db} || echo "$db exists"
done
