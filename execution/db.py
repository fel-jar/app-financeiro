"""Camada de banco de dados (SQLite) do app financeiro.

Substitui o modelo antigo de "chamar a Pluggy toda vez que abre o
dashboard" por: um job de sincronização grava tudo aqui, e tanto o
dashboard quanto o script do Telegram só leem/escrevem neste banco.

Isso é o que permite edição persistente (descrição de compra, valor de
gasto fixo por mês, orçamento por categoria) -- um HTML estático gerado do
zero não tinha como guardar nada.
"""
import json
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
    categoria_grande_custom TEXT,
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
    transacao_id_origem TEXT,
    PRIMARY KEY (mes, nome)
);

CREATE TABLE IF NOT EXISTS gastos_variaveis_manuais (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mes TEXT NOT NULL,
    descricao TEXT NOT NULL,
    forma TEXT NOT NULL DEFAULT 'pix',
    valor REAL NOT NULL,
    categoria TEXT
);

CREATE INDEX IF NOT EXISTS idx_variaveis_manuais_mes ON gastos_variaveis_manuais(mes);

CREATE TABLE IF NOT EXISTS orcamento_categoria (
    categoria TEXT PRIMARY KEY,
    limite_mensal REAL NOT NULL,
    origem TEXT NOT NULL DEFAULT 'media_historica'
);

-- Teto mensal por GRANDE categoria -- é o que o usuário edita em
-- /orcamento desde 2026-07-26. A tabela orcamento_categoria (categoria
-- fina da Pluggy, ~50 linhas) continua existindo e sendo alimentada pela
-- sincronização, mas só como fonte da SUGESTÃO inicial: editar 50 tetos
-- à mão pra depois vê-los somados em 9 grupos no painel era trabalho sem
-- retorno (ver diretiva, 2026-07-26).
CREATE TABLE IF NOT EXISTS orcamento_grande (
    grande TEXT PRIMARY KEY,
    limite_mensal REAL NOT NULL,
    origem TEXT NOT NULL DEFAULT 'media_historica'
);

CREATE TABLE IF NOT EXISTS meta (
    chave TEXT PRIMARY KEY,
    valor TEXT
);

CREATE TABLE IF NOT EXISTS agente_mensagens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL,
    mensagem TEXT NOT NULL,
    criado_em TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agente_mensagens_chat ON agente_mensagens(chat_id, id);
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
    _adicionar_coluna_se_faltando(conexao, "gastos_fixos", "categoria", "TEXT")
    _adicionar_coluna_se_faltando(conexao, "gastos_fixos", "transacao_id_origem", "TEXT")
    _adicionar_coluna_se_faltando(conexao, "transacoes", "categoria_grande_custom", "TEXT")

    # "Família e Saúde" foi separada em duas grandes categorias em
    # 2026-07-26 (pedido do usuário) -- linhas já semeadas com o nome
    # antigo (ex.: Psicóloga) migram pra "Saúde". Idempotente (não sobra
    # nenhuma linha com o nome antigo depois da primeira vez).
    conexao.execute("UPDATE gastos_fixos SET categoria = 'Saúde' WHERE categoria = 'Família e Saúde'")
    conexao.execute("UPDATE transacoes SET categoria_grande_custom = 'Saúde' WHERE categoria_grande_custom = 'Família e Saúde'")

    _semear_orcamento_grande(conexao)


# Muda sempre que categorias_grandes.GRANDES_CATEGORIAS for reorganizada.
# Só assim as SUGESTÕES de orçamento por grande categoria são recalculadas
# -- sem isso, depois de mover "Compras" pra fora de "Outros" (2026-07-26)
# o teto sugerido continuaria eternamente pendurado no grupo errado.
VERSAO_TAXONOMIA_GRANDES = "2026-07-26-compras-impostos"


def _semear_orcamento_grande(conexao: sqlite3.Connection):
    """Carga do teto por grande categoria: soma os tetos finos que já
    existem em orcamento_categoria. Sem isso, quem já tinha orçamento
    sugerido abriria a tela nova zerada e perderia o ponto de partida.

    Roda em toda inicialização, mas só faz alguma coisa na primeira vez ou
    quando a taxonomia muda -- e, mesmo aí, apaga apenas linhas de origem
    'media_historica'. Teto ajustado à mão pelo usuário nunca é tocado."""
    from categorias_grandes import grande_categoria  # import tardio: evita ciclo db <-> domínio
    from normalizacao import traduzir_categoria

    versao_gravada = obter_meta(conexao, "taxonomia_grandes")
    taxonomia_mudou = versao_gravada != VERSAO_TAXONOMIA_GRANDES
    if taxonomia_mudou:
        conexao.execute("DELETE FROM orcamento_grande WHERE origem = 'media_historica'")
        definir_meta(conexao, "taxonomia_grandes", VERSAO_TAXONOMIA_GRANDES)
    elif conexao.execute("SELECT 1 FROM orcamento_grande LIMIT 1").fetchone():
        return

    totais: dict[str, float] = {}
    for row in conexao.execute("SELECT categoria, limite_mensal FROM orcamento_categoria"):
        grande = grande_categoria(traduzir_categoria(row["categoria"]))
        totais[grande] = totais.get(grande, 0.0) + (row["limite_mensal"] or 0.0)
    for grande, limite in totais.items():
        conexao.execute(
            "INSERT OR IGNORE INTO orcamento_grande (grande, limite_mensal, origem) VALUES (?, ?, 'media_historica')",
            (grande, round(limite, 2)),
        )


def _adicionar_coluna_se_faltando(conexao: sqlite3.Connection, tabela: str, coluna: str, tipo: str):
    colunas = {row["name"] for row in conexao.execute(f"PRAGMA table_info({tabela})")}
    if coluna in colunas:
        return
    try:
        conexao.execute(f"ALTER TABLE {tabela} ADD COLUMN {coluna} {tipo}")
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


def carregar_mensagens_agente(conexao: sqlite3.Connection, chat_id: str, limite: int) -> list[dict]:
    """Últimas `limite` mensagens da conversa com o agente (formato OpenAI:
    role/content/tool_calls/tool_call_id), pra dar contexto ao LLM entre
    reinícios do processo (redeploy, restart do container)."""
    linhas = conexao.execute(
        """SELECT mensagem FROM agente_mensagens WHERE chat_id = ?
           ORDER BY id DESC LIMIT ?""",
        (chat_id, limite),
    ).fetchall()
    return [json.loads(r["mensagem"]) for r in reversed(linhas)]


def gravar_mensagem_agente(conexao: sqlite3.Connection, chat_id: str, mensagem: dict, agora: str):
    conexao.execute(
        "INSERT INTO agente_mensagens (chat_id, mensagem, criado_em) VALUES (?, ?, ?)",
        (chat_id, json.dumps(mensagem, ensure_ascii=False), agora),
    )


if __name__ == "__main__":
    inicializar()
    print(f"Banco inicializado em {DB_PATH}")
