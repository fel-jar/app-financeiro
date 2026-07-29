"""Ferramentas (function calling) que o agente conversacional do Telegram
pode chamar -- cada uma é uma função Python determinística que lê/escreve
no banco (mesma fonte de verdade do dashboard). O LLM só decide QUAL
ferramenta chamar e com quais argumentos; toda lógica de data, categoria e
persistência fica em código, não no modelo (mesmo princípio de sempre
neste projeto: empurrar a complexidade pro determinístico).

Cada ferramenta devolve um dict JSON-serializável -- vira o resultado que
o LLM lê pra formular a resposta final em português pro usuário.
"""
import os
from datetime import date, datetime, timedelta

import db
import fatura_parser
import sync
from categorias_grandes import GRANDES_CATEGORIAS, grande_categoria
from dados_db import (
    carregar_caixa_externo, carregar_gastos_fixos_do_banco,
    carregar_transacoes_do_banco, carregar_variaveis_manuais_do_banco,
)
from gerar_dashboard import MESES_PT, construir_panorama_mensal
from normalizacao import traduzir_categoria
from pluggy_client import from_env as pluggy_from_env

NOMES_CARTOES_CONHECIDOS = ["VISA INFINITE PRIME", "THE PLATINUM CARD", "ELO NANQUIM PRIME"]

CATEGORIAS_VALIDAS = sorted(GRANDES_CATEGORIAS.keys()) + ["Outros"]
LIMITE_ITENS = 30


def _intervalo_periodo(periodo: str, data_inicio: str | None, data_fim: str | None) -> tuple[str, str] | dict:
    hoje = date.today()
    if periodo == "hoje":
        return hoje.isoformat(), hoje.isoformat()
    if periodo == "ontem":
        d = hoje - timedelta(days=1)
        return d.isoformat(), d.isoformat()
    if periodo == "esta_semana":
        inicio = hoje - timedelta(days=hoje.weekday())
        return inicio.isoformat(), hoje.isoformat()
    if periodo == "mes_atual":
        return hoje.replace(day=1).isoformat(), hoje.isoformat()
    if periodo == "mes_passado":
        primeiro_atual = hoje.replace(day=1)
        ultimo_passado = primeiro_atual - timedelta(days=1)
        return ultimo_passado.replace(day=1).isoformat(), ultimo_passado.isoformat()
    if periodo == "personalizado":
        if not data_inicio or not data_fim:
            return {"erro": "periodo 'personalizado' exige data_inicio e data_fim (YYYY-MM-DD)."}
        return data_inicio, data_fim
    return {"erro": f"periodo inválido: {periodo!r}. Use hoje/ontem/esta_semana/mes_atual/mes_passado/personalizado."}


def consultar_gastos(
    periodo: str,
    data_inicio: str | None = None,
    data_fim: str | None = None,
    descricao_contem: str | None = None,
    categoria_grande: str | None = None,
    forma: str | None = None,
) -> dict:
    """Total gasto, breakdown por categoria e lista de lançamentos (com
    `id`, pra edição posterior) num período. `periodo` resolve a data de
    forma determinística em Python -- o modelo nunca precisa calcular
    "ontem" ou "mês passado" sozinho."""
    intervalo = _intervalo_periodo(periodo, data_inicio, data_fim)
    if isinstance(intervalo, dict):
        return intervalo
    inicio, fim = intervalo

    with db.sessao() as conexao:
        linhas = conexao.execute(
            """SELECT id, date, COALESCE(description_custom, description) AS descricao,
                      category, amount, account_type, categoria_grande_custom, status
               FROM transacoes
               WHERE substr(date, 1, 10) BETWEEN ? AND ? AND amount < 0
               ORDER BY date DESC""",
            (inicio, fim),
        ).fetchall()

    itens = []
    por_categoria: dict = {}
    for r in linhas:
        categoria_pt = traduzir_categoria(r["category"] or "Outros")
        grande = r["categoria_grande_custom"] or grande_categoria(categoria_pt)
        forma_item = "cartao" if r["account_type"] == "CREDIT" else "pix"
        if categoria_grande and grande != categoria_grande:
            continue
        if forma and forma_item != forma:
            continue
        if descricao_contem and descricao_contem.lower() not in (r["descricao"] or "").lower():
            continue
        valor = abs(r["amount"])
        por_categoria[grande] = por_categoria.get(grande, 0.0) + valor
        itens.append({
            "id": r["id"],
            "data": r["date"][:10],
            "descricao": r["descricao"],
            "categoria": grande,
            "categoria_fina": categoria_pt,
            "forma": forma_item,
            "valor": round(valor, 2),
            "status": r["status"],
        })

    total = sum(i["valor"] for i in itens)
    return {
        "periodo": {"inicio": inicio, "fim": fim},
        "total_despesas": round(total, 2),
        "quantidade": len(itens),
        "por_categoria": [
            {"categoria": c, "total": round(v, 2)}
            for c, v in sorted(por_categoria.items(), key=lambda kv: -kv[1])
        ],
        "itens": itens[:LIMITE_ITENS],
        "itens_omitidos": max(0, len(itens) - LIMITE_ITENS),
    }


