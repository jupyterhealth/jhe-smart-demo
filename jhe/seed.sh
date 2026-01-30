# run me with
#    cat jhe/seed.sh | docker exec -i jhe-smart-demo-jhe-1 bash -
# each time this is run, the JHE db is completely reset
set -eu

export PGHOST=$DB_HOST
export PGPORT=$DB_PORT
export PGUSER=$DB_USER
export PGPASSWORD="$DB_PASSWORD"

set -x
dropdb --if-exists "${DB_NAME}"
createdb "${DB_NAME}"
python3 manage.py migrate
python3 manage.py seed
