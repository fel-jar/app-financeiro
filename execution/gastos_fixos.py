"""Lista de gastos fixos mensais, mantida manualmente pelo usuário.

Atualizar aqui sempre que um valor fixo mudar (reajuste, novo contrato,
etc.). Itens com faixa de valor (ex.: luz) usam `valor_min`/`valor_max` --
o planejamento usa o `valor_max` por segurança (cenário mais conservador
pra organizar caixa).

Campo `forma` ("pix" ou "cartao") importa pro cálculo de sobra estimada:
itens no cartão já estão dentro da fatura atual (puxada da Pluggy), então
não entram de novo na conta -- só os fixos pagos via Pix são somados por
fora, porque não aparecem na fatura do cartão.

Mercado e Combustível SAÍRAM dessa lista em 2026-07-24 (decisão do
usuário): viram parte de "Variáveis" no painel mês a mês, com o gasto
real do mês em vez de uma faixa estimada -- ver
construir_panorama_mensal() em gerar_dashboard.py.
"""

# "categoria" aqui é a GRANDE categoria (categorias_grandes.py), pra
# agrupar visualmente junto com os itens variáveis reais -- não é a
# categoria fina da Pluggy.
GASTOS_FIXOS = [
    {"nome": "Vivo", "valor": 44.00 + 49.00, "forma": "cartao", "categoria": "Assinaturas"},
    {"nome": "Psicóloga", "valor": 720.00, "forma": "pix", "categoria": "Família e Saúde"},
    {"nome": "Faculdade", "valor": 829.19, "forma": "cartao", "categoria": "Família e Saúde"},
    {"nome": "Escolinha do Guel", "valor": 1917.14, "forma": "cartao", "categoria": "Família e Saúde"},
    {"nome": "Tim", "valor": 56.80, "forma": "cartao", "categoria": "Assinaturas"},
    {"nome": "Financiamento carro", "valor": 2760.56, "forma": "pix", "categoria": "Transporte"},
    {"nome": "Internet", "valor": 99.90, "forma": "pix", "categoria": "Assinaturas"},
    {"nome": "Condomínio", "valor": 1162.63, "forma": "pix", "categoria": "Casa"},
    {"nome": "Luz", "valor_min": 600.00, "valor_max": 900.00, "forma": "pix", "categoria": "Casa"},
    {"nome": "YouTube Premium", "valor": 16.90 + 16.90, "forma": "cartao", "categoria": "Assinaturas"},
    {"nome": "Spotify", "valor": 12.90, "forma": "cartao", "categoria": "Assinaturas"},
    {"nome": "Smiles", "valor": 46.00, "forma": "cartao", "categoria": "Assinaturas"},
    {"nome": "PNR", "valor": 334.11, "forma": "pix", "categoria": "Outros"},
    {"nome": "IR", "valor": 203.50, "forma": "pix", "categoria": "Outros"},
]


# Regras best-effort pra reconhecer a transação REAL de um item fixo do
# cartão e excluí-la de "Variáveis" (senão duplicaria com o valor
# planejado em Fixas). Nem todo item tem regra confiável -- "Faculdade" e
# os fixos pagos em Pix não apareceram como transação isolada nos dados
# reais testados em 2026-07-24 (provavelmente pagos por boleto/débito
# ainda não registrado nesse ciclo), então ficam sem regra por ora: se
# algum dia aparecerem como transação real, vão duplicar em Variáveis até
# alguém adicionar uma regra aqui. `categoria` casa com a categoria já
# traduzida (normalizacao.traduzir_categoria); `palavra_chave` casa
# (case-insensitive) com a descrição da transação.
EXCLUSAO_VARIAVEIS = {
    "Vivo": {"palavra_chave": "vivo"},
    "Tim": {"palavra_chave": "tim"},
    "Escolinha do Guel": {"categoria": "Escolinha/Creche"},
    "YouTube Premium": {"palavra_chave": "youtube"},
    "Spotify": {"palavra_chave": "spotify"},
    "Smiles": {"palavra_chave": "smiles club"},
}


def eh_transacao_do_fixo(nome_fixo: str, descricao: str, categoria: str) -> bool:
    regra = EXCLUSAO_VARIAVEIS.get(nome_fixo)
    if not regra:
        return False
    if "categoria" in regra and categoria == regra["categoria"]:
        return True
    if "palavra_chave" in regra and regra["palavra_chave"] in (descricao or "").lower():
        return True
    return False


def valor_planejamento(item: dict) -> float:
    """Valor a considerar no planejamento: o valor fixo, ou o teto da faixa."""
    return item.get("valor", item.get("valor_max", 0.0))


def total_fixo_mensal() -> float:
    return sum(valor_planejamento(item) for item in GASTOS_FIXOS)


def total_fixo_pix() -> float:
    """Fixos pagos fora do cartão -- somam por fora da fatura na sobra estimada."""
    return sum(valor_planejamento(item) for item in GASTOS_FIXOS if item.get("forma") == "pix")


def total_fixo_cartao() -> float:
    """Fixos pagos no cartão -- já estão dentro da fatura atual, só informativo."""
    return sum(valor_planejamento(item) for item in GASTOS_FIXOS if item.get("forma") == "cartao")
