# Decisões de arquitetura

- **Django 6.0 em vez de 5.x**: o ambiente tem Python 3.14, que o Django 5.2 LTS não suporta (vai até 3.13). Django 6.0.7 instalado.
- **Código em `webapp/` separado dos documentos**: a raiz `Compras_E_Suprimentos/` guarda especificação/cronograma; o repositório git é `webapp/`.
- **Papéis via model `PapelUsuario` (FK para `Usuario`)** em vez de campo único: um usuário acumula papéis, e ADMINISTRADOR exige `modulo` (constraint no banco). `UniqueConstraint` com `nulls_distinct=False` impede papel duplicado mesmo com módulo nulo.
- **Banco dedicado `compras_suprimentos` com usuário próprio `compras_app`**: a aplicação não usa o superusuário `postgres`.
- **`django-environ` com `.env`**: credenciais fora do código; `.env.example` versionado.
