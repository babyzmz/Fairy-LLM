#!/bin/sh
set -eu

psql \
  --set=ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=owner_role="$POSTGRES_USER" \
  --set=database_name="$POSTGRES_DB" \
  --set=app_password="$FAIRY_APP_POSTGRES_PASSWORD" \
  --set=worker_password="$FAIRY_WORKER_POSTGRES_PASSWORD" \
  --set=execution_password="$FAIRY_EXECUTION_POSTGRES_PASSWORD" <<'SQL'
SELECT format(
  'CREATE ROLE fairy_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS PASSWORD %L',
  :'app_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fairy_app')
\gexec

SELECT format(
  'CREATE ROLE fairy_worker LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT BYPASSRLS PASSWORD %L',
  :'worker_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fairy_worker')
\gexec

SELECT format(
  'CREATE ROLE fairy_execution LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT BYPASSRLS PASSWORD %L',
  :'execution_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fairy_execution')
\gexec

ALTER ROLE fairy_app WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS PASSWORD :'app_password';
ALTER ROLE fairy_worker WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT BYPASSRLS PASSWORD :'worker_password';
ALTER ROLE fairy_execution WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT BYPASSRLS PASSWORD :'execution_password';

GRANT CONNECT ON DATABASE :"database_name" TO fairy_app, fairy_worker, fairy_execution;
GRANT USAGE ON SCHEMA public TO fairy_app, fairy_worker, fairy_execution;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO fairy_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO fairy_app;

ALTER DEFAULT PRIVILEGES FOR ROLE :"owner_role" IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO fairy_app;
ALTER DEFAULT PRIVILEGES FOR ROLE :"owner_role" IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO fairy_app;

SELECT 'REVOKE ALL ON TABLE public.alembic_version FROM fairy_app, fairy_worker'
WHERE to_regclass('public.alembic_version') IS NOT NULL
\gexec

SELECT 'REVOKE ALL ON TABLE public.alembic_version FROM fairy_execution'
WHERE to_regclass('public.alembic_version') IS NOT NULL
\gexec

SELECT 'GRANT SELECT, UPDATE ON TABLE public.outbox TO fairy_worker'
WHERE to_regclass('public.outbox') IS NOT NULL
\gexec

SELECT 'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.worker_leases TO fairy_worker'
WHERE to_regclass('public.worker_leases') IS NOT NULL
\gexec

SELECT 'GRANT SELECT, UPDATE ON TABLE public.execution_jobs TO fairy_execution'
WHERE to_regclass('public.execution_jobs') IS NOT NULL
\gexec

SELECT 'GRANT SELECT, INSERT, UPDATE ON TABLE public.execution_workers TO fairy_execution'
WHERE to_regclass('public.execution_workers') IS NOT NULL
\gexec
SQL
