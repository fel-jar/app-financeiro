"""Normalização de transações da Pluggy -- compartilhada entre o gerador
de dashboard estático (gerar_dashboard.py) e a sincronização pro banco
(sync.py). Extraído pra módulo próprio pra não duplicar (e desalinhar) a
mesma lógica financeira em dois lugares.
"""

# A Pluggy devolve a categoria em inglês -- só traduzir na hora de EXIBIR.
# Nunca traduzir antes dos filtros que comparam contra
# CATEGORIAS_MOVIMENTACAO_INTERNA (que são em inglês, vindos direto da
# API), senão o match quebra.
CATEGORIAS_PT = {
    "Groceries": "Mercado", "Shopping": "Compras", "Bookstore": "Livraria",
    "Clothing": "Vestuário", "Eating out": "Restaurante", "Pharmacy": "Farmácia",
    "Electronics": "Eletrônicos", "Houseware": "Casa e decoração", "Services": "Serviços",
    "Food delivery": "Delivery de comida", "Digital services": "Serviços digitais",
    "School": "Escola", "Office supplies": "Material de escritório",
    "Tax on financial operations": "IOF", "Gas stations": "Posto de gasolina",
    "Online shopping": "Compras online", "Telecommunications": "Telecomunicações",
    "Parking": "Estacionamento", "Accomodation": "Hospedagem", "Bicycle": "Bicicleta",
    "Healthcare": "Saúde", "Car rental": "Aluguel de carro", "Sports goods": "Artigos esportivos",
    "Optometry": "Óptica", "Insurance": "Seguro", "Donations": "Doações",
    "Online Courses": "Cursos online", "Tolls and in vehicle payment": "Pedágio",
    "Vehicle maintenance": "Manutenção do veículo", "Kids and toys": "Brinquedos e infantil",
    "Leisure": "Lazer", "Bank fees": "Tarifas bancárias", "Automotive": "Automotivo",
    "Gaming": "Jogos", "Airport and airlines": "Aeroporto e passagens",
    "Wellness and fitness": "Bem-estar e academia", "Kindergarten": "Escolinha/Creche",
    "Late payment and overdraft costs": "Juros e multas", "Education": "Educação",
    "Tickets": "Ingressos", "Housing": "Moradia", "Taxi and ride-hailing": "Táxi/App de transporte",
    "Food and drinks": "Alimentação e bebidas", "Pet supplies and vet": "Pet shop e veterinário",
    "Taxes": "Impostos", "Internet": "Internet", "University": "Faculdade",
    "Travel": "Viagem", "Water": "Água", "Electricity": "Energia elétrica",
    "Salary": "Salário", "Credit card fees": "Tarifas de cartão",
}


def traduzir_categoria(categoria: str) -> str:
    return CATEGORIAS_PT.get(categoria, categoria)


CATEGORIAS_MOVIMENTACAO_INTERNA = {
    "Investments", "Fixed income", "Transfer - Bank Slip", "Transfer - PIX",
    "Same person transfer", "Transfers", "Transfer - Cash", "Credit card payment",
}

# PIX recebidos com esse remetente são pagamento de cliente da assessoria
# esportiva (renda extra real) -- confirmado pelo usuário em 2026-07-22.
# Vêm categorizados como "Transfer - PIX" pela Pluggy (indistinguível de
# transferência entre contas próprias só pela categoria), por isso
# precisam ser resgatados antes do filtro de movimentação interna.
FONTE_RENDA_EXTRA = "PERMAX CONSULTORIA"
CATEGORIA_RENDA_EXTRA = "Renda extra"


def normalizar_transacoes_pluggy(cliente, item_id: str) -> tuple[list[dict], float | None]:
    """Busca e normaliza transações de todas as contas do item.

    Contas tipo CREDIT (cartão) vêm com `amount` sempre positivo -- quem
    indica a direção é o campo `type` (DEBIT = compra, CREDIT = pagamento
    da fatura/estorno). Contas tipo BANK já vêm com o sinal correto
    (negativo = saída). Sem essa normalização, compras de cartão apareciam
    como receita gigante no agregado.

    Descarta transações de CATEGORIAS_MOVIMENTACAO_INTERNA (investimento,
    transferência entre contas próprias, pagamento de fatura de cartão):
    são dinheiro mudando de lugar, não gasto nem renda real, e incluir o
    pagamento de fatura junto com as compras do cartão duplicaria o valor.

    O saldo (conta corrente, não cartão -- cartão é dívida) vem do próprio
    objeto de conta (`GET /accounts`), já que as transações da API real não
    trazem `balance` preenchido.
    """
    contas = cliente.list_accounts(item_id)
    contas_banco = [c for c in contas if c["type"] == "BANK"]
    saldo = sum(c["balance"] for c in contas_banco) if contas_banco else None

    transacoes = []
    for conta in contas:
        for t in cliente.list_transactions(conta["id"]):
            categoria = t.get("category") or ""
            if categoria in CATEGORIAS_MOVIMENTACAO_INTERNA:
                descricao = (t.get("description") or "").upper()
                if categoria == "Transfer - PIX" and t.get("type") == "CREDIT" and FONTE_RENDA_EXTRA in descricao:
                    t["category"] = CATEGORIA_RENDA_EXTRA
                else:
                    continue
            if conta["type"] == "CREDIT":
                t["amount"] = -abs(t["amount"]) if t.get("type") == "DEBIT" else abs(t["amount"])
            transacoes.append(t)
    return transacoes, saldo
