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
from gastos_fixos import GASTOS_FIXOS, valor_planejamento, total_fixo_mensal, total_fixo_pix, total_fixo_cartao
from normalizacao import traduzir_categoria, normalizar_transacoes_pluggy, CATEGORIA_RENDA_EXTRA

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


def transacoes_mes_atual(transacoes: list[dict]) -> list[dict]:
    mes_atual = datetime.now().strftime("%Y-%m")
    return [t for t in transacoes if t["date"][:7] == mes_atual]


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


def construir_panorama_mensal(transacoes: list[dict], saldo: float | None, meses_futuros: int = 5) -> list[dict]:
    """Monta o painel mês a mês: mês atual + próximos `meses_futuros`, cada
    um com fatura de cartão (real no mês atual, projetada nos seguintes),
    fixos, entrada prevista (salário + renda extra) e um saldo de caixa
    projetado rodando de mês a mês, pra responder "dá pra cobrir?".

    A API guarda um "retrato" da parcela em cada fatura já fechada (ex.: uma
    compra em 10x aparece uma vez por mês, incrementando installmentNumber) --
    por isso a projeção parte só das parcelas que estão na fatura ATUAL
    (`bill == mes_atual`), não de todo o histórico, senão cada retrato
    passado geraria sua própria projeção e duplicaria os valores futuros.

    No mês atual, os fixos pagos no cartão já estão dentro da fatura real
    (não soma de novo). Nos meses futuros, a fatura só tem as parcelas já
    parceladas -- os fixos recorrentes do cartão (Vivo, mercado etc., que
    não são parcelamento) ainda vão ser cobrados, então entram por fora.
    """
    mes_atual = datetime.now().strftime("%Y-%m")
    total_pix = total_fixo_pix()
    total_cartao_fixo = total_fixo_cartao()
    salario_medio = salario_medio_recente(transacoes) or 0.0
    renda_extra_media = renda_extra_media_recente(transacoes) or 0.0
    entrada_prevista = salario_medio + renda_extra_media

    fatura_atual = 0.0
    detalhe_atual: list[dict] = []
    projecao: dict = defaultdict(float)
    detalhe_futuro: dict = defaultdict(list)

    for t in transacoes:
        meta = t.get("creditCardMetadata")
        if not meta or t.get("type") != "DEBIT":
            continue
        atual_parc, total_parc, bill = meta.get("installmentNumber"), meta.get("totalInstallments"), meta.get("billForecastDate")
        if atual_parc is None or total_parc is None or bill != mes_atual:
            continue
        valor = abs(t["amount"])
        fatura_atual += valor
        item_detalhe = {
            "id": t.get("id"),
            "descricao": t.get("description") or t.get("descriptionRaw") or "—",
            "categoria": traduzir_categoria(t.get("category") or "Outros"),
            "valor": valor,
            "parcela": f"{atual_parc}/{total_parc}" if total_parc > 1 else None,
        }
        detalhe_atual.append(item_detalhe)
        restantes = total_parc - atual_parc
        for i in range(1, min(restantes, meses_futuros) + 1):
            mes_futuro = _mes_seguinte(bill, i)
            projecao[mes_futuro] += valor
            detalhe_futuro[mes_futuro].append({
                **item_detalhe,
                "parcela": f"{atual_parc + i}/{total_parc}",
            })

    meses_ordenados = [mes_atual] + [_mes_seguinte(mes_atual, i) for i in range(1, meses_futuros + 1)]

    linhas = []
    caixa_inicio = saldo
    for i, mes in enumerate(meses_ordenados):
        if i == 0:
            fatura_mes = fatura_atual  # já inclui os fixos do cartão reais deste ciclo
            fixos_cartao_recorrente = total_cartao_fixo  # só exibido, não somado nesse mês
            detalhe = detalhe_atual
        else:
            fatura_mes = projecao.get(mes, 0.0) + total_cartao_fixo  # parcelas + recorrente assumido
            fixos_cartao_recorrente = total_cartao_fixo
            detalhe = detalhe_futuro.get(mes, [])

        # Total necessário = fixos no Pix + fatura do cartão. O fixo recorrente
        # do cartão NÃO entra separado -- já está embutido na fatura (real no
        # mês atual, projetado por fora nos futuros); somar de novo duplicaria.
        necessario = fatura_mes + total_pix
        saldo_final = None if caixa_inicio is None else caixa_inicio + entrada_prevista - necessario
        cobre = None if caixa_inicio is None else (caixa_inicio + entrada_prevista) >= necessario

        linhas.append({
            "mes": mes,
            "fatura": fatura_mes,
            "fixos_cartao_recorrente": fixos_cartao_recorrente,
            "fixos_pix": total_pix,
            "necessario": necessario,
            "salario_medio": salario_medio,
            "renda_extra_media": renda_extra_media,
            "entrada": entrada_prevista,
            "caixa_inicio": caixa_inicio,
            "saldo_final": saldo_final,
            "cobre": cobre,
            "detalhe": sorted(detalhe, key=lambda d: -d["valor"]),
        })
        caixa_inicio = saldo_final

    return linhas


