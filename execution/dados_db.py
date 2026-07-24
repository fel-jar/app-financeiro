"""Carrega transações/saldo do banco local (populado por sync.py) no
mesmo formato de dict que a API da Pluggy devolve, pra reaproveitar toda
a lógica de agregação/renderização já existente em gerar_dashboard.py
sem duplicar nada. É o que o app Flask (app.py) usa em vez de chamar a
Pluggy ao vivo.
"""
import db


def carregar_transacoes_do_banco() -> tuple[list[dict], float | None]:
    with db.sessao() as conexao:
        linhas = conexao.execute(
            """SELECT id, account_id, account_type, date,
                      COALESCE(description_custom, description) AS description,
                      description AS description_original, category, amount, type,
                      installment_current, installment_total, bill_forecast_date,
                      categoria_grande_custom
               FROM transacoes"""
        ).fetchall()
        saldo_row = conexao.execute(
            "SELECT SUM(balance) AS total FROM contas WHERE account_type = 'BANK'"
        ).fetchone()

    transacoes = []
    for r in linhas:
        meta = None
        if r["account_type"] == "CREDIT":
            meta = {
                "installmentNumber": r["installment_current"],
                "totalInstallments": r["installment_total"],
                "billForecastDate": r["bill_forecast_date"],
            }
        transacoes.append({
            "id": r["id"],
            "accountId": r["account_id"],
            "date": r["date"],
            "description": r["description"],
            "descriptionRaw": r["description_original"],
            "category": r["category"],
            "amount": r["amount"],
            "type": r["type"],
            "creditCardMetadata": meta,
            "balance": None,
            "categoriaGrandeCustom": r["categoria_grande_custom"],
        })
    saldo = saldo_row["total"] if saldo_row and saldo_row["total"] is not None else None
    return transacoes, saldo


def carregar_gastos_fixos_do_banco() -> dict[str, list[dict]]:
    """Retorna {mes: [{nome, forma, valor, categoria}, ...]} com os valores
    já editados pelo usuário via /fixos/<mes> -- é a fonte de verdade que o
    painel mês a mês (e o link "editar") deve usar, em vez da lista estática."""
    with db.sessao() as conexao:
        linhas = conexao.execute(
            "SELECT mes, nome, forma, valor, categoria, transacao_id_origem FROM gastos_fixos ORDER BY mes, forma, nome"
        ).fetchall()

    por_mes: dict[str, list[dict]] = {}
    for r in linhas:
        por_mes.setdefault(r["mes"], []).append({
            "nome": r["nome"], "forma": r["forma"], "valor": r["valor"],
            "categoria": r["categoria"] or "Outros",
            "transacao_id_origem": r["transacao_id_origem"],
        })
    return por_mes


def carregar_variaveis_manuais_do_banco() -> dict[str, list[dict]]:
    """Gastos variáveis pix digitados à mão (ex.: uma conta que não passa
    pelo Pluggy) -- editáveis/apagáveis em /variaveis/<mes>, ao contrário
    das transações reais. Mesclados aos itens reais em
    construir_panorama_mensal()."""
    with db.sessao() as conexao:
        linhas = conexao.execute(
            "SELECT id, mes, descricao, forma, valor, categoria FROM gastos_variaveis_manuais ORDER BY mes, descricao"
        ).fetchall()

    por_mes: dict[str, list[dict]] = {}
    for r in linhas:
        por_mes.setdefault(r["mes"], []).append({
            "manual_id": r["id"], "descricao": r["descricao"], "forma": r["forma"],
            "valor": r["valor"], "categoria": r["categoria"] or "Outros",
        })
    return por_mes


def carregar_caixa_externo() -> float:
    """Reserva manual (dinheiro/contas fora do Pluggy), editável em
    /caixa-externo -- guardada como meta única (não muda por mês)."""
    with db.sessao() as conexao:
        valor = db.obter_meta(conexao, "caixa_externo")
    return float(valor) if valor else 0.0
