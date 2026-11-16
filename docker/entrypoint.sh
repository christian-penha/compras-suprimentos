#!/bin/sh
set -e

# Aplica as migrations antes de subir (o compose já espera o banco ficar saudável).
echo "Aplicando migrations..."
python manage.py migrate --noinput

# Cria o administrador inicial se as variáveis forem informadas (não mexe em usuário existente).
if [ -n "$DJANGO_ADMIN_USER" ] && [ -n "$DJANGO_ADMIN_PASSWORD" ]; then
    python manage.py criar_admin
fi

exec "$@"