def editar_transacao(
    transacao_id: str,
    nova_descricao: str | None = None,
    nova_categoria_grande: str | None = None,
) -> dict:
    """Renomeia e/ou recategoriza uma transação real (nunca apaga nem
    altera valor/data -- só a descrição customizada e a grande categoria,
    mesmos campos que a edição manual no dashboard usa)."""
    if nova_categoria_grande and nova_categoria_grande not in CATEGORIAS_VALIDAS:
        return {"erro": f"categoria inválida: {nova_categoria_grande!r}. Use uma de: {', '.join(CATEGORIAS_VALIDAS)}."}
    if not nova_descricao and not nova_categoria_grande:
        return {"erro": "informe nova_descricao e/ou nova_categoria_grande."}

    with db.sessao() as conexao:
        antes = conexao.execute(
            """SELECT COALESCE(description_custom, description) AS descricao, category,
                      categoria_grande_custom, amount, date
               FROM transacoes WHERE id = ?""",
            (transacao_id,),
        ).fetchone()
        if antes is None:
            return {"erro": f"transação {transacao_id!r} não encontrada."}

        categoria_antes = antes["categoria_grande_custom"] or grande_categoria(traduzir_categoria(antes["category"] or "Outros"))

        if nova_descricao:
            conexao.execute("UPDATE transacoes SET description_custom = ? WHERE id = ?", (nova_descricao, transacao_id))
        if nova_categoria_grande:
            conexao.execute("UPDATE transacoes SET categoria_grande_custom = ? WHERE id = ?", (nova_categoria_grande, transacao_id))

    return {
        "sucesso": True,
        "id": transacao_id,
        "antes": {"descricao": antes["descricao"], "categoria": categoria_antes},
        "depois": {
            "descricao": nova_descricao or antes["descricao"],
            "categoria": nova_categoria_grande or categoria_antes,
        },
    }


def consultar_painel_mensal() -> dict:
    """Painel mês a mês (mesmo cálculo do dashboard): a partir do mês em
    que a fatura que está fechando agora é paga, fixas/variáveis/caixa
    projetado e se cobre ou não, mês a mês."""
    transacoes, saldo = carregar_transacoes_do_banco()
    gastos_fixos_por_mes = carregar_gastos_fixos_do_banco()
    variaveis_manuais_por_mes = carregar_variaveis_manuais_do_banco()
    caixa_externo = carregar_caixa_externo()
    saldo_com_externo = None if saldo is None else saldo + caixa_externo

    panorama = construir_panorama_mensal(transacoes, saldo_com_externo, gastos_fixos_por_mes, variaveis_manuais_por_mes)
    meses = []
    for linha in panorama:
        meses.append({
            "mes": linha["mes"],
            "mes_label": f"{MESES_PT[int(linha['mes'][5:7]) - 1]}/{linha['mes'][2:4]}",
            "caixa_inicio": linha["caixa_inicio"],
            "entrada_prevista": round(linha["entrada"], 2),
            "fixas_total": round(linha["fixas_total"], 2),
            "variaveis_total": round(linha["variaveis_total"], 2),
            "despesas_totais": round(linha["necessario"], 2),
            "saldo_final": linha["saldo_final"],
            "cobre": linha["cobre"],
        })
    return {"hoje": date.today().isoformat(), "meses": meses}


