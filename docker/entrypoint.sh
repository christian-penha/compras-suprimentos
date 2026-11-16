#!/bin/sh
set -e

# Aplica as migrations antes de subir (o compose já espera o banco ficar saudável).
echo "Aplicando migrations..."
python manage.py migrate --noinput

# Garante o administrador do aplicativo (papel SuperAdmin): se não houver nenhum, cria DJANGO_ADMIN_USER
# (padrão "admin") com DJANGO_ADMIN_PASSWORD; sem senha, gera uma e mostra no log. Nunca altera usuário existente.
python manage.py criar_admin --garantir

exec "$@"
