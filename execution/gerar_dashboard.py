"""Gera dashboard/index.html com o fluxo de caixa a partir de transações
(reais via Pluggy ou mock, conforme execution/pluggy_client.py e mock_data.py).

Uso: python execution/gerar_dashboard.py
"""
import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from pluggy_client import from_env
from mock_data import gerar_transacoes
from email_source import buscar_transacoes as buscar_transacoes_email
from gastos_fixos import GASTOS_FIXOS, valor_planejamento, total_fixo_mensal
from normalizacao import traduzir_categoria, normalizar_transacoes_pluggy, CATEGORIA_RENDA_EXTRA
from categorias_grandes import grande_categoria
import graficos
import ui
# Reexportados: app.py e telegram_diario.py importam a formatação daqui
# desde antes de existir o módulo ui.
from ui import fmt_brl, fmt_brl_ou_indisponivel, var_serie  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "dashboard" / "index.html"

COR_RECEITA_LIGHT, COR_RECEITA_DARK = "#2a78d6", "#3987e5"
COR_DESPESA_LIGHT, COR_DESPESA_DARK = "#e34948", "#e66767"

MESES_PT = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]


def carregar_transacoes() -> tuple[list[dict], float | None]:
    cliente = from_env()
    item_id = os.getenv("PLUGGY_ITEM_ID")
    if cliente is not None and item_id:
        print("Usando dados reais da Pluggy (conta + cartões via meu.pluggy.ai).")
        return normalizar_transacoes_pluggy(cliente, item_id)

    if os.getenv("EMAIL_IMAP_USER") and os.getenv("EMAIL_IMAP_APP_PASSWORD"):
        print("Usando notificações de compra encaminhadas por e-mail (Bradesco).")
        return buscar_transacoes_email(), None

    print("Sem credenciais no .env -> usando dados mock (sandbox).")
    transacoes = gerar_transacoes()
    return transacoes, saldo_atual(transacoes)


def agregar_por_mes(transacoes: list[dict]) -> dict:
    meses = defaultdict(lambda: {"receita": 0.0, "despesa": 0.0})
    for t in transacoes:
        mes = t["date"][:7]  # YYYY-MM
        if t["amount"] >= 0:
            meses[mes]["receita"] += t["amount"]
        else:
            meses[mes]["despesa"] += abs(t["amount"])
    return dict(sorted(meses.items()))


def agregar_categorias_despesa(transacoes: list[dict], top_n: int = 5) -> list[tuple]:
    categorias = defaultdict(float)
    for t in transacoes:
        if t["amount"] < 0:
            cat = t.get("category") or "Outros"
            categorias[cat] += abs(t["amount"])
    return sorted(categorias.items(), key=lambda kv: kv[1], reverse=True)[:top_n]


def saldo_atual(transacoes: list[dict]) -> float | None:
    com_saldo = [t for t in transacoes if t.get("balance") is not None]
    if not com_saldo:
        return None
    ultima = max(com_saldo, key=lambda t: t["date"])
    return ultima["balance"]


def media_categoria_meses_fechados(transacoes: list[dict], categoria: str, meses: int = 3) -> float | None:
    """Média de uma categoria de receita nos últimos `meses` meses JÁ
    FECHADOS (exclui o mês atual, que costuma estar parcial). Meses sem
    nenhum lançamento daquela categoria não entram na média -- evita que
    um mês sem renda extra derrube a média artificialmente."""
    por_mes = defaultdict(float)
    for t in transacoes:
        if (t.get("category") or "") == categoria:
            por_mes[t["date"][:7]] += t["amount"]
    mes_atual = datetime.now().strftime("%Y-%m")
    fechados = sorted(m for m in por_mes if m < mes_atual)
    if not fechados:
        return None
    ultimos = fechados[-meses:]
    return sum(por_mes[m] for m in ultimos) / len(ultimos)


def salario_medio_recente(transacoes: list[dict], meses: int = 3) -> float | None:
    return media_categoria_meses_fechados(transacoes, "Salary", meses)


def renda_extra_media_recente(transacoes: list[dict], meses: int = 3) -> float | None:
    return media_categoria_meses_fechados(transacoes, CATEGORIA_RENDA_EXTRA, meses)


def gasto_cartao_por_mes(transacoes: list[dict]) -> dict:
    """Total gasto no cartão por mês da COMPRA (não da fatura prevista) --
    serve pra ver se o gasto no cartão está subindo ou descendo mês a mês
    (indicador de "estancar o sangramento")."""
    por_mes = defaultdict(float)
    for t in transacoes:
        if t.get("creditCardMetadata") and t["amount"] < 0:
            por_mes[t["date"][:7]] += abs(t["amount"])
    return dict(sorted(por_mes.items()))


def _mes_seguinte(aaaa_mm: str, n: int) -> str:
    ano, mes = map(int, aaaa_mm.split("-"))
    mes += n
    while mes > 12:
        mes -= 12
        ano += 1
    return f"{ano:04d}-{mes:02d}"


def _fixas_do_mes(mes: str, gastos_fixos_por_mes: dict[str, list[dict]] | None) -> list[dict]:
    """Itens fixos daquele mês (banco tem prioridade -- reflete edição via
    /fixos/<mes>); sem banco, cai na lista estática de gastos_fixos.py."""
    itens = gastos_fixos_por_mes.get(mes) if gastos_fixos_por_mes else None
    if itens:
        return itens
    return [
        {
            "nome": item["nome"],
            "forma": item.get("forma", "cartao"),
            "valor": valor_planejamento(item),
            "categoria": item.get("categoria", "Outros"),
        }
        for item in GASTOS_FIXOS
    ]


