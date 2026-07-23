"""Sincroniza dados reais da Pluggy pro banco local (SQLite).

Uso: python execution/sync.py
Pensado pra rodar 1x/dia (cron/scheduler) -- é o que substitui as chamadas
"ao vivo" que o gerar_dashboard.py fazia toda vez que era aberto.

Nunca sobrescreve edições do usuário: `description_custom` (em
transacoes) e valores já existentes em `gastos_fixos`/`orcamento_categoria`
são preservados -- só preenche o que ainda não foi definido.
"""
import os
from collections import defaultdict
from datetime import datetime

from dotenv import load_dotenv

from pluggy_client import from_env
from normalizacao import normalizar_transacoes_pluggy
from gastos_fixos import GASTOS_FIXOS, valor_planejamento
import db

load_dotenv()

MESES_SEED_FIXOS = 6  # quantos meses à frente pré-cadastrar os fixos
MESES_MEDIA_ORCAMENTO = 3


def _mes_seguinte(aaaa_mm: str, n: int) -> str:
    ano, mes = map(int, aaaa_mm.split("-"))
    mes += n
    while mes > 12:
        mes -= 12
        ano += 1
    return f"{ano:04d}-{mes:02d}"


def sincronizar_transacoes_e_contas(conexao, cliente, item_id: str) -> list[dict]:
    transacoes, _ = normalizar_transacoes_pluggy(cliente, item_id)
    agora = datetime.now().isoformat()

    contas = cliente.list_accounts(item_id)
    for conta in contas:
        conexao.execute(
            """INSERT INTO contas (account_id, account_type, account_name, balance, synced_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(account_id) DO UPDATE SET
                 account_type=excluded.account_type, account_name=excluded.account_name,
                 balance=excluded.balance, synced_at=excluded.synced_at""",
            (conta["id"], conta["type"], conta.get("name"), conta.get("balance"), agora),
        )

    for t in transacoes:
        meta = t.get("creditCardMetadata") or {}
        conexao.execute(
            """INSERT INTO transacoes
                 (id, account_id, account_type, date, description, category, amount, type,
                  installment_current, installment_total, bill_forecast_date, synced_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 account_id=excluded.account_id, account_type=excluded.account_type,
                 date=excluded.date, description=excluded.description, category=excluded.category,
                 amount=excluded.amount, type=excluded.type,
                 installment_current=excluded.installment_current,
                 installment_total=excluded.installment_total,
                 bill_forecast_date=excluded.bill_forecast_date, synced_at=excluded.synced_at""",
            (
                t["id"], t["accountId"], _tipo_conta(t, contas), t["date"],
                t.get("description") or t.get("descriptionRaw"), t.get("category"),
                t["amount"], t.get("type"),
                meta.get("installmentNumber"), meta.get("totalInstallments"),
                meta.get("billForecastDate"), agora,
            ),
        )
    return transacoes


def _tipo_conta(transacao: dict, contas: list[dict]) -> str:
    for c in contas:
        if c["id"] == transacao["accountId"]:
            return c["type"]
    return "DESCONHECIDO"


def seed_gastos_fixos(conexao):
    """Pré-cadastra os fixos pros próximos meses -- só cria linha se ainda
    não existir (não sobrescreve valor já editado pelo usuário)."""
    mes_atual = datetime.now().strftime("%Y-%m")
    for i in range(MESES_SEED_FIXOS):
        mes = _mes_seguinte(mes_atual, i)
        for item in GASTOS_FIXOS:
            conexao.execute(
                "INSERT OR IGNORE INTO gastos_fixos (mes, nome, forma, valor) VALUES (?, ?, ?, ?)",
                (mes, item["nome"], item.get("forma", "cartao"), valor_planejamento(item)),
            )


def seed_orcamento_categoria(conexao, transacoes: list[dict]):
    """Define um orçamento padrão por categoria = média dos últimos meses
    fechados, só pra quem ainda não tem orçamento definido -- ponto de
    partida editável, não é meta prescrita por mim."""
    mes_atual = datetime.now().strftime("%Y-%m")
    por_categoria_mes = defaultdict(lambda: defaultdict(float))
    for t in transacoes:
        if t["amount"] >= 0:
            continue
        mes = t["date"][:7]
        if mes >= mes_atual:
            continue
        cat = t.get("category") or "Outros"
        por_categoria_mes[cat][mes] += abs(t["amount"])

    for cat, valores_por_mes in por_categoria_mes.items():
        ultimos = sorted(valores_por_mes.keys())[-MESES_MEDIA_ORCAMENTO:]
        if not ultimos:
            continue
        media = sum(valores_por_mes[m] for m in ultimos) / len(ultimos)
        conexao.execute(
            "INSERT OR IGNORE INTO orcamento_categoria (categoria, limite_mensal, origem) VALUES (?, ?, 'media_historica')",
            (cat, round(media, 2)),
        )


def main():
    db.inicializar()
    cliente = from_env()
    item_id = os.getenv("PLUGGY_ITEM_ID")
    if cliente is None or not item_id:
        raise SystemExit("Faltam PLUGGY_CLIENT_ID/SECRET/ITEM_ID no .env.")

    with db.sessao() as conexao:
        transacoes = sincronizar_transacoes_e_contas(conexao, cliente, item_id)
        seed_gastos_fixos(conexao)
        seed_orcamento_categoria(conexao, transacoes)

    print(f"Sincronizado: {len(transacoes)} transações em {db.DB_PATH}")


if __name__ == "__main__":
    main()
