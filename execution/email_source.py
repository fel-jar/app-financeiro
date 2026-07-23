"""Lê a caixa de entrada (IMAP) em busca de notificações de compra do
Bradesco Cartões encaminhadas via MacroDroid (gatilho de notificação do app,
não SMS -- SMS mostrou formato irregular demais pra confiar).

Requer no .env: EMAIL_IMAP_USER, EMAIL_IMAP_APP_PASSWORD.
Opcional: EMAIL_IMAP_SERVER (default imap.gmail.com).

Formato esperado da notificação (texto do corpo do e-mail):
  "Compra de R$ 161,89 APROVADA em OPENROUTER, INC, no Cartão final 4807."
"""
import email
import email.utils
import imaplib
import os
import re
from datetime import datetime, timedelta
from email.header import decode_header
from email.message import Message

from dotenv import load_dotenv

load_dotenv()

PADRAO_COMPRA = re.compile(
    r"Compra de R\$\s*([\d.,]+)\s+APROVADA em (.+), no Cart[ãa]o final (\d{4})",
    re.IGNORECASE,
)


def _decodificar(valor) -> str:
    partes = decode_header(valor)
    resultado = ""
    for texto, cod in partes:
        if isinstance(texto, bytes):
            try:
                resultado += texto.decode(cod or "utf-8", errors="ignore")
            except LookupError:
                resultado += texto.decode("utf-8", errors="ignore")
        else:
            resultado += texto
    return resultado


def _decodificar_payload(payload: bytes, charset: str | None) -> str:
    try:
        return payload.decode(charset or "utf-8", errors="ignore")
    except LookupError:
        return payload.decode("utf-8", errors="ignore")


def _corpo_texto(msg: Message) -> str:
    if msg.is_multipart():
        partes = []
        for parte in msg.walk():
            if parte.get_content_type() == "text/plain":
                payload: bytes = parte.get_payload(decode=True)  # type: ignore[assignment]
                if payload:
                    partes.append(_decodificar_payload(payload, parte.get_content_charset()))
        return "\n".join(partes)
    payload: bytes = msg.get_payload(decode=True)  # type: ignore[assignment]
    if payload:
        return _decodificar_payload(payload, msg.get_content_charset())
    return ""


def _parse_valor(valor_str: str) -> float:
    return float(valor_str.replace(".", "").replace(",", "."))


def buscar_transacoes(dias: int = 31) -> list[dict]:
    """Busca notificações de compra dos últimos `dias` dias (padrão 90).

    Filtrar por data evita varrer a caixa de entrada inteira (pode ter
    milhares de e-mails não relacionados) a cada execução.
    """
    usuario = os.getenv("EMAIL_IMAP_USER")
    senha = os.getenv("EMAIL_IMAP_APP_PASSWORD")
    servidor = os.getenv("EMAIL_IMAP_SERVER", "imap.gmail.com")
    if not usuario or not senha:
        raise SystemExit("Faltam EMAIL_IMAP_USER/EMAIL_IMAP_APP_PASSWORD no .env.")

    conexao = imaplib.IMAP4_SSL(servidor)
    conexao.login(usuario, senha)
    conexao.select("INBOX")

    desde = (datetime.now() - timedelta(days=dias)).strftime("%d-%b-%Y")
    _, dados = conexao.search(None, "SINCE", desde)
    ids = dados[0].split()

    transacoes = []
    for msg_id in ids:
        _, msg_dados = conexao.fetch(msg_id, "(RFC822)")
        corpo_bruto: bytes = msg_dados[0][1]  # type: ignore[index,assignment]
        msg = email.message_from_bytes(corpo_bruto)
        assunto = _decodificar(msg.get("Subject", ""))
        corpo = _corpo_texto(msg) + "\n" + assunto

        m = PADRAO_COMPRA.search(corpo)
        if not m:
            continue

        valor_str, estabelecimento, final_cartao = m.groups()
        valor = _parse_valor(valor_str)
        data_header = msg.get("Date") or email.utils.formatdate()
        data_recebimento = email.utils.parsedate_to_datetime(data_header)

        transacoes.append({
            "id": f"email-{msg_id.decode()}",
            "description": estabelecimento.strip(),
            "descriptionRaw": estabelecimento.strip(),
            "currencyCode": "BRL",
            "amount": -valor,
            "date": data_recebimento.isoformat(),
            "balance": None,
            "category": estabelecimento.strip(),
            "accountId": f"cartao-final-{final_cartao}",
            "status": "POSTED",
            "type": "DEBIT",
        })

    conexao.close()
    conexao.logout()
    return transacoes


if __name__ == "__main__":
    for t in buscar_transacoes():
        print(t)