def construir_panorama_mensal(
    transacoes: list[dict],
    saldo: float | None,
    gastos_fixos_por_mes: dict[str, list[dict]] | None = None,
    variaveis_manuais_por_mes: dict[str, list[dict]] | None = None,
    meses_futuros: int = 5,
) -> list[dict]:
    """Monta o painel mês a mês: a partir do mês em que a fatura que está
    fechando agora será PAGA (não o mês atual do calendário), com
    `meses_futuros` seguintes, cada um com despesas divididas em Fixas
    (lista editável, ver /fixos/<mes>) e Variáveis (parcelas de cartão já
    comprometidas), entrada prevista (salário + renda extra) e um saldo de
    caixa projetado rodando de mês a mês, pra responder "dá pra cobrir?".

    Decisão do usuário em 2026-07-25: o painel não mostra mais o mês
    corrente do calendário nem projeta a partir dele -- gasto que já
    aconteceu neste mês já está refletido no saldo real da conta (`saldo`
    recebido aqui já é o saldo de hoje). O primeiro card do painel é o mês
    em que a fatura que está fechando agora cai pra pagamento, com "caixa
    no início do mês" = saldo real de hoje (não uma projeção arrastada).
    "Eu não quero saber da projeção do passado mês a mês. Quero só saber o
    que eu vou pagar de agosto para frente" -- ver `directives/
    dashboard_fluxo_caixa.md`.

    Cada compra de cartão é agrupada pela FATURA (`billForecastDate`), não
    pela data da compra -- uma compra feita perto do fechamento do cartão
    pode ter sido realizada em um mês mas só ser cobrada no mês seguinte.
    Além disso, a Pluggy nomeia a fatura pelo mês de FECHAMENTO/referência,
    não pelo mês em que ela é efetivamente paga -- o pagamento cai no mês
    seguinte ao nome que a Pluggy dá (confirmado pelo usuário: fatura que a
    Pluggy chama de "julho" é paga em agosto), por isso todo `bill` é
    deslocado +1 mês (`_mes_seguinte(bill, 1)`) antes de virar chave do
    painel. Transação de cartão sem `billForecastDate` (acontece bastante
    nos dados reais -- ver achado 2026-07-25) cai de volta na data da
    própria compra, pra nunca sumir uma despesa real do painel.

    Variáveis = só parcelas de cartão já comprometidas (não é estimativa
    nova -- a API guarda um "retrato" da parcela em cada fatura já
    fechada, por isso a projeção parte só das parcelas que estão na fatura
    ATUAL, `bill_raw == mes_atual`, não de todo o histórico, senão cada
    retrato passado geraria sua própria projeção e duplicaria os valores
    futuros) + gasto variável manual em Pix (ver /variaveis/<mes>). MENOS
    o que já bate com um item de Fixas (ver transacao_id_origem).

    **Achado 2026-07-26 (auditoria do usuário contra a fatura fechada)**:
    uma compra parcelada em 18x (`MP*SAMSUNGELETRONICADAAMA`) foi
    estornada no dia seguinte, mas isso escondia dois bugs:
    1. A Pluggy, nesse caso, materializou as 18 parcelas de uma vez só, na
       MESMA fatura (`bill_raw` igual pras 18), em vez de uma por fatura
       futura como é o normal (ver `EC*SAMSUNG`, compra antiga, correta).
       Projetar a partir da parcela 1/18 duplicava 2/18..18/18, que já
       existiam como transação real.
    2. O estorno (transação CREDIT, valor positivo) nunca era descontado
       -- só existia o filtro `amount >= 0: continue`, que IGNORA
       silenciosamente qualquer reembolso, positivo ou negativo pro
       usuário.
    Corrigido com duas travas abaixo: `maior_parcela_realizada` (nunca
    projeta um número de parcela que já existe como transação real, seja
    qual for a fatura) e `descricoes_estornadas` (uma transação CREDIT com
    a mesma descrição de uma compra parcelada cancela o grupo inteiro --
    real e projetado).
    """
    mes_atual = datetime.now().strftime("%Y-%m")
    salario_medio = salario_medio_recente(transacoes) or 0.0
    renda_extra_media = renda_extra_media_recente(transacoes) or 0.0
    entrada_prevista = salario_medio + renda_extra_media

    fixas_mes_atual = _fixas_do_mes(mes_atual, gastos_fixos_por_mes)
    ids_convertidos_em_fixo = {f["transacao_id_origem"] for f in fixas_mes_atual if f.get("transacao_id_origem")}
    nomes_convertidos_em_fixo = {f["nome"] for f in fixas_mes_atual if f.get("forma") == "cartao"}

    # Pré-processamento: descobre parcelamentos estornados (mesma descrição
    # ORIGINAL -- nunca a customizada, ver achado abaixo -- de uma
    # transação CREDIT positiva) e o maior installmentNumber já
    # materializado como transação real por (descrição, total de parcelas)
    # -- essa chave identifica o mesmo parcelamento já que a API não
    # devolve um id de compra único.
    #
    # **Achado 2026-07-26**: usar a descrição CUSTOMIZADA (`description`,
    # que já vem com o apelido do usuário via description_custom) pra essa
    # chave quebra o casamento -- um estorno sem apelido não bate com as
    # parcelas que o usuário já tinha renomeado (aconteceu de verdade: 18
    # parcelas da Samsung viraram "Celular novo", o estorno ficou com o
    # nome original, e a exclusão nunca disparava). Sempre usar
    # `descriptionRaw` (nunca sobrescrito) pra agrupar/casar; o apelido
    # customizado continua sendo usado só na exibição (`descricao` no item
    # do painel), não na lógica.
    descricoes_estornadas: set[str] = set()
    maior_parcela_realizada: dict[tuple, int] = {}
    for t in transacoes:
        meta = t.get("creditCardMetadata")
        if not meta:
            continue
        descricao_chave = t.get("descriptionRaw") or t.get("description") or ""
        if t.get("type") == "CREDIT" and t["amount"] > 0:
            descricoes_estornadas.add(descricao_chave)
            continue
        total_parc, atual_parc = meta.get("totalInstallments"), meta.get("installmentNumber")
        if t.get("type") == "DEBIT" and total_parc and atual_parc is not None:
            chave = (descricao_chave, total_parc)
            maior_parcela_realizada[chave] = max(maior_parcela_realizada.get(chave, 0), atual_parc)

    # Cartão: agrupado pelo mês de PAGAMENTO da fatura (billForecastDate
    # deslocado +1 mês -- ver docstring), não pela data da compra nem pelo
    # nome cru que a Pluggy dá à fatura. Cada transação real entra direto
    # no mês em que será paga (inclusive a fatura que está fechando agora),
    # e só as parcelas em aberto na fatura ATUAL são projetadas pros meses
    # seguintes.
    despesas_cartao_por_pagamento: dict = defaultdict(list)
    for t in transacoes:
        meta = t.get("creditCardMetadata")
        if not meta or t.get("type") != "DEBIT" or t["amount"] >= 0:
            continue
        descricao = t.get("description") or t.get("descriptionRaw") or "—"
        if t.get("id") in ids_convertidos_em_fixo or descricao in nomes_convertidos_em_fixo:
            continue
        descricao_chave = t.get("descriptionRaw") or t.get("description") or ""
        if descricao_chave in descricoes_estornadas:
            continue
        bill_raw = meta.get("billForecastDate") or t["date"][:7]
        mes_pagamento = _mes_seguinte(bill_raw, 1)
        atual_parc, total_parc = meta.get("installmentNumber"), meta.get("totalInstallments")
        valor = abs(t["amount"])
        categoria_pt = traduzir_categoria(t.get("category") or "Outros")
        parcela_txt = f"{atual_parc}/{total_parc}" if atual_parc and total_parc and total_parc > 1 else None
        despesas_cartao_por_pagamento[mes_pagamento].append({
            "id": t.get("id"),
            "descricao": descricao,
            "categoria": categoria_pt,
            "categoria_grande": t.get("categoriaGrandeCustom") or grande_categoria(categoria_pt),
            "forma": "cartao",
            "valor": valor,
            "parcela": parcela_txt,
        })

        if bill_raw != mes_atual or atual_parc is None or total_parc is None:
            continue
        chave = (descricao_chave, total_parc)
        maior_ja_realizada = maior_parcela_realizada.get(chave, atual_parc)
        if atual_parc != maior_ja_realizada:
            continue  # só a parcela mais avançada do grupo projeta -- evita duplicar
        restantes = total_parc - maior_ja_realizada
        for i in range(1, min(restantes, meses_futuros) + 1):
            bill_futuro = _mes_seguinte(bill_raw, i)
            mes_pagamento_futuro = _mes_seguinte(bill_futuro, 1)
            despesas_cartao_por_pagamento[mes_pagamento_futuro].append({
                "id": t.get("id"),
                "descricao": descricao,
                "categoria": categoria_pt,
                "categoria_grande": t.get("categoriaGrandeCustom") or grande_categoria(categoria_pt),
                "forma": "cartao",
                "valor": valor,
                "parcela": f"{maior_ja_realizada + i}/{total_parc}",
            })

    mes_pagamento_atual = _mes_seguinte(mes_atual, 1)
    meses_ordenados = [mes_pagamento_atual] + [_mes_seguinte(mes_pagamento_atual, i) for i in range(1, meses_futuros + 1)]

    linhas = []
    caixa_inicio = saldo
    for i, mes in enumerate(meses_ordenados):
        fixas_itens = _fixas_do_mes(mes, gastos_fixos_por_mes)
        fixas_total = sum(it["valor"] for it in fixas_itens)

        variaveis_itens = list(despesas_cartao_por_pagamento.get(mes, []))
        manuais_mes = [
            {**it, "categoria_grande": it["categoria"]}
            for it in (variaveis_manuais_por_mes or {}).get(mes, [])
        ]
        variaveis_itens = variaveis_itens + manuais_mes
        variaveis_total = sum(it["valor"] for it in variaveis_itens)

        necessario = fixas_total + variaveis_total
        saldo_final = None if caixa_inicio is None else caixa_inicio + entrada_prevista - necessario
        cobre = None if caixa_inicio is None else (caixa_inicio + entrada_prevista) >= necessario

        linhas.append({
            "mes": mes,
            "eh_atual": i == 0,
            "fixas_itens": fixas_itens,
            "fixas_total": fixas_total,
            "variaveis_itens": sorted(variaveis_itens, key=lambda d: -d["valor"]),
            "variaveis_total": variaveis_total,
            "necessario": necessario,
            "salario_medio": salario_medio,
            "renda_extra_media": renda_extra_media,
            "entrada": entrada_prevista,
            "caixa_inicio": caixa_inicio,
            "saldo_final": saldo_final,
            "cobre": cobre,
        })
        caixa_inicio = saldo_final

    return linhas


