"""Lista de gastos fixos mensais, mantida manualmente pelo usuário.

Atualizar aqui sempre que um valor fixo mudar (reajuste, novo contrato,
etc.). Itens com faixa de valor (ex.: luz) usam `valor_min`/`valor_max` --
o planejamento usa o `valor_max` por segurança (cenário mais conservador
pra organizar caixa).

Só itens PIX entram nessa lista estática. Gastos fixos no CARTÃO não são
mais digitados à mão aqui (decisão do usuário em 2026-07-24): o valor
digitado divergia do valor real cobrado e dava pra editar/apagar um
"fixo" que na real é uma cobrança real do Pluggy. Agora o fluxo é: a
cobrança aparece em Variáveis (dado real do Pluggy) e o usuário usa o
botão "→ fixo" (ver /transacao/<id>/tornar-fixo em app.py) pra
classificá-la como fixa SEM perder o vínculo com a transação real --
esse vínculo (`transacao_id_origem` em gastos_fixos) é o que trava valor
e exclusão pra esses itens (ver fixos_mes em app.py). Mercado e
Combustível já tinham saído da lista antes, pelo mesmo motivo (ver
construir_panorama_mensal() em gerar_dashboard.py).
"""

# "categoria" aqui é a GRANDE categoria (categorias_grandes.py), pra
# agrupar visualmente junto com os itens variáveis reais -- não é a
# categoria fina da Pluggy.
GASTOS_FIXOS = [
    {"nome": "Psicóloga", "valor": 720.00, "forma": "pix", "categoria": "Família e Saúde"},
    {"nome": "Financiamento carro", "valor": 2760.56, "forma": "pix", "categoria": "Transporte"},
    {"nome": "Internet", "valor": 99.90, "forma": "pix", "categoria": "Assinaturas"},
    {"nome": "Condomínio", "valor": 1162.63, "forma": "pix", "categoria": "Casa"},
    {"nome": "Luz", "valor_min": 600.00, "valor_max": 900.00, "forma": "pix", "categoria": "Casa"},
    {"nome": "PNR", "valor": 334.11, "forma": "pix", "categoria": "Outros"},
    {"nome": "IR", "valor": 203.50, "forma": "pix", "categoria": "Outros"},
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
