"""Gera transações sintéticas no mesmo schema da API Pluggy (v2/transactions),
para construir e testar o dashboard antes de ter credenciais reais.
"""
import random
from datetime import date, timedelta

CATEGORIAS_DESPESA = [
    "Moradia", "Alimentação", "Transporte", "Lazer", "Saúde", "Assinaturas",
]
DESCRICOES_DESPESA = {
    "Moradia": ["Aluguel", "Condomínio", "Energia elétrica", "Internet"],
    "Alimentação": ["Supermercado", "Restaurante", "iFood"],
    "Transporte": ["Combustível", "Uber", "Estacionamento"],
    "Lazer": ["Cinema", "Streaming", "Show"],
    "Saúde": ["Farmácia", "Plano de saúde", "Consulta"],
    "Assinaturas": ["Academia", "Software", "Clube"],
}
DESCRICOES_RECEITA = ["Salário", "Freelance", "Reembolso"]


def gerar_transacoes(meses: int = 6, seed: int = 42) -> list[dict]:
    rng = random.Random(seed)
    hoje = date.today()
    inicio = date(hoje.year, hoje.month, 1)
    for _ in range(meses - 1):
        inicio = (inicio.replace(day=1) - timedelta(days=1)).replace(day=1)

    transacoes = []
    saldo = 5000.0
    account_id = "mock-account-001"
    dia = inicio

    while dia <= hoje:
        # salário todo dia 5
        if dia.day == 5:
            valor = round(rng.uniform(4500, 6500), 2)
            saldo += valor
            transacoes.append(_transacao(dia, DESCRICOES_RECEITA[0], valor,
                                          "Receita", account_id, saldo, "CREDIT"))

        # 0-2 despesas aleatórias por dia
        for _ in range(rng.randint(0, 2)):
            categoria = rng.choice(CATEGORIAS_DESPESA)
            desc = rng.choice(DESCRICOES_DESPESA[categoria])
            valor = -round(rng.uniform(15, 400), 2)
            saldo += valor
            transacoes.append(_transacao(dia, desc, valor, categoria,
                                          account_id, saldo, "DEBIT"))

        # freelance ocasional
        if rng.random() < 0.03:
            valor = round(rng.uniform(300, 1500), 2)
            saldo += valor
            transacoes.append(_transacao(dia, DESCRICOES_RECEITA[1], valor,
                                          "Receita", account_id, saldo, "CREDIT"))

        dia += timedelta(days=1)

    return transacoes


def _transacao(dia: date, descricao: str, valor: float, categoria: str,
               account_id: str, saldo: float, tipo: str) -> dict:
    return {
        "id": f"mock-{dia.isoformat()}-{descricao}-{valor}",
        "description": descricao,
        "descriptionRaw": descricao,
        "currencyCode": "BRL",
        "amount": valor,
        "date": dia.isoformat() + "T00:00:00.000Z",
        "balance": round(saldo, 2),
        "category": categoria,
        "accountId": account_id,
        "status": "POSTED",
        "type": tipo,
    }