def realizado_por_grande_categoria(transacoes: list[dict], mes: str) -> dict[str, float]:
    """Gasto REAL do mês (pela data da compra, não pela fatura) agrupado
    por grande categoria -- é o lado "real" do confronto com o orçamento.
    Usa a data da compra de propósito: o teto de orçamento é sobre o que
    você gastou no mês, não sobre o que vai pagar."""
    totais: dict[str, float] = defaultdict(float)
    for t in transacoes:
        if t["amount"] >= 0 or t["date"][:7] != mes:
            continue
        categoria_pt = traduzir_categoria(t.get("category") or "Outros")
        grande = t.get("categoriaGrandeCustom") or grande_categoria(categoria_pt)
        totais[grande] += abs(t["amount"])
    return dict(totais)


def media_por_grande_categoria(transacoes: list[dict], meses: int = 3) -> dict[str, float]:
    """Média por grande categoria nos últimos `meses` FECHADOS -- o mês
    corrente fica fora porque está parcial e derrubaria a média. Divide
    pelo número de meses considerados (não pelos meses em que aquela
    categoria apareceu): uma categoria que só gastou em 1 dos 3 meses tem
    média mensal menor mesmo, e é isso que se quer ver."""
    por_mes: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    mes_atual = datetime.now().strftime("%Y-%m")
    for t in transacoes:
        mes = t["date"][:7]
        if t["amount"] >= 0 or mes >= mes_atual:
            continue
        categoria_pt = traduzir_categoria(t.get("category") or "Outros")
        grande = t.get("categoriaGrandeCustom") or grande_categoria(categoria_pt)
        por_mes[mes][grande] += abs(t["amount"])

    ultimos = sorted(por_mes)[-meses:]
    if not ultimos:
        return {}
    totais: dict[str, float] = defaultdict(float)
    for mes in ultimos:
        for grande, valor in por_mes[mes].items():
            totais[grande] += valor / len(ultimos)
    return dict(totais)


def gasto_ate_dia(transacoes: list[dict], mes: str, dia_limite: int) -> float:
    """Total gasto num mês contando só até o dia `dia_limite` -- é o que
    torna a comparação com o mês anterior honesta: no dia 10 de agosto,
    comparar o mês inteiro de julho contra 10 dias de agosto sempre daria
    'melhorei muito'."""
    total = 0.0
    for t in transacoes:
        if t["amount"] >= 0 or t["date"][:7] != mes:
            continue
        try:
            dia = int(t["date"][8:10])
        except ValueError:
            dia = 1
        if dia <= dia_limite:
            total += abs(t["amount"])
    return total


def comparativo_mes_anterior(transacoes: list[dict], hoje: datetime) -> dict:
    """Gasto do mês corrente até hoje x mesmo intervalo de dias do mês
    anterior. Responde "estou melhorando?" sem depender de o mês ter
    fechado."""
    mes = hoje.strftime("%Y-%m")
    anterior = _mes_seguinte(mes, -1) if int(mes[5:7]) > 1 else f"{int(mes[:4]) - 1}-12"
    atual = gasto_ate_dia(transacoes, mes, hoje.day)
    passado = gasto_ate_dia(transacoes, anterior, hoje.day)
    variacao = None if passado <= 0 else (atual - passado) / passado * 100
    return {"mes": mes, "mes_anterior": anterior, "atual": atual, "anterior": passado, "variacao": variacao}


def parcelas_que_terminam(panorama: list[dict]) -> list[dict]:
    """Compras parceladas cuja ÚLTIMA parcela cai dentro da janela
    projetada. Cada uma é um compromisso mensal que some do mês seguinte
    em diante -- é a resposta pra "quando é que essa conta afrouxa?", que
    hoje só aparecia indiretamente na queda das colunas do gráfico."""
    por_mes: dict[str, list[dict]] = defaultdict(list)
    for linha in panorama:
        for item in linha["variaveis_itens"]:
            parcela = item.get("parcela")
            if not parcela or "/" not in parcela:
                continue
            atual, total = parcela.split("/", 1)
            if atual.strip() == total.strip():
                por_mes[linha["mes"]].append(item)
    return [
        {"mes": mes, "itens": sorted(itens, key=lambda i: -i["valor"]), "alivio": sum(i["valor"] for i in itens)}
        for mes, itens in sorted(por_mes.items())
    ]


