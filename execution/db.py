"""Camada de banco de dados (SQLite) do app financeiro.

Substitui o modelo antigo de "chamar a Pluggy toda vez que abre o
dashboard" por: um job de sincronização grava tudo aqui, e tanto o
dashboard quanto o script do Telegram só leem/escrevem neste banco.

Isso é o que permite edição persistente (descrição de compra, valor de
gasto fixo por mês, orçamento por categoria) -- um HTML estático gerado do
zero não tinha como guardar nada.
"""
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("APP_FINANCEIRO_DB", ROOT / "dados" / "app_financeiro.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS transacoes (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    account_type TEXT NOT NULL,
    date TEXT NOT NULL,
    description TEXT,
    description_custom TEXT,
    category TEXT,
    amount REAL NOT NULL,
    type TEXT,
    installment_current INTEGER,
    installment_total INTEGER,
    bill_forecast_date TEXT,
    synced_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_transacoes_date ON transacoes(date);
CREATE INDEX IF NOT EXISTS idx_transacoes_bill ON transacoes(bill_forecast_date);

CREATE TABLE IF NOT EXISTS contas (
    account_id TEXT PRIMARY KEY,
    account_type TEXT NOT NULL,
    account_name TEXT,
    balance REAL,
    synced_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gastos_fixos (
    mes TEXT NOT NULL,
    nome TEXT NOT NULL,
    forma TEXT NOT NULL,
    valor REAL NOT NULL,
    PRIMARY KEY (mes, nome)
);

CREATE TABLE IF NOT EXISTS orcamento_categoria (
    categoria TEXT PRIMARY KEY,
    limite_mensal REAL NOT NULL,
    origem TEXT NOT NULL DEFAULT 'media_historica'
);

CREATE TABLE IF NOT EXISTS meta (
    chave TEXT PRIMARY KEY,
    valor TEXT
);
"""


def conectar() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conexao = sqlite3.connect(DB_PATH)
    conexao.row_factory = sqlite3.Row
    conexao.execute("PRAGMA foreign_keys = ON")
    return conexao


def inicializar():
    with conectar() as conexao:
        conexao.executescript(SCHEMA)


@contextmanager
def sessao():
    conexao = conectar()
    try:
        yield conexao
        conexao.commit()
    finally:
        conexao.close()


if __name__ == "__main__":
    inicializar()
    print(f"Banco inicializado em {DB_PATH}")
