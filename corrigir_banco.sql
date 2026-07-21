-- Script idempotente: pode rodar quantas vezes precisar.
-- Executar como superusuário:
--   "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -h localhost -f corrigir_banco.sql

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'compras_app') THEN
        CREATE ROLE compras_app LOGIN PASSWORD 'admin' CREATEDB;
    ELSE
        ALTER ROLE compras_app WITH LOGIN PASSWORD 'admin' CREATEDB;
    END IF;
END
$$;

SELECT 'CREATE DATABASE compras_suprimentos OWNER compras_app'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'compras_suprimentos')
\gexec

ALTER DATABASE compras_suprimentos OWNER TO compras_app;

-- Conferência final: deve listar o usuário e o banco
SELECT rolname AS usuario_criado FROM pg_roles WHERE rolname = 'compras_app';
SELECT datname AS banco_criado, pg_get_userbyid(datdba) AS dono
FROM pg_database WHERE datname = 'compras_suprimentos';
