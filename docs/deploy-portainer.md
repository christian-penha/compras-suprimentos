# Deploy no Portainer

A stack (`docker-compose.yml`) sobe dois containers:

- **db**: PostgreSQL 15, com os dados no volume `pgdata`.
- **web**: o app Django rodando no gunicorn, com estáticos servidos pelo WhiteNoise.

Volumes do app:

| Volume    | Conteúdo                                  |
|-----------|-------------------------------------------|
| `media`   | imagens de produto                        |
| `privado` | XML de notas fiscais (nunca é público)    |

No start, o container `web` faz duas coisas:

1. Aplica as migrations. Elas já criam o tenant, as filiais, as categorias e as alçadas.
2. Cria o administrador, se `DJANGO_ADMIN_USER` e `DJANGO_ADMIN_PASSWORD` estiverem preenchidos.

## 1. Criar a stack

Em **Stacks > Add stack > Repository**:

- Repository URL: `https://github.com/christian-penha/compras-suprimentos`
- Reference: `refs/heads/main`
- Compose path: `docker-compose.yml`

Se o repositório for privado, marque **Authentication** e informe um token do GitHub com leitura.

## 2. Variáveis de ambiente

| Variável | Obrigatória | Exemplo |
|---|---|---|
| `SECRET_KEY` | sim | gerar: `python -c "import secrets; print(secrets.token_urlsafe(60))"` |
| `POSTGRES_PASSWORD` | sim | senha forte do banco |
| `ALLOWED_HOSTS` | sim | `192.168.0.10,compras.exemplo.com.br` (sem `http://`) |
| `CSRF_TRUSTED_ORIGINS` | sim* | `http://192.168.0.10:8001,https://compras.exemplo.com.br` |
| `APP_PORT` | não | `8001` (porta publicada no host) |
| `HTTPS` | não | `True` **só** com proxy reverso e certificado na frente |
| `DJANGO_ADMIN_USER` / `DJANGO_ADMIN_PASSWORD` / `DJANGO_ADMIN_EMAIL` | 1º deploy | administrador inicial |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GOOGLE_ALLOWED_DOMAINS` | não | login com Google |

\* Sem `CSRF_TRUSTED_ORIGINS` com o endereço exato (esquema, host e porta), o login e os formulários dão erro 403.

## 3. Conferir

- Em **Containers**, o `web` deve ficar *healthy*. O healthcheck chama `/saude/`, que testa o app e o banco.
- Acesse `http://IP:8001`. Entre com o administrador e cadastre usuários e papéis em Configurações.
- Depois do primeiro acesso, você pode apagar `DJANGO_ADMIN_PASSWORD` da stack. O usuário não é recriado nem alterado.

## Atualizar

Depois de enviar o código ao GitHub, abra a stack no Portainer e clique em **Pull and redeploy**. Marque **Re-pull image and redeploy**. As migrations novas rodam sozinhas.

## Backup e migração de dados

Backup do banco:

```sh
docker exec <container-db> pg_dump -U compras_app -Fc compras_suprimentos > backup.dump
```

Para trazer um banco existente (ex.: o dump do `.114`), suba a stack, pare o `web` e rode:

```sh
docker exec -i <container-db> pg_restore -U compras_app -d compras_suprimentos --clean --if-exists < backup.dump
```

Depois, inicie o `web` de novo.

Os arquivos de mídia ficam no volume `media`. Para copiar as imagens antigas, use `docker cp`.

## Observações

- O CSS do Tailwind é compilado localmente e versionado (`static/css/app.css`). O fonte `static/src` não entra na imagem.
- O login com Google exige um domínio público ou `localhost` como URI de redirecionamento. Por IP de rede, use usuário e senha.
