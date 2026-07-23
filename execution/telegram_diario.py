"""Manda uma mensagem diária no Telegram com o resumo financeiro do dia:
gasto de hoje (por categoria, com quanto ainda cabe no orçamento daquela
categoria) + caixa disponível + total gasto no mês.

Uso: python execution/telegram_diario.py
Pensado pra rodar 1x/dia via cron/scheduler, DEPOIS do sync.py (lê só do
banco local, não chama a Pluggy).

Requer no .env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID.
Como conseguir:
  1. Fale com @BotFather no Telegram, /newbot, siga o passo a passo ->
     ele te dá o TELEGRAM_BOT_TOKEN.
  2. Mande qualquer mensagem pro seu bot novo, depois acesse
     https://api.telegram.org/bot<TOKEN>/getUpdates -- o campo
     "chat":{"id": ...} é o TELEGRAM_CHAT_ID.
"""
import os
import sys
from collections import defaultdict
from datetime import datetime

import requests
from dotenv import load_dotenv

import db
from normalizacao import traduzir_categoria

load_dotenv()

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")


def fmt_brl(valor: float) -> str:
    s = f"{valor:,.2f}"
    s = s.replace(",", "_").replace(".", ",").replace("_", ".")
    return f"R$ {s}"


def montar_resumo_diario() -> str:
    hoje = datetime.now().strftime("%Y-%m-%d")
    mes_atual = datetime.now().strftime("%Y-%m")

    with db.sessao() as conexao:
        gastos_hoje = conexao.execute(
            """SELECT COALESCE(description_custom, description) AS descricao, category, amount
               FROM transacoes WHERE date LIKE ? AND amount < 0
               ORDER BY amount ASC""",
            (f"{hoje}%",),
        ).fetchall()

        gasto_mes_por_categoria = {
            row["category"]: row["total"]
            for row in conexao.execute(
                """SELECT category, SUM(-amount) AS total FROM transacoes
                   WHERE date LIKE ? AND amount < 0 GROUP BY category""",
                (f"{mes_atual}%",),
            )
        }

        orcamentos = {
            row["categoria"]: row["limite_mensal"]
            for row in conexao.execute("SELECT categoria, limite_mensal FROM orcamento_categoria")
        }

        total_gasto_mes = conexao.execute(
            "SELECT COALESCE(SUM(-amount), 0) AS total FROM transacoes WHERE date LIKE ? AND amount < 0",
            (f"{mes_atual}%",),
        ).fetchone()["total"]

        caixa = conexao.execute(
            "SELECT COALESCE(SUM(balance), 0) AS total FROM contas WHERE account_type = 'BANK'"
        ).fetchone()["total"]

    data_label = datetime.now().strftime("%d/%m/%Y")
    linhas = [f"📊 Resumo financeiro — {data_label}", ""]

    if not gastos_hoje:
        linhas.append("Nenhum gasto registrado hoje.")
    else:
        por_categoria_hoje: dict = defaultdict(float)
        for g in gastos_hoje:
            por_categoria_hoje[g["category"] or "Outros"] += abs(g["amount"])

        linhas.append(f"💳 Gasto de hoje: {fmt_brl(sum(por_categoria_hoje.values()))}")
        for cat, valor_hoje in por_categoria_hoje.items():
            gasto_mes_cat = gasto_mes_por_categoria.get(cat, 0.0)
            limite = orcamentos.get(cat)
            cat_pt = traduzir_categoria(cat)
            if limite is not None:
                resta = limite - gasto_mes_cat
                linhas.append(
                    f"   • {cat_pt}: {fmt_brl(valor_hoje)} hoje "
                    f"(gasto no mês: {fmt_brl(gasto_mes_cat)} de {fmt_brl(limite)} — "
                    f"{'ainda pode gastar ' + fmt_brl(resta) if resta >= 0 else 'estourou em ' + fmt_brl(-resta)})"
                )
            else:
                linhas.append(f"   • {cat_pt}: {fmt_brl(valor_hoje)} hoje (sem orçamento definido pra essa categoria)")

    linhas.append("")
    linhas.append(f"💰 Caixa disponível: {fmt_brl(caixa)}")
    linhas.append(f"📈 Total gasto este mês: {fmt_brl(total_gasto_mes)}")

    return "\n".join(linhas)


def enviar_telegram(mensagem: str):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise SystemExit("Faltam TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID no .env.")

    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": mensagem},
        timeout=30,
    )
    resp.raise_for_status()


def main():
    mensagem = montar_resumo_diario()
    print(mensagem)
    enviar_telegram(mensagem)
    print("\nEnviado ao Telegram.")


if __name__ == "__main__":
    main()
