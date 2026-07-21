# Decisões de arquitetura

- **Django 6.0 em vez de 5.x**: o ambiente tem Python 3.14, que o Django 5.2 LTS não suporta (vai até 3.13). Django 6.0.7 instalado.
- **Código em `webapp/` separado dos documentos**: a raiz `Compras_E_Suprimentos/` guarda especificação/cronograma; o repositório git é `webapp/`.
- **Papéis via model `PapelUsuario` (FK para `Usuario`)** em vez de campo único: um usuário acumula papéis, e ADMINISTRADOR exige `modulo` (constraint no banco). `UniqueConstraint` com `nulls_distinct=False` impede papel duplicado mesmo com módulo nulo.
- **Banco dedicado `compras_suprimentos` com usuário próprio `compras_app`**: a aplicação não usa o superusuário `postgres`.
- **`django-environ` com `.env`**: credenciais fora do código; `.env.example` versionado.
- **`Categoria` como model (não TextChoices)**: as categorias de produto mudam com a operação (hoje 9, virão outras) e precisam ser gerenciáveis pelo admin sem deploy. Nome novo registrado conforme o contrato.
- **Seed via data migration (`cadastros.0002`)**: filiais, 22 almoxarifados (1 central no CSC), categorias, motivos, centros de custo por filial e as 4 alçadas entram junto com o schema — todo ambiente novo nasce operável e os testes validam o seed.
- **`Alcada.para_valor()`**: resolução da faixa centralizada no model; testes cobrem limites exatos (R$ 300,00 automática / R$ 300,01 diretoria) e faixa sem teto (CEO).
- **`VinculoAprovacao` com ordem única por requisitante ativo** e check de autoaprovação no banco.
- **`Produto.codigo` como chave de reimportação**: o código interno do Glide (ABAC-001…) permite reimportar a planilha sem duplicar (update_or_create). Importador com prévia por padrão e `--aplicar` para gravar; `reset_dimensions()` obrigatório porque o export do Glide tem metadados de dimensão corrompidos (A1:C1).
- **Produtos importados na categoria "A classificar"**: a planilha não traz categoria; reclassificação será feita no admin pela equipe. Saldos da planilha NÃO são importados — entram pela tela de entrada por planilha (fase 3).
- **Tailwind v4 via CLI standalone em `tools/` (gitignorado)**: build com `./tools/tailwindcss.exe -i static/src/app.css -o static/css/app.css --minify` após alterar templates.
- **Entrega = transferência central → almoxarifado destino**: a baixa da entrega credita o saldo lógico da unidade; débito entre almoxarifados só é gerado em transferências fora desse fluxo (unidade → unidade).
- **Entrada por planilha com prévia em sessão**: upload → prévia validada → confirmação; cada confirmação gera Movimentações com `arquivo_origem` preenchido.
- **Design system**: fundo slate-100, cards rounded-3xl brancos, azul-600 como cor primária, badges por status (pedidos/templatetags/ui.py).
