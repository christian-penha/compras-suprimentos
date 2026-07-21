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