def _para_detalhe(transacoes_like: list[dict]) -> list[dict]:
    """Converte transações cruas pro formato usado nas seções expansíveis
    (descricao/categoria/valor/parcela)."""
    return [{
        "id": t.get("id"),
        "descricao": t.get("description") or t.get("descriptionRaw") or "—",
        "categoria": traduzir_categoria(t.get("category") or "Outros"),
        "valor": abs(t["amount"]),
        "parcela": None,
    } for t in transacoes_like]


def _categorias_do_detalhe(detalhe: list[dict]) -> list[tuple[str, float]]:
    agregados: dict = defaultdict(float)
    for d in detalhe:
        agregados[d["categoria"]] += d["valor"]
    return sorted(agregados.items(), key=lambda kv: -kv[1])


def _agrupar_por_categoria(detalhe: list[dict]) -> dict:
    agrupado: dict = defaultdict(list)
    for d in detalhe:
        agrupado[d["categoria"]].append(d)
    return dict(agrupado)


def fmt_brl(valor: float) -> str:
    s = f"{valor:,.2f}"
    s = s.replace(",", "_").replace(".", ",").replace("_", ".")
    return f"R$ {s}"


def fmt_brl_ou_indisponivel(valor: float | None) -> str:
    return "Indisponível" if valor is None else fmt_brl(valor)


def render_categorias_expansivel(detalhe: list[dict]) -> str:
    """Uma linha por categoria (label + barra + valor), cada uma um
    <details> que abre pra listar os lançamentos daquela categoria."""
    categorias_valores = _categorias_do_detalhe(detalhe)
    if not categorias_valores:
        return '<p class="subtitulo">Nenhum lançamento.</p>'
    por_categoria = _agrupar_por_categoria(detalhe)
    max_valor = max(v for _, v in categorias_valores) or 1

    linhas = ""
    for cat, valor in categorias_valores:
        largura = round(valor / max_valor * 100, 1)
        itens = sorted(por_categoria[cat], key=lambda i: -i["valor"])
        linhas_item = "".join(
            f"""<tr><td>{i['descricao']}{f" ({i['parcela']})" if i.get('parcela') else ""}"""
            f"""{f' <a class="editar-link" href="/transacao/{i["id"]}/editar">✎</a>' if i.get('id') else ""}</td>"""
            f"""<td class="num">{fmt_brl(i['valor'])}</td></tr>"""
            for i in itens
        )
        linhas += f"""
        <details class="cat-detalhe">
          <summary>
            <div class="linha-cat">
              <span class="cat-label">{cat}</span>
              <div class="cat-track"><div class="cat-barra" style="width:{largura}%"></div></div>
              <span class="cat-valor">{fmt_brl(valor)}</span>
            </div>
          </summary>
          <table class="tabela-detalhe"><tbody>{linhas_item}</tbody></table>
        </details>"""
    return linhas