def sincronizar_agora() -> dict:
    """Sincroniza AGORA com a Pluggy (fora do horário fixo do scheduler) --
    mesma rotina de sync.main(), só que devolve um resultado em vez de
    imprimir/sair do processo. Usa as mesmas credenciais Pluggy já
    presentes no ambiente do container do agente."""
    cliente = pluggy_from_env()
    item_id = os.getenv("PLUGGY_ITEM_ID")
    if cliente is None or not item_id:
        return {"erro": "faltam credenciais da Pluggy no ambiente (PLUGGY_CLIENT_ID/SECRET/ITEM_ID)."}

    try:
        with db.sessao() as conexao:
            transacoes = sync.sincronizar_transacoes_e_contas(conexao, cliente, item_id)

            item_id_esposa = os.getenv("PLUGGY_ITEM_ID_ESPOSA")
            if item_id_esposa:
                transacoes += sync.sincronizar_cartao_apenas_credito(conexao, cliente, item_id_esposa)

            for item_id_extra in sync.itens_extras_do_env():
                transacoes += sync.sincronizar_transacoes_e_contas(conexao, cliente, item_id_extra)

            sync.seed_gastos_fixos(conexao)
            sync.seed_orcamento_categoria(conexao, transacoes)
            sync.reconciliar_pendentes_email(conexao)
    except Exception as e:
        return {"erro": f"falha ao sincronizar com a Pluggy: {e}"}

    return {
        "sucesso": True,
        "transacoes_sincronizadas": len(transacoes),
        "sincronizado_em": datetime.now().isoformat(),
    }


def _data_iso_para_date(data_iso: str) -> date:
    ano, mes, dia = data_iso.split("-")
    return date(int(ano), int(mes), int(dia))


def auditar_fatura_pdf(caminho_arquivo: str) -> dict:
    """Compara uma fatura em PDF (baixada de um documento enviado no
    Telegram) com o que já está sincronizado no banco -- parser
    determinístico (fatura_parser.py, sem LLM) extrai os lançamentos reais
    da fatura; esta função casa cada um (por data ±3 dias e valor) com uma
    transação já sincronizada da mesma conta. Devolve o que está faltando
    sincronizar e o que sobra no banco sem bater com a fatura (ex.: um
    estorno que zera parcelas futuras que ainda não foram ajustadas).

    Linhas de pagamento da própria fatura ("PAGTO"/"PAGAMENTO") são
    ignoradas na comparação -- por design nunca são sincronizadas (são
    dinheiro mudando de lugar, já contado compra a compra), não é
    divergência real."""
    try:
        dados_fatura = fatura_parser.extrair_fatura(caminho_arquivo)
    except Exception as e:
        return {"erro": f"não consegui ler o PDF: {e}"}

    cartao = fatura_parser.identificar_cartao(dados_fatura["_texto_pagina1"], NOMES_CARTOES_CONHECIDOS)
    if not cartao:
        return {"erro": "não reconheci qual cartão é essa fatura (nome não bate com nenhuma conta cadastrada)."}

    with db.sessao() as conexao:
        conta = conexao.execute("SELECT account_id FROM contas WHERE account_name = ?", (cartao,)).fetchone()
        if conta is None:
            return {"erro": f"cartão '{cartao}' identificado no PDF, mas essa conta não existe no banco."}
        account_id = conta["account_id"]

        datas = [it["data"] for it in dados_fatura["itens"]]
        data_min = min(datas) if datas else dados_fatura["vencimento"]
        data_max = max(datas) if datas else dados_fatura["vencimento"]

        linhas_banco = conexao.execute(
            """SELECT id, date, description, amount FROM transacoes
               WHERE account_id = ? AND substr(date, 1, 10) BETWEEN ? AND ?""",
            (account_id, data_min, data_max),
        ).fetchall()

    banco_usados = set()
    faltando_no_banco = []
    for item in dados_fatura["itens"]:
        if "PAGTO" in item["descricao"].upper() or "PAGAMENTO" in item["descricao"].upper():
            continue

        data_item = _data_iso_para_date(item["data"])
        achou = None
        for row in linhas_banco:
            if row["id"] in banco_usados:
                continue
            data_row = _data_iso_para_date(row["date"][:10])
            if abs((data_row - data_item).days) > 3:
                continue
            sinal_ok = (item["tipo"] == "DEBIT" and row["amount"] < 0) or (item["tipo"] == "CREDIT" and row["amount"] > 0)
            if sinal_ok and abs(abs(row["amount"]) - item["valor"]) < 0.05:
                achou = row
                break
        if achou:
            banco_usados.add(achou["id"])
        else:
            faltando_no_banco.append(item)

    sobrando_no_banco = [
        {"id": r["id"], "data": r["date"][:10], "descricao": r["description"], "valor": round(abs(r["amount"]), 2)}
        for r in linhas_banco if r["id"] not in banco_usados
    ]

    return {
        "cartao": cartao,
        "vencimento": dados_fatura["vencimento"],
        "total_fatura": dados_fatura["total_fatura"],
        "itens_na_fatura": len(dados_fatura["itens"]),
        "aviso_sobrando_no_banco": (
            "sobrando_no_banco NÃO é confiável como divergência -- o banco pode ter dado de "
            "OUTROS ciclos de fatura que não pertencem a esta fatura específica (a janela de "
            "datas usada na comparação é só um recorte, não o fechamento real do ciclo). Só "
            "trate como divergência de verdade um item de sobrando_no_banco que pareça um "
            "ESTORNO/duplicata óbvia de algo em faltando_no_banco -- senão, ignore."
        ),
        "faltando_no_banco": faltando_no_banco,
        "sobrando_no_banco": sobrando_no_banco,
    }


