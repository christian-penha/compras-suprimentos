from django.db import models


class Filial(models.Model):
    nome = models.CharField(max_length=100, unique=True)
    sigla = models.CharField(max_length=10, unique=True)
    ativo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "filial"
        verbose_name_plural = "filiais"
        ordering = ["nome"]

    def __str__(self):
        return self.nome


class Fornecedor(models.Model):
    razao_social = models.CharField(max_length=200)
    nome_fantasia = models.CharField(max_length=200, blank=True)
    cnpj = models.CharField(max_length=18, blank=True)
    contato = models.CharField(max_length=100, blank=True)
    telefone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    condicoes_pagamento = models.CharField(max_length=200, blank=True)
    ativo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "fornecedor"
        verbose_name_plural = "fornecedores"
        ordering = ["razao_social"]
        constraints = [
            models.UniqueConstraint(
                fields=["cnpj"],
                condition=~models.Q(cnpj=""),
                name="cnpj_unico_quando_informado",
            ),
        ]

    def __str__(self):
        return self.nome_fantasia or self.razao_social


class CentroCusto(models.Model):
    filial = models.ForeignKey(Filial, on_delete=models.PROTECT, related_name="centros_custo")
    nome = models.CharField(max_length=100)
    ativo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "centro de custo"
        verbose_name_plural = "centros de custo"
        ordering = ["filial", "nome"]
        constraints = [
            models.UniqueConstraint(fields=["filial", "nome"], name="centro_unico_por_filial"),
        ]

    def __str__(self):
        return f"{self.filial.sigla} — {self.nome}"


class Subcentro(models.Model):
    centro_custo = models.ForeignKey(
        CentroCusto, on_delete=models.PROTECT, related_name="subcentros"
    )
    nome = models.CharField(max_length=100)
    ativo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "subcentro"
        verbose_name_plural = "subcentros"
        ordering = ["centro_custo", "nome"]
        constraints = [
            models.UniqueConstraint(
                fields=["centro_custo", "nome"], name="subcentro_unico_por_centro"
            ),
        ]

    def __str__(self):
        return f"{self.centro_custo} / {self.nome}"


class MotivoRequisicao(models.Model):
    nome = models.CharField(max_length=100, unique=True)
    ativo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "motivo de requisição"
        verbose_name_plural = "motivos de requisição"
        ordering = ["nome"]

    def __str__(self):
        return self.nome


class Categoria(models.Model):
    nome = models.CharField(max_length=100, unique=True)
    ativo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "categoria"
        verbose_name_plural = "categorias"
        ordering = ["nome"]

    def __str__(self):
        return self.nome


class Grupo(models.TextChoices):
    MATERIAL = "MATERIAL", "Material"
    SERVICO = "SERVICO", "Serviço"


class UnidadeMedida(models.TextChoices):
    UNIDADE = "UN", "Unidade"
    CAIXA = "CX", "Caixa"
    PACOTE = "PC", "Pacote"
    RESMA = "RM", "Resma"
    KILO = "KG", "Quilograma"
    GRAMA = "G", "Grama"
    LITRO = "L", "Litro"
    MILILITRO = "ML", "Mililitro"
    METRO = "M", "Metro"
    ROLO = "RL", "Rolo"
    PAR = "PAR", "Par"
    KIT = "KIT", "Kit"


class Produto(models.Model):
    codigo = models.CharField(
        max_length=30, unique=True, null=True, blank=True,
        help_text="Código interno importado (chave de reimportação)",
    )
    nome = models.CharField(max_length=200)
    especificacao = models.CharField(
        max_length=300, help_text="Cor, gramatura, tamanho, marca de referência etc."
    )
    grupo = models.CharField(max_length=20, choices=Grupo.choices, default=Grupo.MATERIAL)
    categoria = models.ForeignKey(Categoria, on_delete=models.PROTECT, related_name="produtos")
    tipo = models.CharField(max_length=100, blank=True)
    unidade_medida = models.CharField(
        max_length=5, choices=UnidadeMedida.choices, default=UnidadeMedida.UNIDADE
    )
    imagem = models.ImageField(upload_to="produtos/", null=True, blank=True)
    reutilizavel = models.BooleanField(
        default=False, help_text="Exige devolução ao término do projeto"
    )
    fornecedor_padrao = models.ForeignKey(
        Fornecedor,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="produtos_padrao",
    )
    ativo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "produto"
        verbose_name_plural = "produtos"
        ordering = ["nome", "especificacao"]
        constraints = [
            models.UniqueConstraint(
                fields=["nome", "especificacao"], name="produto_unico_por_especificacao"
            ),
        ]
        indexes = [
            models.Index(fields=["categoria", "ativo"]),
        ]

    def __str__(self):
        return f"{self.nome} — {self.especificacao}" if self.especificacao else self.nome


class Feriado(models.Model):
    data = models.DateField(unique=True)
    descricao = models.CharField(max_length=100)

    class Meta:
        verbose_name = "feriado"
        verbose_name_plural = "feriados"
        ordering = ["data"]

    def __str__(self):
        return f"{self.data:%d/%m/%Y} — {self.descricao}"
