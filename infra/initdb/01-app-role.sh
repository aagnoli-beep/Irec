#!/bin/bash
# Ruolo applicativo NON privilegiato per il compose di sviluppo: la RLS
# non si applica ai superuser, quindi il servizio non deve connettersi
# come utente di bootstrap. Le migrazioni girano invece con l'utente
# admin (proprietario delle tabelle).
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
    CREATE ROLE irec_app LOGIN PASSWORD '${IREC_APP_DB_PASSWORD:-irec_app_dev_only}';
    GRANT USAGE ON SCHEMA public TO irec_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO irec_app;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO irec_app;
SQL
