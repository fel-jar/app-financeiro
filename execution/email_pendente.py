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
  4. Antes de notificar, procura no PRÓPRIO histórico (gastos_fixos e
     transações confirmadas) se esse estabelecimento já apareceu antes --
     `sugerir_classificacao()` -- pra já chegar com tipo de gasto
     (fixo/variável), categoria e descrição sugeridos, só pedindo
     confirmação em vez de pedir tudo do zero (pedido do usuário em
     2026-07-29, depois de ver a 1ª notificação: "TIM já existe, é a
     mensalidade -- não precisa perguntar de novo").
  5. Manda um Telegram enxuto com a sugestão, e grava a MESMA notificação
     (com a sugestão + o id, em texto que só o agente lê) no histórico de
     conversa (`agente_mensagens`) -- é o que permite o usuário responder
     só "confirma"/"sim" e o agente (agente_llm.py) saber exatamente qual
     transação e quais valores aplicar, mesmo sem re-explicar nada.
  6. Quando o sync.py oficial (Pluggy) trouxer a transação real, a
     reconciliação em sync.py casa por valor+data e apaga a pendente,
     herdando a descrição/categoria que o usuário já tiver corrigido.

Requer no .env: os mesmos EMAIL_IMAP_USER/EMAIL_IMAP_APP_PASSWORD de
email_source.py, e TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID.
"""
import os
import re
import sys
from datetime import datetime

import db
import email_source
from categorias_grandes import grande_categoria
from normalizacao import traduzir_categoria
from telegram_diario import enviar_telegram, fmt_brl

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

CHAT_ID_PADRAO = os.getenv("TELEGRAM_CHAT_ID")


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


def _palavras_chave(descricao: str) -> list[str]:
    """Extrai as palavras alfabéticas mais "específicas" da descrição
    (as mais longas primeiro) pra buscar no histórico -- descarta números
    (telefone, terminal) e palavras curtas demais pra identificar sozinhas
    o estabelecimento (ex.: "DE" em "IFD*CLAUDIA DE ASCENCA"). Palavras
    longas tendem a ser o nome do estabelecimento; palavras curtas tendem
    a ser o código genérico da processadora (ex.: "IFD", "MP") -- ordenar
    por tamanho decrescente naturalmente prioriza a busca mais específica
    sem precisar manter uma lista de códigos conhecidos."""
    brutas = re.findall(r"[A-Za-zÀ-ÿ]+", descricao)
    palavras = {p for p in brutas if len(p) >= 3}
    return sorted(palavras, key=len, reverse=True)[:4]


def sugerir_classificacao(conexao, descricao: str) -> dict:
    """Procura no histórico algo parecido com essa compra pra sugerir tipo
    de gasto (fixo/variável), categoria e descrição -- em vez de deixar o
    usuário classificar do zero uma cobrança que ele já classificou antes
    (ex.: mensalidade de telefone que já virou "fixo" em meses anteriores).

    1º lugar: `gastos_fixos` -- se o nome já está cadastrado como fixo
    (usuário já usou "→ fixo" nele antes), É fixo, sem dúvida.
    2º lugar: `transacoes` confirmadas -- se já apareceu antes mas nunca
    foi marcada fixa, sugere variável com a categoria/descrição de quando
    apareceu mais recentemente (e avisa se parece recorrente, pra sugerir
    marcar como fixo).
    Sem achado: sugestão genérica (variável / Outros / descrição limpa)."""
    palavras = _palavras_chave(descricao)

    for p in palavras:
        row = conexao.execute(
            """SELECT nome, categoria FROM gastos_fixos
               WHERE nome LIKE ? ORDER BY mes DESC LIMIT 1""",
            (f"%{p}%",),
        ).fetchone()
        if row:
            return {
                "encontrado": True,
                "fonte": "gasto_fixo",
                "tipo_gasto": "fixo",
                "categoria": row["categoria"] or "Outros",
                "descricao_sugerida": row["nome"],
            }

    for p in palavras:
        linhas = conexao.execute(
            """SELECT COALESCE(description_custom, description) AS descricao, category,
                      categoria_grande_custom, substr(date, 1, 7) AS mes
               FROM transacoes
               WHERE status = 'confirmada' AND (description LIKE ? OR description_custom LIKE ?)
               ORDER BY date DESC""",
            (f"%{p}%", f"%{p}%"),
        ).fetchall()
        if linhas:
            mais_recente = linhas[0]
            meses_distintos = {l["mes"] for l in linhas}
            categoria = mais_recente["categoria_grande_custom"] or grande_categoria(
                traduzir_categoria(mais_recente["category"] or "Outros")
            )
            resultado = {
                "encontrado": True,
                "fonte": "historico",
                "tipo_gasto": "variável",
                "categoria": categoria,
                "descricao_sugerida": mais_recente["descricao"],
            }
            if len(meses_distintos) >= 2:
                resultado["nota"] = f"apareceu em {len(meses_distintos)} meses diferentes -- pode valer marcar como fixo"
            return resultado

    return {
        "encontrado": False,
        "fonte": None,
        "tipo_gasto": "variável",
        "categoria": "Outros",
        "descricao_sugerida": descricao.strip().title(),
    }


def obter_resumo_categoria(conexao, categoria_grande: str) -> str:
    mes_atual = datetime.now().strftime("%Y-%m")
    
    row_limite = conexao.execute(
        "SELECT limite_mensal FROM orcamento_grande WHERE grande = ?", 
        (categoria_grande,)
    ).fetchone()
    limite = row_limite["limite_mensal"] if row_limite and row_limite["limite_mensal"] else None
    
    linhas = conexao.execute(
        "SELECT category, categoria_grande_custom, amount FROM transacoes WHERE date LIKE ? AND amount < 0",
        (f"{mes_atual}%",)
    ).fetchall()
    
    total_gasto = 0.0
    for r in linhas:
        cat = r["categoria_grande_custom"] or grande_categoria(traduzir_categoria(r["category"] or "Outros"))
        if cat == categoria_grande:
            total_gasto += abs(r["amount"])
            
    if limite:
        disponivel = limite - total_gasto
        return f"\n📊 {categoria_grande} no mês: {fmt_brl(total_gasto)} / {fmt_brl(limite)} (sobra {fmt_brl(disponivel)})"
    return f"\n📊 {categoria_grande} no mês: {fmt_brl(total_gasto)} (sem teto definido)"


def _montar_mensagens(conexao, t: dict, sugestao: dict) -> tuple[str, str]:
    """Devolve (mensagem_telegram, mensagem_para_historico_do_agente).

    A do Telegram é só o que o usuário precisa ler. A do histórico tem o
    mesmo texto + um bloco de contexto interno (id, valores sugeridos,
    instrução de qual ferramenta chamar) -- gravado em `agente_mensagens`
    pra quando o usuário responder só "confirma"/"sim", o agente
    (agente_llm.py) já saber exatamente o que aplicar, sem o usuário
    precisar repetir nada."""
    try:
        data_label = datetime.fromisoformat(t["date"]).strftime("%d/%m %H:%M")
    except ValueError:
        data_label = t["date"][:10]
    valor = fmt_brl(abs(t["amount"]))
    cabecalho = f"🔔 Compra pendente ({data_label})\n{t['description']} — {valor}"

    if sugestao["fonte"] == "gasto_fixo":
        corpo = f"Achei no histórico: é a cobrança fixa \"{sugestao['descricao_sugerida']}\" ({sugestao['categoria']}). Confirma?"
    elif sugestao["encontrado"]:
        corpo = f"Já vi antes: \"{sugestao['descricao_sugerida']}\" ({sugestao['categoria']}), variável. Confirma?"
        if sugestao.get("nota"):
            corpo += f"\n({sugestao['nota']}.)"
    else:
        corpo = (
            f"Não achei histórico parecido. Sugestão: variável, {sugestao['categoria']}, "
            f"\"{sugestao['descricao_sugerida']}\". Confirma ou corrija."
        )

    resumo_orcamento = obter_resumo_categoria(conexao, sugestao['categoria'])
    mensagem_telegram = f"{cabecalho}\n\n{corpo}\n{resumo_orcamento}"
    mensagem_historico = (
        f"{mensagem_telegram}\n"
        f"[contexto interno pro agente, não repita pro usuário: id={t['id']!r}, "
        f"tipo_gasto_sugerido={sugestao['tipo_gasto']!r}, categoria_sugerida={sugestao['categoria']!r}, "
        f"descricao_sugerida={sugestao['descricao_sugerida']!r}. Se o usuário confirmar (\"sim\"/\"confirma\"/"
        f"\"pode\"/\"correto\"), chame editar_transacao(id, nova_descricao=descricao_sugerida, "
        f"nova_categoria_grande=categoria_sugerida) e, se tipo_gasto_sugerido == 'fixo', também "
        f"marcar_como_fixo(id) -- e confirme numa frase o que foi feito. Se o usuário corrigir algo "
        f"(nome/categoria/tipo diferente), use o que ELE disser em vez da sugestão.]"
    )
    return mensagem_telegram, mensagem_historico


def checar_email_pendente() -> int:
    """Roda um ciclo de checagem. Devolve quantas transações pendentes
    novas entraram (0 se a inbox não tinha nada novo desde a última vez)."""
    transacoes = email_source.buscar_transacoes(dias=2)
    if not transacoes:
        return 0

    pendentes_com_sugestao = []
    with db.sessao() as conexao:
        novas = _gravar_pendentes(conexao, transacoes)
        for t in novas:
            sugestao = sugerir_classificacao(conexao, t["description"])
            pendentes_com_sugestao.append((t, sugestao))

    agora = datetime.now().isoformat()
    for t, sugestao in pendentes_com_sugestao:
        with db.sessao() as conexao:
            mensagem_telegram, mensagem_historico = _montar_mensagens(conexao, t, sugestao)
        try:
            enviar_telegram(mensagem_telegram)
        except Exception as e:
            print(f"Erro ao notificar pendente {t['id']}: {e}")
            continue

        if CHAT_ID_PADRAO:
            with db.sessao() as conexao:
                db.gravar_mensagem_agente(
                    conexao, CHAT_ID_PADRAO,
                    {"role": "assistant", "content": mensagem_historico}, agora,
                )

    return len(pendentes_com_sugestao)


def main():
    db.inicializar()
    quantidade = checar_email_pendente()
    print(f"{quantidade} compra(s) pendente(s) nova(s) detectada(s) por e-mail.")


if __name__ == "__main__":
    main()
