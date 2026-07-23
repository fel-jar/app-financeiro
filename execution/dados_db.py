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
                      installment_current, installment_total, bill_forecast_date
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
        })
    saldo = saldo_row["total"] if saldo_row and saldo_row["total"] is not None else None
    return transacoes, saldo
