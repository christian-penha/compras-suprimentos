FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Usuário sem privilégios; as pastas de dados ficam em volumes.
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /data/media /data/privado /app/staticfiles \
    && chmod +x /app/docker/entrypoint.sh \
    && SECRET_KEY=build DATABASE_URL=postgres://x:x@localhost/x DEBUG=False \
       python manage.py collectstatic --noinput -i src \
    && chown -R app:app /data /app/staticfiles

USER app
ENV MEDIA_ROOT=/data/media PRIVATE_ROOT=/data/privado
EXPOSE 8000

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "60", "--access-logfile", "-"]
