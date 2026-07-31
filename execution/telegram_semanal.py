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

import dados_db
import db
from categorias_grandes import grande_categoria
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
            """SELECT date, COALESCE(description_custom, description) AS descricao,
                      description_custom, category, categoria_grande_custom, amount, origem
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
        # Agregado pela GRANDE categoria (Mercado, Casa, Lazer...), não pela
        # categoria fina da Pluggy -- é a granularidade em que o orçamento
        # (orcamento_grande) é definido/comparado em todo o resto do app
        # (painel, notificação de compra pendente). Ver directives/agente_telegram.md.
        orcamentos = dados_db.carregar_orcamento_por_grande()
        por_categoria: dict = defaultdict(float)
        for g in gastos_semana:
            cat = g["categoria_grande_custom"] or grande_categoria(traduzir_categoria(g["category"] or "Outros"))
            por_categoria[cat] += abs(g["amount"])

        total = sum(por_categoria.values())
        linhas.append(f"💳 Total confirmado na semana: {fmt_brl(total)}")
        for cat, valor in sorted(por_categoria.items(), key=lambda kv: -kv[1]):
            limite = orcamentos.get(cat)
            if limite:
                linhas.append(f"   • {cat}: {fmt_brl(valor)} (orçamento mensal: {fmt_brl(limite)})")
            else:
                linhas.append(f"   • {cat}: {fmt_brl(valor)} (sem orçamento definido)")

        # "Supervisionada" = teve alguma chance de revisão humana: veio pelo
        # e-mail (origem='email' -- só cobre cartão Bradesco, sync.py marca
        # isso na reconciliação) OU foi editada manualmente (description_custom/
        # categoria_grande_custom). O resto -- confirmada direto pela Pluggy,
        # de outra conta/cartão que o e-mail não cobre -- nunca passou por
        # ninguém, fica só com a categoria automática. Ver directives/agente_telegram.md.
        nao_supervisionadas = [
            g for g in gastos_semana
            if g["origem"] != "email" and not g["description_custom"] and not g["categoria_grande_custom"]
        ]
        if nao_supervisionadas:
            total_nao_supervisionado = sum(abs(g["amount"]) for g in nao_supervisionadas)
            linhas.append("")
            linhas.append(
                f"👀 {len(nao_supervisionadas)} compra(s) ainda não supervisionada(s) nessa semana "
                f"({fmt_brl(total_nao_supervisionado)}) -- não vieram do e-mail (só cobre Bradesco) "
                "nem foram editadas no dashboard, estão com a categoria automática da Pluggy:"
            )
            for g in nao_supervisionadas[:20]:
                data_label = g["date"][:10][8:10] + "/" + g["date"][:10][5:7]
                cat_pt = traduzir_categoria(g["category"] or "Outros")
                linhas.append(f"   • {data_label} {g['descricao']} — {cat_pt} — {fmt_brl(abs(g['amount']))}")
            if len(nao_supervisionadas) > 20:
                linhas.append(f"   ... e mais {len(nao_supervisionadas) - 20}.")

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
