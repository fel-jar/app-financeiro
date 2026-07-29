"""Manda um resumo de FECHAMENTO da semana no Telegram, todo domingo depois
do ciclo diário -- diferente do resumo diário, usa SÓ transações
`status='confirmada'` (nunca pendente de e-mail), porque o objetivo aqui é
bater com o extrato oficial ("ratchet" pedido pelo usuário em 2026-07-29),
não estimar em tempo real.

Uso: python execution/telegram_semanal.py
Pensado pra rodar 1x/semana (domingo) via scheduler.py, DEPOIS do sync.py
do dia (lê só do banco local).

Requer no .env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (mesmos do
telegram_diario.py).
"""
import sys
from collections import defaultdict
from datetime import datetime, timedelta

import db
from normalizacao import traduzir_categoria
from telegram_diario import enviar_telegram, fmt_brl

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")


def montar_resumo_semanal() -> str:
    hoje = datetime.now().date()
    inicio_semana = hoje - timedelta(days=hoje.weekday())  # segunda-feira desta semana
    inicio_label = inicio_semana.strftime("%d/%m")
    fim_label = hoje.strftime("%d/%m")

    with db.sessao() as conexao:
        gastos_semana = conexao.execute(
            """SELECT COALESCE(description_custom, description) AS descricao, category, amount
               FROM transacoes
               WHERE status = 'confirmada' AND amount < 0
                 AND substr(date, 1, 10) BETWEEN ? AND ?
               ORDER BY amount ASC""",
            (inicio_semana.isoformat(), hoje.isoformat()),
        ).fetchall()

        ainda_pendente = conexao.execute(
            """SELECT COUNT(*) AS n, COALESCE(SUM(-amount), 0) AS total FROM transacoes
               WHERE status = 'pendente' AND substr(date, 1, 10) BETWEEN ? AND ?""",
            (inicio_semana.isoformat(), hoje.isoformat()),
        ).fetchone()

    linhas = [f"📅 Fechamento da semana — {inicio_label} a {fim_label}", ""]

    if not gastos_semana:
        linhas.append("Nenhum gasto confirmado nessa semana.")
    else:
        por_categoria: dict = defaultdict(float)
        for g in gastos_semana:
            por_categoria[traduzir_categoria(g["category"] or "Outros")] += abs(g["amount"])

        total = sum(por_categoria.values())
        linhas.append(f"💳 Total confirmado na semana: {fmt_brl(total)}")
        for cat, valor in sorted(por_categoria.items(), key=lambda kv: -kv[1]):
            linhas.append(f"   • {cat}: {fmt_brl(valor)}")

    if ainda_pendente["n"]:
        linhas.append("")
        linhas.append(
            f"⚠️ {ainda_pendente['n']} compra(s) dessa semana ainda sem confirmação da Pluggy "
            f"({fmt_brl(ainda_pendente['total'])}) -- não entram nesse fechamento, mas já foram "
            "notificadas quando chegaram."
        )

    linhas.append("")
    linhas.append("Esse fechamento usa só o que já bateu com o banco -- é o número pra conferir contra a fatura/extrato.")

    return "\n".join(linhas)


def main():
    mensagem = montar_resumo_semanal()
    print(mensagem)
    enviar_telegram(mensagem)
    print("\nEnviado ao Telegram.")


if __name__ == "__main__":
    main()
