-- Executar uma única vez como superusuário (postgres), via pgAdmin ou:
--   "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -h localhost -f setup_banco.sql
-- Depois, troque TROCAR_SENHA aqui e no arquivo .env pela senha escolhida.

CREATE USER compras_app WITH PASSWORD 'admin' CREATEDB;
CREATE DATABASE compras_suprimentos OWNER compras_app;
