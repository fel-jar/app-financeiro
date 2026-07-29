"""Fecha o gap de tempo real do resumo diário: lê as notificações de
compra que o MacroDroid encaminha por e-mail (mesmo parser de
`email_source.py`) e grava cada uma como transação PENDENTE no banco
assim que chega -- antes da Pluggy confirmar (o emissor do cartão leva de
1 a 3 dias pra liquidar e só aí a Pluggy expõe a transação, com a data
retroativa da compra original; ver diretiva de 2026-07-29).

Pensado pra rodar com frequência curta (ex.: a cada 15-20min, via
scheduler.py), diferente de sync.py/telegram_diario.py que rodam 1x/dia.

Fluxo:
  1. Busca e-mails novos, casa com o regex de compra.
  2. Pula quem já está no banco (mesmo `id`, e-mail já processado antes).
  3. Grava com status='pendente', origem='email'.
  4. Manda um Telegram avisando -- o usuário pode responder na hora
     ("essa foi categoria X") que o agente conversacional
     (agente_llm.py/agente_ferramentas.editar_transacao) já sabe editar
     description_custom/categoria_grande_custom de QUALQUER transação pelo
     id, pendente ou não, sem precisar de ferramenta nova.
  5. Quando o sync.py oficial (Pluggy) trouxer a transação real, a
     reconciliação em sync.py casa por valor+data e apaga a pendente,
     herdando a descrição/categoria que o usuário já tiver corrigido.

Requer no .env: os mesmos EMAIL_IMAP_USER/EMAIL_IMAP_APP_PASSWORD de
email_source.py, e TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID.
"""
import sys
from datetime import datetime

import db
import email_source
from telegram_diario import enviar_telegram, fmt_brl

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")


def _gravar_pendentes(conexao, transacoes: list[dict]) -> list[dict]:
    """Insere só as que ainda não existem no banco (nem como pendente já
    processada antes, nem como confirmada -- isso último não deveria
    acontecer pra um id `email-*`, mas o `INSERT OR IGNORE` protege de
    qualquer forma). Devolve as que de fato entraram agora (novas)."""
    novas = []
    agora = datetime.now().isoformat()
    for t in transacoes:
        cursor = conexao.execute(
            """INSERT OR IGNORE INTO transacoes
                 (id, account_id, account_type, date, description, category,
                  amount, type, synced_at, status, origem)
               VALUES (?, ?, 'CREDIT', ?, ?, NULL, ?, ?, ?, 'pendente', 'email')""",
            (
                t["id"], t["accountId"], t["date"], t["description"],
                t["amount"], t.get("type"), agora,
            ),
        )
        if cursor.rowcount:
            novas.append(t)
    return novas


def checar_email_pendente() -> int:
    """Roda um ciclo de checagem. Devolve quantas transações pendentes
    novas entraram (0 se a inbox não tinha nada novo desde a última vez)."""
    transacoes = email_source.buscar_transacoes(dias=2)
    if not transacoes:
        return 0

    with db.sessao() as conexao:
        novas = _gravar_pendentes(conexao, transacoes)

    for t in novas:
        data_label = t["date"][:10]
        try:
            data_label = datetime.fromisoformat(t["date"]).strftime("%d/%m %H:%M")
        except ValueError:
            pass
        mensagem = (
            f"🔔 Compra pendente detectada ({data_label}):\n"
            f"{t['description']} — {fmt_brl(abs(t['amount']))}\n\n"
            "Ainda não confirmada pelo banco (aparece como 'pendente' no resumo até "
            "a Pluggy bater com ela). Se quiser já corrigir nome/categoria, é só "
            "responder aqui normalmente."
        )
        try:
            enviar_telegram(mensagem)
        except Exception as e:
            print(f"Erro ao notificar pendente {t['id']}: {e}")

    return len(novas)


def main():
    db.inicializar()
    quantidade = checar_email_pendente()
    print(f"{quantidade} compra(s) pendente(s) nova(s) detectada(s) por e-mail.")


if __name__ == "__main__":
    main()
