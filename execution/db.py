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
    categoria TEXT,
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
        _migrar(conexao)


def _migrar(conexao: sqlite3.Connection):
    """Ajustes de schema em bancos que já existiam antes de uma coluna
    nova ser criada -- `CREATE TABLE IF NOT EXISTS` não adiciona coluna em
    tabela já existente, por isso o ALTER explícito aqui.

    O `try/except` (não só o `PRAGMA table_info` antes) é necessário porque
    múltiplos processos chamam inicializar() ao mesmo tempo no primeiro
    boot (os workers do gunicorn + o scheduler, cada um importando app.py
    de forma independente) -- sem isso, dois processos podem checar "coluna
    não existe" ao mesmo tempo e um deles quebra com "duplicate column
    name" ao tentar o ALTER (visto em produção em 2026-07-24)."""
    colunas = {row["name"] for row in conexao.execute("PRAGMA table_info(gastos_fixos)")}
    if "categoria" not in colunas:
        try:
            conexao.execute("ALTER TABLE gastos_fixos ADD COLUMN categoria TEXT")
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e):
                raise


@contextmanager
def sessao():
    conexao = conectar()
    try:
        yield conexao
        conexao.commit()
    finally:
        conexao.close()


def obter_meta(conexao: sqlite3.Connection, chave: str) -> str | None:
    row = conexao.execute("SELECT valor FROM meta WHERE chave = ?", (chave,)).fetchone()
    return row["valor"] if row else None


def definir_meta(conexao: sqlite3.Connection, chave: str, valor: str):
    conexao.execute(
        """INSERT INTO meta (chave, valor) VALUES (?, ?)
           ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor""",
        (chave, valor),
    )


if __name__ == "__main__":
    inicializar()
    print(f"Banco inicializado em {DB_PATH}")
