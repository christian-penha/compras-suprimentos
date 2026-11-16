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
2. Garante o administrador do aplicativo: se ainda não houver nenhum SuperAdmin, cria o usuário `admin` (ou o de `DJANGO_ADMIN_USER`) com o papel SuperAdmin, que libera todas as telas e o menu completo. Ele não acessa o `/admin/` do Django. Sem `DJANGO_ADMIN_PASSWORD`, a senha é gerada e aparece uma única vez no log do container (**Containers > web > Logs**, linha `*** Senha gerada ...`).

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
| `DJANGO_ADMIN_USER` / `DJANGO_ADMIN_PASSWORD` / `DJANGO_ADMIN_EMAIL` | não | administrador do app; padrão `admin`, senha gerada no log se vazia |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GOOGLE_ALLOWED_DOMAINS` | não | login com Google |

\* Sem `CSRF_TRUSTED_ORIGINS` com o endereço exato (esquema, host e porta), o login e os formulários dão erro 403.

## 3. Conferir

- Em **Containers**, o `web` deve ficar *healthy*. O healthcheck chama `/saude/`, que testa o app e o banco.
- Acesse `http://IP:8001`. Entre com o administrador e cadastre usuários e papéis em Configurações.
- Depois do primeiro acesso, troque a senha e apague `DJANGO_ADMIN_PASSWORD` da stack. O usuário não é recriado nem alterado.
- Fora do Docker, o mesmo comando cria o administrador: `python manage.py criar_admin --usuario admin --senha <senha>`.

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

## Carga inicial: produtos do Gestio e fotos do Glide

Os produtos vêm do Gestio (`GESTIO_PRODUTO.xlsx`). As fotos vêm do Glide, ligadas a cada código pela planilha de de-para `fotos-glide-x-gestio.xlsx`:

- Na aba **Fotos**, as linhas em amarelo estão com o **Código** em branco: confira a coluna **Sugestão** e preencha o código quando estiver certo.
- Linhas com o código em branco são puladas.

Copie as planilhas para o container e rode, nesta ordem (cada comando sem `--aplicar` mostra só a prévia):

```sh
docker cp GESTIO_PRODUTO.xlsx <container-web>:/tmp/
docker cp fotos-glide-x-gestio.xlsx <container-web>:/tmp/
docker exec <container-web> python manage.py importar_produtos /tmp/GESTIO_PRODUTO.xlsx --aplicar
docker exec <container-web> python manage.py importar_imagens_glide /tmp/fotos-glide-x-gestio.xlsx --aplicar
docker exec <container-web> python manage.py importar_imagens --baixar-links --aplicar
```

O último comando baixa as fotos do Glide para o volume `media`. Assim o sistema deixa de depender do Glide.

## Dados de teste para treinamento

Rode depois de carregar os produtos e os saldos e de cadastrar os usuários. Cada requisitante precisa de setor com líder e de almoxarifado.

```sh
docker exec <container-web> python manage.py criar_dados_teste
docker exec <container-web> python manage.py criar_dados_teste --aplicar
```

O primeiro comando mostra a prévia; o segundo grava. O comando faz duas coisas:

- **Orçamentos fictícios**: cada centro de custo operacional ainda sem orçamento recebe um valor mensal de janeiro a dezembro, com fevereiro 50% maior e julho 50% menor.
- **Pedidos**: 30 pedidos, com a descrição começando por `[TESTE]`. Eles passam pelo fluxo real (envio, aprovação do líder, separação de estoque e compra) e ficam nas etapas: aguardando aprovação, aprovado, aguardando compra, recusado e cancelado.

Opções:

- `--pedidos N`: muda a quantidade de pedidos.
- `--usuarios login1,login2`: escolhe os requisitantes.
- `--sem-orcamentos` ou `--sem-pedidos`: faz só uma das partes.

**Atenção:** entregar um pedido de teste dá baixa de verdade no estoque e conta no consumo do orçamento.

Antes de entrar em produção, limpe os dados de teste:

```sh
docker exec <container-web> python manage.py criar_dados_teste --remover --zerar-orcamentos --aplicar
```

- `--remover` apaga só os pedidos `[TESTE]`. Os que já têm cotação ou baixa de estoque são listados para cancelar pela tela.
- `--zerar-orcamentos` volta **todos** os centros para "sem orçamento". Use antes de lançar os valores reais.

## Banco de imagens dos produtos

As fotos ficam no volume `media`. A ligação entre o banco antigo e o novo é pelo **código do produto**, não pelo id. Por isso, os produtos precisam existir no banco novo antes da importação: via dump restaurado ou `importar_produtos`.

1. **No sistema atual**, gere o pacote. Ele inclui as fotos anexadas e também baixa as que hoje são só link do Glide:

   ```sh
   python manage.py exportar_imagens imagens-produtos.zip
   ```

   O comando avisa sobre produtos com foto mas sem código, que não dá para ligar, e sobre links que falharam.

2. **Leve o .zip para o container novo**:

   ```sh
   docker cp imagens-produtos.zip <container-web>:/tmp/
   ```

3. **Confira a prévia** (nada é gravado) e depois aplique:

   ```sh
   docker exec <container-web> python manage.py importar_imagens /tmp/imagens-produtos.zip
   docker exec <container-web> python manage.py importar_imagens /tmp/imagens-produtos.zip --aplicar
   ```

Opções do `importar_imagens`:

- `--substituir`: troca a foto de quem já tem uma.
- `--baixar-links`: copia para o sistema as imagens que ainda forem só link externo.
- Também aceita uma pasta ou um .zip montado à mão, com cada foto nomeada pelo código (ex.: `PAPOFI-001.jpg`).

Só entram imagens JPG, PNG, WEBP ou GIF de até 8 MB, verificadas uma a uma.

## Observações

- O CSS do Tailwind é compilado localmente e versionado (`static/css/app.css`). O fonte `static/src` não entra na imagem.
- O login com Google exige um domínio público ou `localhost` como URI de redirecionamento. Por IP de rede, use usuário e senha.