FERRAMENTAS = [
    {
        "type": "function",
        "function": {
            "name": "consultar_gastos",
            "description": (
                "Consulta despesas reais (Pix e cartão) num período: total gasto, "
                "breakdown por categoria e a lista de lançamentos individuais (cada "
                "um com 'id', necessário pra chamar editar_transacao depois)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "periodo": {
                        "type": "string",
                        "enum": ["hoje", "ontem", "esta_semana", "mes_atual", "mes_passado", "personalizado"],
                        "description": "Período a consultar. Use 'personalizado' + data_inicio/data_fim pra um intervalo específico.",
                    },
                    "data_inicio": {"type": "string", "description": "YYYY-MM-DD, só quando periodo='personalizado'."},
                    "data_fim": {"type": "string", "description": "YYYY-MM-DD, só quando periodo='personalizado'."},
                    "descricao_contem": {"type": "string", "description": "Filtra por parte da descrição da compra (ex: 'uber', 'carrefour')."},
                    "categoria_grande": {"type": "string", "enum": CATEGORIAS_VALIDAS, "description": "Filtra por uma grande categoria."},
                    "forma": {"type": "string", "enum": ["pix", "cartao"], "description": "Filtra por forma de pagamento."},
                },
                "required": ["periodo"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "editar_transacao",
            "description": (
                "Renomeia e/ou recategoriza uma transação real já encontrada via "
                "consultar_gastos (usa o 'id' dela). Nunca apaga a transação nem "
                "altera valor/data -- só a descrição customizada e a grande categoria."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "transacao_id": {"type": "string", "description": "id da transação, obtido em consultar_gastos."},
                    "nova_descricao": {"type": "string", "description": "Nova descrição/nome pra essa compra."},
                    "nova_categoria_grande": {"type": "string", "enum": CATEGORIAS_VALIDAS, "description": "Nova grande categoria."},
                },
                "required": ["transacao_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "consultar_painel_mensal",
            "description": (
                "Painel mês a mês: caixa no início do mês, entrada prevista, "
                "despesas totais (fixas + variáveis) e se cobre ou não, a partir do "
                "mês em que a fatura de cartão que está fechando agora será paga."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sincronizar_agora",
            "description": (
                "Sincroniza AGORA com a Pluggy (fora do horário fixo diário) -- busca "
                "transações e saldos novos e atualiza o banco. Use quando o usuário pedir "
                "explicitamente pra atualizar/sincronizar os dados na hora. Pode levar "
                "alguns segundos."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

EXECUTORES = {
    "consultar_gastos": consultar_gastos,
    "editar_transacao": editar_transacao,
    "consultar_painel_mensal": consultar_painel_mensal,
    "sincronizar_agora": sincronizar_agora,
}
