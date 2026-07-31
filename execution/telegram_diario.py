"""Utilitários compartilhados de Telegram (`fmt_brl`, `enviar_telegram`) usados
por `telegram_semanal.py` (fechamento semanal, o único resumo periódico) e
`email_pendente.py` (notificação de compra pendente).

O resumo diário que existia aqui foi removido a pedido do usuário em
2026-07-31 -- ver directives/agente_telegram.md. O fechamento semanal
(`telegram_semanal.py`) e a notificação de compra pendente (que já mostra
gasto no mês x orçamento da categoria, via `email_pendente.obter_resumo_categoria`)
cobrem o que o resumo diário cobria.

Requer no .env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID.
Como conseguir:
  1. Fale com @BotFather no Telegram, /newbot, siga o passo a passo ->
     ele te dá o TELEGRAM_BOT_TOKEN.
  2. Mande qualquer mensagem pro seu bot novo, depois acesse
     https://api.telegram.org/bot<TOKEN>/getUpdates -- o campo
     "chat":{"id": ...} é o TELEGRAM_CHAT_ID.
"""
import os

import requests
from dotenv import load_dotenv

load_dotenv()


def fmt_brl(valor: float) -> str:
    s = f"{valor:,.2f}"
    s = s.replace(",", "_").replace(".", ",").replace("_", ".")
    return f"R$ {s}"


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