def render_classe_por_categoria(itens: list[dict], chave_nome: str, permitir_tornar_fixo: bool = False) -> str:
    """Agrupa itens (fixas ou variáveis) por grande categoria e renderiza
    cada grupo como <details> expansível -- mesmo padrão visual de
    render_categorias_expansivel, mas agrupando por categoria_grande e
    mostrando a forma de pagamento (pix/cartão) ao lado de cada item.

    `permitir_tornar_fixo` (só nas Variáveis do mês atual) acrescenta um
    botão "→ fixo" que promove aquela transação real a gasto fixo
    recorrente -- ver /transacao/<id>/tornar-fixo em app.py. Aparece mesmo
    quando `parcela` está preenchido: a linha do mês atual é sempre a
    transação real (id de verdade), nunca uma parcela futura projetada --
    essas só entram nos meses seguintes, fora do escopo de
    `permitir_tornar_fixo`. Uma compra parcelada recorrente (ex.: seguro em
    12x) também precisa poder virar fixa (achado 2026-07-29)."""
    if not itens:
        return '<p class="vazio">Nenhum lançamento.</p>'

    por_grande: dict = defaultdict(list)
    for it in itens:
        por_grande[it.get("categoria_grande") or it.get("categoria") or "Outros"].append(it)
    totais = {grande: sum(i["valor"] for i in lst) for grande, lst in por_grande.items()}
    max_valor = max(totais.values()) or 1
    total_geral = sum(totais.values()) or 1

    linhas = ""
    for grande, total in sorted(totais.items(), key=lambda kv: -kv[1]):
        largura = round(total / max_valor * 100, 1)
        pct = total / total_geral * 100
        itens_grupo = sorted(por_grande[grande], key=lambda i: -i["valor"])
        linhas_item = ""
        for i in itens_grupo:
            nome = i.get(chave_nome, "—")
            forma_txt = " · Pix" if i.get("forma") == "pix" else " · Cartão" if i.get("forma") == "cartao" else ""
            parcela_txt = f' <span class="tag-parcela">{i["parcela"]}</span>' if i.get("parcela") else ""
            editar = (
                f'<a class="link-discreto acao-item" href="/transacao/{i["id"]}/editar" '
                f'title="Renomear ou recategorizar">✎ editar</a>' if i.get("id") else ""
            )
            tornar_fixo = ""
            if permitir_tornar_fixo and i.get("id"):
                tornar_fixo = (
                    f'<form method="post" action="/transacao/{i["id"]}/tornar-fixo" style="display:inline;" '
                    f'onsubmit="return confirm(\'Tornar este lançamento um gasto fixo recorrente?\');">'
                    f'<button type="submit" class="link-botao acao-item">→ tornar fixo</button></form>'
                )
            linhas_item += (
                f'<tr><td>{nome}<span class="meta-item">{forma_txt}</span>{parcela_txt}'
                f'<span class="acoes-item">{editar}{tornar_fixo}</span></td>'
                f'<td class="num">{fmt_brl(i["valor"])}</td></tr>'
            )
        linhas += f"""
        <details class="cat-detalhe">
          <summary title="{len(itens_grupo)} lançamento(s) · {pct:.0f}% do total">
            <div class="linha-cat">
              <span class="cat-label"><span class="ponto-cat" style="background:{var_serie(grande)}"></span>{grande}</span>
              <div class="cat-track"><div class="cat-barra" style="width:{largura}%;background:{var_serie(grande)}"></div></div>
              <span class="cat-valor">{fmt_brl(total)}</span>
            </div>
          </summary>
          <table class="tabela-detalhe"><tbody>{linhas_item}</tbody></table>
        </details>"""
    return linhas


def render_classe_expansivel(
    titulo: str, itens: list[dict], chave_nome: str, total: float,
    mes: str | None = None, permitir_tornar_fixo: bool = False, editar_href: str | None = None,
) -> str:
    """Card "Fixas" ou "Variáveis" do painel do mês: total no cabeçalho,
    corpo agrupado por grande categoria. `mes` acrescenta o link pra
    /fixos/<mes> (Fixas); `editar_href` sobrescreve com outra URL (usado
    pra /variaveis/<mes>, onde dá pra incluir gasto manual em pix)."""
    corpo = render_classe_por_categoria(itens, chave_nome, permitir_tornar_fixo=permitir_tornar_fixo)
    href = editar_href or (f"/fixos/{mes}" if mes else None)
    editar_link = f'<a class="link-discreto" href="{href}">✎ editar</a>' if href else ""
    chave = "fixas" if titulo.lower().startswith("fix") else "variaveis"
    return f"""
  <details class="classe-despesa" open>
    <summary>
      <span class="classe-titulo"><span class="chave {chave}"></span>{titulo}</span>
      <span class="classe-total">{fmt_brl(total)}</span>
    </summary>
    <div class="classe-corpo">
      <div class="classe-acoes">{editar_link}</div>
      {corpo}
    </div>
  </details>"""


def render_mes_panorama(linha: dict, aberto: bool) -> str:
    cobre = linha["cobre"]
    badge = ""
    if cobre is not None:
        badge_texto = "Cobre" if cobre else "Não cobre"
        badge_classe = "good" if cobre else "critical"
        icone = "✓" if cobre else "!"
        badge = f'<span class="badge {badge_classe}"><span aria-hidden="true">{icone}</span>{badge_texto}</span>'

    caixa_inicio_classe = "" if linha["caixa_inicio"] is None else ("good" if linha["caixa_inicio"] >= 0 else "critical")
    saldo_final_classe = "good" if (linha["saldo_final"] or 0) >= 0 else "critical"
    open_attr = " open" if aberto else ""
    fixas_html = render_classe_expansivel("Fixas", linha["fixas_itens"], "nome", linha["fixas_total"], mes=linha["mes"])
    variaveis_html = render_classe_expansivel(
        "Variáveis", linha["variaveis_itens"], "descricao", linha["variaveis_total"],
        permitir_tornar_fixo=linha.get("eh_atual", False),
        editar_href=f"/variaveis/{linha['mes']}",
    )
    marcador_atual = '<span class="badge neutro">próxima fatura</span>' if linha.get("eh_atual") else ""

    return f"""
  <details class="card mes-panorama"{open_attr}>
    <summary>
      <span class="mes-cabecalho">
        <span class="mes-panorama-titulo">{MESES_PT[int(linha['mes'][5:7]) - 1]}/{linha['mes'][2:4]}</span>
        {marcador_atual}
      </span>
      <span class="mes-resumo">
        <span class="mes-resumo-valor">{fmt_brl(linha['necessario'])} <span class="mes-resumo-label">a pagar</span></span>
        {badge}
      </span>
    </summary>
    <div class="tiles" style="margin-top:18px;">
      <div class="tile"><div class="label">Caixa no início do mês</div><div class="valor {caixa_inicio_classe}">{fmt_brl_ou_indisponivel(linha['caixa_inicio'])}</div></div>
      <div class="tile"><div class="label">Entrada prevista</div><div class="valor">{fmt_brl(linha['entrada'])}</div>
        <div class="nota">salário {fmt_brl(linha['salario_medio'])} + extra {fmt_brl(linha['renda_extra_media'])}</div></div>
      <div class="tile"><div class="label">A pagar no mês</div><div class="valor warn">{fmt_brl(linha['necessario'])}</div>
        <div class="nota">fixas {fmt_brl(linha['fixas_total'])} + variáveis {fmt_brl(linha['variaveis_total'])}</div></div>
      <div class="tile destaque"><div class="label">Saldo projetado no fim do mês</div><div class="valor {saldo_final_classe}">{fmt_brl_ou_indisponivel(linha['saldo_final'])}</div></div>
    </div>
    <div class="classes-despesa">
      {fixas_html}
      {variaveis_html}
    </div>
  </details>"""


