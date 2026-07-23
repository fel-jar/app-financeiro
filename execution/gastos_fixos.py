"""Lista de gastos fixos mensais, mantida manualmente pelo usuário.

Atualizar aqui sempre que um valor fixo mudar (reajuste, novo contrato,
etc.). Itens com faixa de valor (ex.: luz, combustível, mercado) usam
`valor_min`/`valor_max` -- o planejamento usa o `valor_max` por segurança
(cenário mais conservador pra organizar caixa).

Campo `forma` ("pix" ou "cartao") importa pro cálculo de sobra estimada:
itens no cartão já estão dentro da fatura atual (puxada da Pluggy), então
não entram de novo na conta -- só os fixos pagos via Pix são somados por
fora, porque não aparecem na fatura do cartão.
"""

GASTOS_FIXOS = [
    {"nome": "Vivo", "valor": 44.00 + 49.00, "forma": "cartao"},
    {"nome": "Psicóloga", "valor": 720.00, "forma": "pix"},
    {"nome": "Faculdade", "valor": 829.19, "forma": "cartao"},
    {"nome": "Escolinha do Guel", "valor": 1917.14, "forma": "cartao"},
    {"nome": "Tim", "valor": 56.80, "forma": "cartao"},
    {"nome": "Financiamento carro", "valor": 2760.56, "forma": "pix"},
    {"nome": "Internet", "valor": 99.90, "forma": "pix"},
    {"nome": "Condomínio", "valor": 1162.63, "forma": "pix"},
    {"nome": "Luz", "valor_min": 600.00, "valor_max": 900.00, "forma": "pix"},
    {"nome": "Combustível", "valor_min": 400.00, "valor_max": 500.00, "forma": "cartao"},
    {"nome": "Mercado", "valor_min": 1600.00, "valor_max": 1800.00, "forma": "cartao"},
    {"nome": "YouTube Premium", "valor": 16.90 + 16.90, "forma": "cartao"},
    {"nome": "Spotify", "valor": 12.90, "forma": "cartao"},
    {"nome": "Smiles", "valor": 46.00, "forma": "cartao"},
    {"nome": "PNR", "valor": 334.11, "forma": "pix"},
    {"nome": "IR", "valor": 203.50, "forma": "pix"},
]


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