def render_fixos_detalhe(titulo: str, forma: str, card: bool = False) -> str:
    """Lista expansível dos gastos fixos de uma forma (pix/cartao). `card`
    controla se vem embrulhado como card de página (uso solo) ou como
    details simples pra encaixar dentro de outro card (painel do mês)."""
    itens = [i for i in GASTOS_FIXOS if i.get("forma") == forma]
    total = sum(valor_planejamento(i) for i in itens)
    linhas = ""
    for item in itens:
        valor = valor_planejamento(item)
        faixa = ""
        if "valor_min" in item:
            faixa = f" (faixa {fmt_brl(item['valor_min'])}–{fmt_brl(item['valor_max'])})"
        linhas += f"""<tr><td>{item['nome']}{faixa}</td><td class="num">{fmt_brl(valor)}</td></tr>"""
    classe = "card" if card else "detalhe-fatura"
    return f"""
  <details class="{classe}">
    <summary>
      <div class="linha-cat" style="grid-template-columns: 1fr 90px;">
        <span class="cat-label" style="font-size:14px;font-weight:600;color:var(--text-primary);">{titulo}</span>
        <span class="cat-valor" style="font-size:14px;">{fmt_brl(total)}</span>
      </div>
    </summary>
    <table class="tabela-detalhe">
      <tbody>{linhas}
        <tr><td><strong>Total</strong></td><td class="num"><strong>{fmt_brl(total)}</strong></td></tr>
      </tbody>
    </table>
  </details>"""


def render_entrada_detalhe(linha: dict) -> str:
    return f"""
  <details class="detalhe-fatura">
    <summary>
      <div class="linha-cat" style="grid-template-columns: 1fr 90px;">
        <span class="cat-label" style="font-size:14px;font-weight:600;color:var(--text-primary);">Entrada prevista</span>
        <span class="cat-valor" style="font-size:14px;">{fmt_brl(linha['entrada'])}</span>
      </div>
    </summary>
    <table class="tabela-detalhe">
      <tbody>
        <tr><td>Salário médio (últimos meses fechados)</td><td class="num">{fmt_brl(linha['salario_medio'])}</td></tr>
        <tr><td>Renda extra média (assessoria)</td><td class="num">{fmt_brl(linha['renda_extra_media'])}</td></tr>
        <tr><td><strong>Total</strong></td><td class="num"><strong>{fmt_brl(linha['entrada'])}</strong></td></tr>
      </tbody>
    </table>
  </details>"""


def render_mes_panorama(linha: dict, aberto: bool) -> str:
    cobre = linha["cobre"]
    badge = ""
    if cobre is not None:
        badge_texto = "Cobre" if cobre else "Não cobre"
        badge_classe = "good" if cobre else "critical"
        badge = f'<span class="badge {badge_classe}">{badge_texto}</span>'

    detalhe_html = render_categorias_expansivel(linha["detalhe"])
    saldo_final_classe = "good" if (linha["saldo_final"] or 0) >= 0 else "critical"
    open_attr = " open" if aberto else ""
    fixos_pix_html = render_fixos_detalhe("Fixos no Pix", "pix")
    fixos_cartao_html = render_fixos_detalhe("Fixos no cartão recorrente", "cartao")
    entrada_html = render_entrada_detalhe(linha)

    return f"""
  <details class="card mes-panorama"{open_attr}>
    <summary>
      <span class="mes-panorama-titulo">{MESES_PT[int(linha['mes'][5:7]) - 1]}/{linha['mes'][2:4]}</span>
      {badge}
    </summary>
    <div class="tiles" style="margin-top:14px;">
      <div class="tile"><div class="label">Total necessário</div><div class="valor">{fmt_brl(linha['necessario'])}</div></div>
      <div class="tile"><div class="label">Caixa no início do mês</div><div class="valor">{fmt_brl_ou_indisponivel(linha['caixa_inicio'])}</div></div>
      <div class="tile"><div class="label">Saldo projetado no fim do mês</div><div class="valor {saldo_final_classe}">{fmt_brl_ou_indisponivel(linha['saldo_final'])}</div></div>
    </div>
    <div style="margin-top:14px;">
      {fixos_pix_html}
      {fixos_cartao_html}
      <details class="detalhe-fatura">
        <summary>
          <div class="linha-cat" style="grid-template-columns: 1fr 90px;">
            <span class="cat-label" style="font-size:14px;font-weight:600;color:var(--text-primary);">Fatura do cartão</span>
            <span class="cat-valor" style="font-size:14px;">{fmt_brl(linha['fatura'])}</span>
          </div>
        </summary>
        <div style="margin-top:10px;">{detalhe_html}</div>
      </details>
      {entrada_html}
    </div>
  </details>"""