CSS_DASHBOARD = """
  /* --- Herói: a resposta, em uma frase ------------------------------ */
  .heroi { padding: 8px 0 26px; }
  .heroi-frase {
    margin: 0; display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap;
  }
  .heroi-numero {
    font-family: var(--fonte-numero); font-variant-numeric: tabular-nums;
    font-size: clamp(38px, 7vw, 60px); font-weight: 600; line-height: 1;
    letter-spacing: -0.035em;
  }
  .heroi.good .heroi-numero { color: var(--good); }
  .heroi.critical .heroi-numero { color: var(--critical); }
  .heroi.neutro .heroi-numero { color: var(--text-primary); }
  .heroi-titulo { font-size: clamp(17px, 2.6vw, 22px); font-weight: 500; color: var(--text-secondary); }
  .heroi.neutro .heroi-titulo { color: var(--text-primary); font-weight: 600; }
  .heroi-apoio {
    margin: 12px 0 0; font-size: 14px; line-height: 1.55; color: var(--text-secondary); max-width: 62ch;
  }
  .selo-sync {
    margin: 14px 0 0; font-size: 12.5px; color: var(--text-secondary);
    display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  }

  /* Indicadores: faixa de apoio, não quatro heróis concorrentes. */
  .faixa-indicadores {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    border-top: 1px solid var(--border); border-bottom: 1px solid var(--border);
  }
  .indicador {
    display: flex; flex-direction: column; gap: 3px; padding: 14px 18px 14px 0;
  }
  .indicador + .indicador { border-left: 1px solid var(--border); padding-left: 18px; }
  .indicador .rotulo { font-size: 12px; color: var(--text-secondary); }
  .indicador .valor { font-size: 17px; font-weight: 600; letter-spacing: -0.015em; }
  .indicador .nota { font-size: 11.5px; color: var(--text-muted); line-height: 1.35; }
  @media (max-width: 700px) {
    .indicador + .indicador { border-left: none; padding-left: 0; border-top: 1px solid var(--grid); }
  }

  /* Bloco = seção sem caixa. Diagnóstico e histórico são consulta, não
     decisão -- não precisam do mesmo peso visual do painel mensal. */
  .bloco { padding: 4px 0 26px; border-bottom: 1px solid var(--border); margin-bottom: 4px; }
  .bloco > h3 { font-size: 14px; font-weight: 600; margin: 0 0 4px; }
  .bloco > .ajuda { font-size: 12.5px; color: var(--text-secondary); margin: 0 0 18px; max-width: 62ch; }
  .sub-bloco { padding: 20px 0 0; }
  .sub-bloco > h3 { font-size: 13px; font-weight: 600; margin: 0 0 12px; color: var(--text-secondary); }
  .delta {
    display: inline-flex; align-items: center; gap: 2px; font-size: 12px; font-weight: 650;
    padding: 2px 7px; border-radius: 999px; vertical-align: middle; margin-left: 4px;
    font-variant-numeric: tabular-nums; letter-spacing: 0;
  }
  .delta.good { background: var(--good-bg); color: var(--good); }
  .delta.critical { background: var(--critical-bg); color: var(--critical); }

  /* --- Painel mês a mês -------------------------------------------- */
  .mes-panorama { padding: 0; }
  .mes-panorama > summary {
    display: flex; align-items: center; justify-content: space-between; gap: 12px;
    padding: 16px 20px; cursor: pointer; list-style: none; border-radius: var(--r-md);
  }
  .mes-panorama > summary::-webkit-details-marker { display: none; }
  .mes-panorama > summary:hover { background: var(--surface-2); }
  .mes-panorama[open] > summary { border-bottom: 1px solid var(--border); border-radius: var(--r-md) var(--r-md) 0 0; }
  .mes-cabecalho { display: flex; align-items: center; gap: 10px; }
  .mes-cabecalho::before {
    content: "›"; color: var(--text-muted); font-size: 17px; line-height: 1;
    transition: transform .18s cubic-bezier(.2,.7,.3,1); display: inline-block;
  }
  .mes-panorama[open] .mes-cabecalho::before { transform: rotate(90deg); }
  .mes-panorama-titulo { font-size: 15.5px; font-weight: 620; letter-spacing: -0.01em; }
  .mes-resumo { display: flex; align-items: center; gap: 12px; }
  .mes-resumo-valor { font-size: 13.5px; font-variant-numeric: tabular-nums; font-weight: 600; }
  .mes-resumo-label { color: var(--text-muted); font-weight: 400; font-size: 12px; }
  .mes-panorama > .tiles, .mes-panorama > .classes-despesa { padding: 0 20px; }
  .mes-panorama > .classes-despesa { padding-bottom: 20px; margin-top: 18px; }

  .classe-despesa { border: 1px solid var(--border); border-radius: var(--r-sm); margin-bottom: 10px; background: var(--page); }
  .classe-despesa > summary {
    display: flex; align-items: center; justify-content: space-between; gap: 10px;
    padding: 11px 14px; cursor: pointer; list-style: none; font-size: 13.5px;
  }
  .classe-despesa > summary::-webkit-details-marker { display: none; }
  .classe-despesa > summary:hover { background: var(--surface-2); border-radius: var(--r-sm); }
  .classe-titulo { display: flex; align-items: center; gap: 8px; font-weight: 600; }
  .classe-total { font-variant-numeric: tabular-nums; font-weight: 600; }
  .classe-corpo { padding: 4px 14px 14px; }
  .classe-acoes { display: flex; justify-content: flex-end; margin-bottom: 8px; }

  .cat-detalhe { margin-bottom: 3px; }
  .cat-detalhe > summary { list-style: none; cursor: pointer; padding: 3px 4px; border-radius: 6px; }
  .cat-detalhe > summary::-webkit-details-marker { display: none; }
  .cat-detalhe > summary:hover { background: var(--surface-2); }
  .linha-cat { display: grid; grid-template-columns: 142px 1fr 96px; align-items: center; gap: 10px; }
  .cat-label { font-size: 13px; color: var(--text-secondary); display: flex; align-items: center; gap: 7px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .ponto-cat { width: 8px; height: 8px; border-radius: 2px; flex: 0 0 8px; }
  .cat-track { background: var(--grid); border-radius: 4px; height: 12px; }
  .cat-barra { height: 12px; border-radius: 4px; }
  .cat-valor { font-size: 12.5px; color: var(--text-secondary); text-align: right; font-variant-numeric: tabular-nums; }
  .tabela-detalhe { margin: 2px 0 10px 15px; width: calc(100% - 15px); font-size: 12.5px; }
  .tabela-detalhe td { padding: 5px 6px; }
  .meta-item { color: var(--text-muted); }
  .tag-parcela {
    display: inline-block; font-size: 10.5px; padding: 1px 6px; border-radius: 999px;
    background: var(--surface-2); color: var(--text-secondary); margin-left: 5px; font-variant-numeric: tabular-nums;
  }
  .acoes-item { margin-left: 8px; display: inline-flex; gap: 10px; opacity: 0; transition: opacity .12s ease; }
  .tabela-detalhe tr:hover .acoes-item, .acoes-item:focus-within { opacity: 1; }
  @media (hover: none) { .acoes-item { opacity: 1; } }

  /* --- Gráficos do histórico --------------------------------------- */
  .chart-mensal {
    display: flex; align-items: flex-end; gap: 16px; height: 180px; padding-top: 10px;
    border-bottom: 1px solid var(--axis); overflow-x: auto;
  }
  .grupo-mes { display: flex; flex-direction: column; align-items: center; gap: 8px; min-width: 42px; }
  .barras { display: flex; align-items: flex-end; gap: 3px; height: 160px; }
  .barra { width: 16px; border-radius: 3px 3px 0 0; }
  .barra.receita { background: var(--receita); }
  .barra.despesa { background: var(--despesa); }
  .mes-label { font-size: 11px; color: var(--text-muted); }
  .historico > summary {
    cursor: pointer; font-size: 13px; color: var(--text-secondary); list-style: none;
    display: flex; align-items: center; gap: 8px;
  }
  .historico > summary::-webkit-details-marker { display: none; }
  .historico > summary::before { content: "›"; color: var(--text-muted); font-size: 16px; transition: transform .18s ease; display: inline-block; }
  .historico[open] > summary::before { transform: rotate(90deg); }
  .historico > summary:hover { color: var(--text-primary); }
  .historico { border-bottom: none; }

  @media (max-width: 560px) {
    .topo-inner { flex-direction: column; align-items: flex-start; gap: 10px; }
    .mes-panorama > summary { flex-wrap: wrap; }
    .linha-cat { grid-template-columns: 106px 1fr 84px; gap: 8px; }
    .tile .valor { font-size: 21px; }
  }
"""


def montar_html(
    transacoes: list[dict],
    saldo: float | None,
    gastos_fixos_por_mes: dict[str, list[dict]] | None = None,
    caixa_externo: float = 0.0,
    variaveis_manuais_por_mes: dict[str, list[dict]] | None = None,
    orcamento_por_grande: dict[str, float] | None = None,
    ultima_sincronizacao: str | None = None,
) -> str:
    """Monta o HTML inteiro do painel.

    Ordem das seções (decidida na revisão de layout de 2026-07-26, modo
    "Operate": a tela existe pra o usuário DECIDIR, não pra impressionar):
    1. Veredito + indicadores de hoje -- responde "dá pra cobrir?" antes
       de qualquer gráfico;
    2. Saldo projetado -- a trajetória, e em que mês o caixa vira;
    3. Para onde vai o dinheiro (rosca) x Fixas contra Variáveis;
    4. Orçamento x real do mês corrente;
    5. Panorama mês a mês (o detalhe editável, onde o usuário age);
    6. Diagnóstico e histórico, recolhidos.
    """
    meses = agregar_por_mes(transacoes)
    categorias = agregar_categorias_despesa(transacoes)
    total_receita = sum(v["receita"] for v in meses.values())
    total_despesa = sum(v["despesa"] for v in meses.values())
    resultado = total_receita - total_despesa

    # Reserva manual (contas fora do Pluggy, ver /caixa-externo) soma no
    # caixa geral E no "Caixa no início do mês" de cada card mensal.
    saldo_com_externo = None if saldo is None else saldo + caixa_externo

    panorama = construir_panorama_mensal(transacoes, saldo_com_externo, gastos_fixos_por_mes, variaveis_manuais_por_mes)
    mes_atual = datetime.now().strftime("%Y-%m")
    itens_mes_atual = gastos_fixos_por_mes.get(mes_atual) if gastos_fixos_por_mes else None
    total_fixo = sum(i["valor"] for i in itens_mes_atual) if itens_mes_atual else total_fixo_mensal()

    salario_medio = salario_medio_recente(transacoes)
    sobra_fixa = None if salario_medio is None else salario_medio - total_fixo
    renda_extra_necessaria = max(0.0, -sobra_fixa) if sobra_fixa is not None else None

    gasto_cartao_mes = gasto_cartao_por_mes(transacoes)
    max_gasto_cartao_mes = max(gasto_cartao_mes.values(), default=1) or 1

    max_valor_mes = max(
        (max(v["receita"], v["despesa"]) for v in meses.values()), default=1
    ) or 1
    max_valor_cat = max((v for _, v in categorias), default=1) or 1

    def mes_label(chave: str) -> str:
        dt = datetime.strptime(chave, "%Y-%m")
        return f"{MESES_PT[dt.month - 1]}/{dt.strftime('%y')}"

    barras_mensais = ""
    linhas_tabela = ""
    for mes, valores in meses.items():
        h_receita = round(valores["receita"] / max_valor_mes * 160, 1)
        h_despesa = round(valores["despesa"] / max_valor_mes * 160, 1)
        barras_mensais += f"""
        <div class="grupo-mes">
          <div class="barras">
            <div class="barra receita" style="height:{h_receita}px"
                 data-tip="<b>{mes_label(mes)}</b><br>Receitas: {fmt_brl(valores['receita'])}"></div>
            <div class="barra despesa" style="height:{h_despesa}px"
                 data-tip="<b>{mes_label(mes)}</b><br>Despesas: {fmt_brl(valores['despesa'])}"></div>
          </div>
          <span class="mes-label">{mes_label(mes)}</span>
        </div>"""
        linhas_tabela += f"""
        <tr>
          <td>{mes_label(mes)}</td>
          <td class="num">{fmt_brl(valores['receita'])}</td>
          <td class="num">{fmt_brl(valores['despesa'])}</td>
          <td class="num">{fmt_brl(valores['receita'] - valores['despesa'])}</td>
        </tr>"""

    barras_categorias = ""
    for cat, valor in categorias:
        largura = round(valor / max_valor_cat * 100, 1)
        nome_pt = traduzir_categoria(cat)
        cor = var_serie(grande_categoria(nome_pt))
        barras_categorias += f"""
        <div class="linha-cat" style="margin-bottom:9px;" data-tip="{nome_pt}: {fmt_brl(valor)}">
          <span class="cat-label"><span class="ponto-cat" style="background:{cor}"></span>{nome_pt}</span>
          <div class="cat-track"><div class="cat-barra" style="width:{largura}%;background:{cor}"></div></div>
          <span class="cat-valor">{fmt_brl(valor)}</span>
        </div>"""

    barras_gasto_cartao = ""
    for mes, valor in gasto_cartao_mes.items():
        altura = round(valor / max_gasto_cartao_mes * 160, 1)
        barras_gasto_cartao += f"""
        <div class="grupo-mes">
          <div class="barras">
            <div class="barra despesa" style="height:{altura}px"
                 data-tip="<b>{mes_label(mes)}</b><br>Comprado no cartão: {fmt_brl(valor)}"></div>
          </div>
          <span class="mes-label">{mes_label(mes)}</span>
        </div>"""

    painel_meses_html = "".join(
        render_mes_panorama(linha, aberto=(i == 0)) for i, linha in enumerate(panorama)
    )

    # --- Gráficos ----------------------------------------------------
    primeiro = panorama[0] if panorama else None
    itens_primeiro = (primeiro["fixas_itens"] + primeiro["variaveis_itens"]) if primeiro else []
    realizado_mes = realizado_por_grande_categoria(transacoes, mes_atual)

    def como_itens(totais: dict[str, float]) -> list[dict]:
        return [{"categoria_grande": g, "valor": v} for g, v in totais.items()]

    periodos_rosca = [
        (
            "previsto",
            f"{mes_label(primeiro['mes'])} previsto" if primeiro else "Previsto",
            itens_primeiro,
        ),
        ("realizado", f"{mes_label(mes_atual)} até hoje", como_itens(realizado_mes)),
        ("media", "Média de 3 meses", como_itens(media_por_grande_categoria(transacoes))),
    ]
    rosca_html = graficos.rosca_com_filtro(periodos_rosca)
    colunas_html = graficos.colunas_fixas_variaveis(panorama, mes_label)
    linha_saldo_html = graficos.linha_saldo_projetado(panorama, mes_label)
    orcamento_html = graficos.orcamento_x_real(realizado_mes, orcamento_por_grande or {})
    parcelas_html = graficos.parcelas_terminando(parcelas_que_terminam(panorama), mes_label)

    comparativo = comparativo_mes_anterior(transacoes, datetime.now())
    if comparativo["variacao"] is None:
        chip_comparativo = ""
    else:
        subiu = comparativo["variacao"] > 0
        chip_comparativo = (
            f'<span class="delta {"critical" if subiu else "good"}">'
            f'<span aria-hidden="true">{"▲" if subiu else "▼"}</span>'
            f'{abs(comparativo["variacao"]):.0f}%</span>'
        )
    nota_comparativo = (
        f'{fmt_brl(comparativo["anterior"])} no mesmo período de {mes_label(comparativo["mes_anterior"])}'
        if comparativo["anterior"] else "sem base de comparação no mês anterior"
    )

    # --- Veredito: a resposta em uma frase --------------------------
    # O veredito é o herói da página: o número que decide vive DENTRO da
    # frase, não numa caixa concorrendo com outras quatro. Os indicadores
    # viraram uma faixa discreta logo abaixo -- cinco caixas do mesmo
    # tamanho competindo era o padrão de dashboard genérico, e nenhuma
    # delas respondia sozinha "e aí, dá ou não dá?".
    primeiro_negativo = next((l for l in panorama if l["saldo_final"] is not None and l["saldo_final"] < 0), None)
    if not panorama or panorama[0]["saldo_final"] is None:
        veredito_classe = "neutro"
        veredito_titulo = "Sem saldo da conta, não dá pra projetar."
        veredito_numero = ""
        veredito_apoio = "Rode a sincronização com o banco pra o painel voltar a responder."
    elif primeiro_negativo is None:
        veredito_classe = "good"
        veredito_numero = fmt_brl(panorama[0]["saldo_final"])
        veredito_titulo = f"sobram em {mes_label(panorama[0]['mes'])}"
        veredito_apoio = (
            f"O caixa cobre todos os {len(panorama)} meses projetados: no fim de "
            f"{mes_label(panorama[-1]['mes'])} ainda restam {fmt_brl(panorama[-1]['saldo_final'])}, "
            f"mantendo a entrada média de {fmt_brl(panorama[0]['entrada'])} por mês."
        )
    else:
        veredito_classe = "critical"
        veredito_numero = f"−{fmt_brl(abs(primeiro_negativo['saldo_final']))}"
        veredito_titulo = f"faltam em {mes_label(primeiro_negativo['mes'])}"
        veredito_apoio = (
            "O caixa vira negativo nesse mês. Pra evitar: cortar variáveis, antecipar renda extra "
            "ou renegociar um fixo — os três aparecem abertos no card do mês."
        )

    resultado_classe = "good" if resultado >= 0 else "critical"
    sobra_fixa_classe = "good" if (sobra_fixa or 0) >= 0 else "critical"
    caixa_classe = "good" if (saldo_com_externo or 0) >= 0 else "critical"
    nota_caixa = "conta corrente" + (f" + {fmt_brl(caixa_externo)} de caixa externo" if caixa_externo else "")
    saldo_final_1 = primeiro["saldo_final"] if primeiro else None
    classe_saldo_1 = "good" if (saldo_final_1 or 0) >= 0 else "critical"

    # Selo de dado velho: o painel projeta seis meses em cima do saldo de
    # hoje. Se o job de sincronização parar, a única pista era o
    # "Atualizado em" do cabeçalho -- que marca quando a PÁGINA foi
    # montada, não quando os dados chegaram do banco. Duas coisas
    # diferentes que pareciam a mesma.
    selo_sync = ""
    if ultima_sincronizacao:
        try:
            quando = datetime.fromisoformat(ultima_sincronizacao)
        except ValueError:
            quando = None
        if quando is not None:
            horas = (datetime.now() - quando).total_seconds() / 3600
            if horas >= 36:
                dias = int(horas // 24)
                quanto = f"{dias} dia{'s' if dias > 1 else ''}" if dias else f"{int(horas)} horas"
                selo_sync = (
                    f'<p class="selo-sync"><span class="badge warn"><span aria-hidden="true">!</span>'
                    f'Dados de {quanto} atrás</span> A última sincronização com o banco foi em '
                    f'{quando.strftime("%d/%m às %H:%M")}. Números de hoje podem estar de fora.</p>'
                )

    acoes_topo = (
        '<a class="botao" href="/orcamento">Orçamento</a>'
        '<a class="botao" href="/caixa-externo">Caixa externo</a>'
    )
    cabecalho_html = ui.cabecalho(
        "Fluxo de caixa",
        f"Atualizado em {datetime.now().strftime('%d/%m/%Y às %H:%M')}",
        acoes_topo,
    )

    corpo = f"""
  <section class="heroi {veredito_classe}">
    <p class="heroi-frase">
      <span class="heroi-numero">{veredito_numero}</span>
      <span class="heroi-titulo">{veredito_titulo}</span>
    </p>
    <p class="heroi-apoio">{veredito_apoio}</p>
    {selo_sync}
  </section>

  <div class="faixa-indicadores">
    <div class="indicador">
      <span class="rotulo">Caixa hoje</span>
      <span class="valor {caixa_classe}">{fmt_brl_ou_indisponivel(saldo_com_externo)}</span>
      <span class="nota">{nota_caixa}</span>
    </div>
    <div class="indicador">
      <span class="rotulo">Entrada por mês</span>
      <span class="valor">{fmt_brl(primeiro['entrada']) if primeiro else '—'}</span>
      <span class="nota">média dos meses fechados</span>
    </div>
    <div class="indicador">
      <span class="rotulo">A pagar em {mes_label(primeiro['mes']) if primeiro else '—'}</span>
      <span class="valor">{fmt_brl(primeiro['necessario']) if primeiro else '—'}</span>
      <span class="nota">{f"{fmt_brl(primeiro['fixas_total'])} fixas + {fmt_brl(primeiro['variaveis_total'])} variáveis" if primeiro else ''}</span>
    </div>
    <div class="indicador">
      <span class="rotulo">Gasto em {mes_label(mes_atual)} até hoje</span>
      <span class="valor">{fmt_brl(comparativo['atual'])} {chip_comparativo}</span>
      <span class="nota">{nota_comparativo}</span>
    </div>
  </div>

  <section class="secao"><h2>Trajetória do caixa</h2></section>
  <div class="card">
    <h3>Saldo projetado no fim de cada mês</h3>
    <p class="ajuda">Parte do caixa de hoje e desconta, mês a mês, as despesas já comprometidas
       contra a entrada média. A faixa vermelha é o território negativo.</p>
    {linha_saldo_html}
  </div>

  <section class="secao"><h2>Para onde vai o dinheiro</h2></section>
  <div class="grade-2">
    <div class="card">
      <h3>Composição das despesas</h3>
      <p class="ajuda">Fixas e variáveis somadas, agrupadas por categoria. Troque entre o que
         está previsto, o que já foi gasto neste mês e a média dos últimos três.</p>
      {rosca_html}
    </div>
    <div class="card">
      <h3>Fixas contra variáveis</h3>
      <p class="ajuda">Fixa só muda se você cancelar ou renegociar. Variável é o que dá pra cortar
         no mês seguinte — quanto maior a fatia laranja, mais manobra você tem.</p>
      {colunas_html}
    </div>
  </div>

  <div class="card">
    <h3>Quando a conta afrouxa</h3>
    <p class="ajuda">Compras parceladas cuja última parcela cai dentro dos meses projetados.
       Cada uma é um valor que some do compromisso mensal a partir do mês seguinte.</p>
    {parcelas_html}
  </div>

  <section class="secao"><h2>Orçamento do mês corrente</h2></section>
  <div class="card">
    <h3>Quanto de cada teto já foi gasto em {mes_label(mes_atual)}</h3>
    <p class="ajuda">Gasto real do mês pela data da compra, contra o teto definido em
       <a href="/orcamento">Orçamento</a>.</p>
    {orcamento_html}
  </div>

  <section class="secao">
    <h2>Panorama mês a mês</h2>
    <p>Cada card abre a lista completa do mês. É aqui que você edita: renomear uma compra,
       promover um gasto a fixo, incluir um pix que não passa pelo banco.</p>
  </section>
  {painel_meses_html}

  <section class="secao"><h2>Diagnóstico</h2></section>
  <div class="bloco">
    <h3>Salário contra gastos fixos</h3>
    <p class="ajuda">A conta fecha sem depender da renda extra?</p>
    <div class="faixa-indicadores">
      <div class="indicador"><span class="rotulo">Salário médio</span><span class="valor">{fmt_brl_ou_indisponivel(salario_medio)}</span><span class="nota">meses fechados</span></div>
      <div class="indicador"><span class="rotulo">Total de gastos fixos</span><span class="valor">{fmt_brl(total_fixo)}</span><span class="nota">no mês corrente</span></div>
      <div class="indicador"><span class="rotulo">Sobra fixa</span><span class="valor {sobra_fixa_classe}">{fmt_brl_ou_indisponivel(sobra_fixa)}</span><span class="nota">antes do variável</span></div>
      <div class="indicador"><span class="rotulo">Renda extra mínima</span><span class="valor">{fmt_brl_ou_indisponivel(renda_extra_necessaria)}</span><span class="nota">pra fechar sem o extra</span></div>
    </div>
  </div>

  <div class="bloco">
    <h3>Gasto no cartão por mês</h3>
    <p class="ajuda">Total comprado no cartão em cada mês, pela data da compra — a pergunta aqui
       é só uma: está diminuindo?</p>
    <div class="chart-mensal">{barras_gasto_cartao}
    </div>
  </div>

  <details class="bloco historico">
    <summary>Histórico completo — receitas, despesas e categorias desde o começo</summary>
    <div class="faixa-indicadores" style="margin-top:18px;">
      <div class="indicador"><span class="rotulo">Receitas no período</span><span class="valor">{fmt_brl(total_receita)}</span></div>
      <div class="indicador"><span class="rotulo">Despesas no período</span><span class="valor">{fmt_brl(total_despesa)}</span></div>
      <div class="indicador"><span class="rotulo">Resultado líquido</span><span class="valor {resultado_classe}">{fmt_brl(resultado)}</span></div>
    </div>
    <div class="sub-bloco">
      <h3>Receitas e despesas por mês</h3>
      <div class="legenda">
        <span><span class="chave" style="background:var(--receita)"></span>Receitas</span>
        <span><span class="chave" style="background:var(--despesa)"></span>Despesas</span>
      </div>
      <div class="chart-mensal">{barras_mensais}
      </div>
    </div>
    <div class="sub-bloco">
      <h3>Maiores categorias de despesa do período</h3>
      {barras_categorias}
    </div>
    <div class="sub-bloco tabela-rolagem">
      <table>
        <thead><tr><th>Mês</th><th class="num">Receitas</th><th class="num">Despesas</th><th class="num">Líquido</th></tr></thead>
        <tbody>{linhas_tabela}
        </tbody>
      </table>
    </div>
  </details>
"""
    return ui.documento(
        "Fluxo de caixa",
        cabecalho_html,
        corpo,
        css_extra=graficos.CSS_GRAFICOS + CSS_DASHBOARD,
    )


def main():
    transacoes, saldo = carregar_transacoes()
    html = montar_html(transacoes, saldo)
    OUT_PATH.parent.mkdir(exist_ok=True)
    OUT_PATH.write_text(html, encoding="utf-8")
    print(f"Dashboard gerado em {OUT_PATH}")


if __name__ == "__main__":
    main()