def montar_html(transacoes: list[dict], saldo: float | None) -> str:
    meses = agregar_por_mes(transacoes)
    categorias = agregar_categorias_despesa(transacoes)
    total_receita = sum(v["receita"] for v in meses.values())
    total_despesa = sum(v["despesa"] for v in meses.values())
    resultado = total_receita - total_despesa

    panorama = construir_panorama_mensal(transacoes, saldo)
    total_fixo = total_fixo_mensal()

    salario_medio = salario_medio_recente(transacoes)
    renda_extra_media = renda_extra_media_recente(transacoes)
    sobra_fixa = None if salario_medio is None else salario_medio - total_fixo
    renda_extra_necessaria = max(0.0, -sobra_fixa) if sobra_fixa is not None else None
    sobra_fixa_com_extra = None
    if salario_medio is not None:
        sobra_fixa_com_extra = salario_medio + (renda_extra_media or 0.0) - total_fixo

    gasto_cartao_mes = gasto_cartao_por_mes(transacoes)
    max_gasto_cartao_mes = max(gasto_cartao_mes.values(), default=1) or 1

    mes_atual_txs = transacoes_mes_atual(transacoes)
    compras_mes = sorted(
        (t for t in mes_atual_txs if t["amount"] < 0),
        key=lambda t: t["date"], reverse=True,
    )
    total_gasto_mes = sum(abs(t["amount"]) for t in compras_mes)
    detalhe_mes_atual = _para_detalhe(compras_mes)

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
                 data-tip="Receitas {mes_label(mes)}: {fmt_brl(valores['receita'])}"></div>
            <div class="barra despesa" style="height:{h_despesa}px"
                 data-tip="Despesas {mes_label(mes)}: {fmt_brl(valores['despesa'])}"></div>
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
        barras_categorias += f"""
        <div class="linha-cat">
          <span class="cat-label">{traduzir_categoria(cat)}</span>
          <div class="cat-track">
            <div class="cat-barra" style="width:{largura}%" data-tip="{fmt_brl(valor)}"></div>
          </div>
          <span class="cat-valor">{fmt_brl(valor)}</span>
        </div>"""

    categorias_mes_html = render_categorias_expansivel(detalhe_mes_atual)

    barras_gasto_cartao = ""
    for mes, valor in gasto_cartao_mes.items():
        altura = round(valor / max_gasto_cartao_mes * 160, 1)
        barras_gasto_cartao += f"""
        <div class="grupo-mes">
          <div class="barras">
            <div class="barra despesa" style="height:{altura}px"
                 data-tip="Gasto no cartão {mes_label(mes)}: {fmt_brl(valor)}"></div>
          </div>
          <span class="mes-label">{mes_label(mes)}</span>
        </div>"""

    painel_meses_html = "".join(
        render_mes_panorama(linha, aberto=(i == 0)) for i, linha in enumerate(panorama)
    )

    resultado_classe = "good" if resultado >= 0 else "critical"
    sobra_fixa_classe = "good" if (sobra_fixa or 0) >= 0 else "critical"
    sobra_fixa_com_extra_classe = "good" if (sobra_fixa_com_extra or 0) >= 0 else "critical"

    return f"""<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<title>Dashboard de Fluxo de Caixa</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {{
    color-scheme: light;
    --surface-1: #fcfcfb; --page: #f9f9f7;
    --text-primary: #0b0b0b; --text-secondary: #52514e; --text-muted: #898781;
    --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10);
    --receita: {COR_RECEITA_LIGHT}; --despesa: {COR_DESPESA_LIGHT};
    --good: #0ca30c; --critical: #d03b3b;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:where(:not([data-theme="light"])) {{
      color-scheme: dark;
      --surface-1: #1a1a19; --page: #0d0d0d;
      --text-primary: #ffffff; --text-secondary: #c3c2b7; --text-muted: #898781;
      --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
      --receita: {COR_RECEITA_DARK}; --despesa: {COR_DESPESA_DARK};
      --good: #0ca30c; --critical: #e66767;
    }}
  }}
  :root[data-theme="dark"] {{
    color-scheme: dark;
    --surface-1: #1a1a19; --page: #0d0d0d;
    --text-primary: #ffffff; --text-secondary: #c3c2b7; --text-muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
    --receita: {COR_RECEITA_DARK}; --despesa: {COR_DESPESA_DARK};
    --good: #0ca30c; --critical: #e66767;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 32px 24px 64px; background: var(--page); color: var(--text-primary);
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  }}
  .wrap {{ max-width: 960px; margin: 0 auto; }}
  header {{ display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 24px; }}
  h1 {{ font-size: 20px; margin: 0; }}
  .subtitulo {{ color: var(--text-secondary); font-size: 13px; }}
  #toggle-tema {{
    border: 1px solid var(--border); background: var(--surface-1); color: var(--text-secondary);
    border-radius: 6px; padding: 6px 10px; font-size: 12px; cursor: pointer;
  }}
  .tiles {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 28px; }}
  .tile {{ background: var(--surface-1); border: 1px solid var(--border); border-radius: 10px; padding: 16px; }}
  .tile .label {{ color: var(--text-secondary); font-size: 12px; margin-bottom: 6px; }}
  .tile .valor {{ font-size: 22px; font-weight: 600; }}
  .tile .valor.good {{ color: var(--good); }}
  .tile .valor.critical {{ color: var(--critical); }}
  .card {{ background: var(--surface-1); border: 1px solid var(--border); border-radius: 10px; padding: 20px; margin-bottom: 20px; }}
  .card h2 {{ font-size: 14px; margin: 0 0 16px; color: var(--text-secondary); font-weight: 600; }}
  .legenda {{ display: flex; gap: 16px; margin-bottom: 12px; font-size: 12px; color: var(--text-secondary); }}
  .legenda span {{ display: inline-flex; align-items: center; gap: 6px; }}
  .dot {{ width: 8px; height: 8px; border-radius: 50%; display: inline-block; }}
  .dot.receita {{ background: var(--receita); }}
  .dot.despesa {{ background: var(--despesa); }}
  .chart-mensal {{
    display: flex; align-items: flex-end; gap: 18px; height: 180px; padding-top: 10px;
    border-bottom: 1px solid var(--axis); overflow-x: auto;
  }}
  .grupo-mes {{ display: flex; flex-direction: column; align-items: center; gap: 8px; min-width: 44px; }}
  .barras {{ display: flex; align-items: flex-end; gap: 2px; height: 160px; }}
  .barra {{ width: 18px; border-radius: 4px 4px 0 0; cursor: pointer; }}
  .barra.receita {{ background: var(--receita); }}
  .barra.despesa {{ background: var(--despesa); }}
  .mes-label {{ font-size: 11px; color: var(--text-muted); }}
  .linha-cat {{ display: grid; grid-template-columns: 110px 1fr 90px; align-items: center; gap: 10px; margin-bottom: 10px; }}
  .cat-label {{ font-size: 13px; color: var(--text-secondary); }}
  .cat-track {{ background: var(--grid); border-radius: 4px; height: 14px; }}
  .cat-barra {{ background: var(--despesa); height: 14px; border-radius: 4px; cursor: pointer; }}
  .cat-valor {{ font-size: 12px; color: var(--text-secondary); text-align: right; font-variant-numeric: tabular-nums; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--grid); }}
  td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  summary {{ cursor: pointer; color: var(--text-secondary); font-size: 13px; margin-bottom: 8px; }}
  #tooltip {{
    position: fixed; display: none; background: var(--text-primary); color: var(--surface-1);
    font-size: 12px; padding: 4px 8px; border-radius: 4px; pointer-events: none; z-index: 10;
  }}
  .secao-titulo {{ font-size: 14px; margin: 28px 0 12px; color: var(--text-secondary); font-weight: 600; }}
  .badge {{ font-size: 11px; padding: 3px 8px; border-radius: 999px; font-weight: 600; }}
  .badge.good {{ background: rgba(12,163,12,0.15); color: var(--good); }}
  .badge.critical {{ background: rgba(211,59,59,0.15); color: var(--critical); }}
  .mes-panorama summary {{ display: flex; justify-content: space-between; align-items: center; list-style: revert; }}
  .mes-panorama-titulo {{ font-size: 15px; font-weight: 600; color: var(--text-primary); }}
  .detalhe-fatura summary {{ font-size: 12px; }}
  .cat-detalhe {{ margin-bottom: 4px; }}
  .cat-detalhe summary {{ margin-bottom: 4px; list-style: revert; }}
  .cat-detalhe summary::marker {{ font-size: 11px; }}
  .cat-detalhe .linha-cat {{ margin-bottom: 0; }}
  .tabela-detalhe {{ margin: 4px 0 12px 18px; width: calc(100% - 18px); }}
  details.card summary {{ list-style: revert; }}
  .editar-link {{ color: var(--text-muted); text-decoration: none; font-size: 11px; }}
  .editar-link:hover {{ color: var(--text-primary); }}
  .form-edicao {{ max-width: 420px; }}
  .form-edicao label {{ display: block; font-size: 12px; color: var(--text-secondary); margin: 12px 0 4px; }}
  .form-edicao input {{ width: 100%; padding: 8px 10px; border-radius: 6px; border: 1px solid var(--border); background: var(--page); color: var(--text-primary); font-size: 14px; }}
  .form-edicao button {{ margin-top: 16px; padding: 8px 16px; border-radius: 6px; border: none; background: var(--receita); color: #fff; font-size: 13px; cursor: pointer; }}
  .voltar {{ color: var(--text-secondary); font-size: 13px; text-decoration: none; }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div>
      <h1>Dashboard de Fluxo de Caixa</h1>
      <div class="subtitulo">Gerado em {datetime.now().strftime('%d/%m/%Y %H:%M')}</div>
    </div>
    <button id="toggle-tema" onclick="alternarTema()">Alternar tema</button>
  </header>

  <div class="tiles">
    <div class="tile"><div class="label">Caixa disponível</div><div class="valor">{fmt_brl_ou_indisponivel(saldo)}</div></div>
    <div class="tile"><div class="label">Salário médio</div><div class="valor">{fmt_brl_ou_indisponivel(salario_medio)}</div></div>
    <div class="tile"><div class="label">Renda extra média</div><div class="valor">{fmt_brl_ou_indisponivel(renda_extra_media)}</div></div>
    <div class="tile"><div class="label">Sobra com renda extra (antes do variável)</div><div class="valor {sobra_fixa_com_extra_classe}">{fmt_brl_ou_indisponivel(sobra_fixa_com_extra)}</div></div>
  </div>

  <h2 class="secao-titulo">Panorama mês a mês</h2>
  {painel_meses_html}

  <div class="card">
    <h2>Salário x gastos fixos — a conta fecha sem a renda extra?</h2>
    <div class="tiles">
      <div class="tile"><div class="label">Salário médio (últimos meses fechados)</div><div class="valor">{fmt_brl_ou_indisponivel(salario_medio)}</div></div>
      <div class="tile"><div class="label">Total de gastos fixos</div><div class="valor">{fmt_brl(total_fixo)}</div></div>
      <div class="tile"><div class="label">Sobra fixa (só salário, antes do variável)</div><div class="valor {sobra_fixa_classe}">{fmt_brl_ou_indisponivel(sobra_fixa)}</div></div>
      <div class="tile"><div class="label">Renda extra mínima necessária</div><div class="valor">{fmt_brl_ou_indisponivel(renda_extra_necessaria)}</div></div>
    </div>
  </div>

  <div class="card">
    <h2>Gasto no cartão por mês — está diminuindo?</h2>
    <div class="legenda">
      <span><span class="dot despesa"></span>Total comprado no cartão naquele mês (data da compra)</span>
    </div>
    <div class="chart-mensal">{barras_gasto_cartao}
    </div>
  </div>

  <div class="card">
    <h2>Todos os gastos do mês atual, por categoria</h2>
    <div class="subtitulo" style="margin-bottom:12px;">Inclui Pix e cartão. Total gasto até agora neste mês: {fmt_brl(total_gasto_mes)}</div>
    {categorias_mes_html}
  </div>

  <details class="card">
    <summary>Ver histórico (receitas x despesas por mês, categorias)</summary>
    <div class="tiles" style="margin-top:16px;">
      <div class="tile"><div class="label">Receitas no período</div><div class="valor">{fmt_brl(total_receita)}</div></div>
      <div class="tile"><div class="label">Despesas no período</div><div class="valor">{fmt_brl(total_despesa)}</div></div>
      <div class="tile"><div class="label">Resultado líquido do período</div><div class="valor {resultado_classe}">{fmt_brl(resultado)}</div></div>
    </div>
    <div class="card">
      <h2>Fluxo de caixa mensal</h2>
      <div class="legenda">
        <span><span class="dot receita"></span>Receitas</span>
        <span><span class="dot despesa"></span>Despesas</span>
      </div>
      <div class="chart-mensal">{barras_mensais}
      </div>
    </div>
    <div class="card">
      <h2>Top categorias de despesa</h2>
      {barras_categorias}
    </div>
    <div class="card">
      <table>
        <thead><tr><th>Mês</th><th class="num">Receitas</th><th class="num">Despesas</th><th class="num">Líquido</th></tr></thead>
        <tbody>{linhas_tabela}
        </tbody>
      </table>
    </div>
  </details>
</div>

<div id="tooltip"></div>
<script>
  const tooltip = document.getElementById('tooltip');
  document.querySelectorAll('[data-tip]').forEach(function (el) {{
    el.addEventListener('mousemove', function (e) {{
      tooltip.textContent = el.getAttribute('data-tip');
      tooltip.style.left = (e.clientX + 12) + 'px';
      tooltip.style.top = (e.clientY + 12) + 'px';
      tooltip.style.display = 'block';
    }});
    el.addEventListener('mouseleave', function () {{ tooltip.style.display = 'none'; }});
  }});

  function alternarTema() {{
    const atual = document.documentElement.getAttribute('data-theme');
    const novo = atual === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', novo);
    localStorage.setItem('tema-dashboard', novo);
  }}
  const salvo = localStorage.getItem('tema-dashboard');
  if (salvo) document.documentElement.setAttribute('data-theme', salvo);
</script>
</body>
</html>
"""


def main():
    transacoes, saldo = carregar_transacoes()
    html = montar_html(transacoes, saldo)
    OUT_PATH.parent.mkdir(exist_ok=True)
    OUT_PATH.write_text(html, encoding="utf-8")
    print(f"Dashboard gerado em {OUT_PATH}")


if __name__ == "__main__":
    main()
