"""Gráficos do dashboard: SVG/HTML puro, sem nenhuma biblioteca externa.

Por que sem biblioteca (Chart.js, Plotly...): o dashboard é servido como
um HTML único, roda no celular do usuário em rede móvel e precisa
funcionar offline no arquivo estático gerado por
`gerar_dashboard.py main()`. Um <script> de CDN adicionaria ~200 KB e uma
dependência de rede pra desenhar quatro gráficos de 6 pontos cada.

Regras de leitura que valem pros quatro gráficos (skill `dataviz`):
- Cor por entidade, nunca por posição no ranking (ver ui.SLOT_CATEGORIA).
- Marca fina, folga de 2px entre fatias/segmentos na cor da superfície,
  eixo e grade recessivos, rótulo direto só onde cabe.
- Identidade nunca depende só da cor: todo gráfico com 2+ séries tem
  legenda com nome + valor, e a rosca tem também versão em tabela.
- Texto usa token de texto (--text-*), nunca a cor da série.
"""
from collections import defaultdict
from datetime import date

from ui import ORDEM_CATEGORIAS, fmt_brl, fmt_brl_curto, var_serie

CSS_GRAFICOS = """
  /* --- Rosca de categorias ---------------------------------------- */
  .rosca-bloco { display: flex; gap: 22px; align-items: center; flex-wrap: wrap; }
  .rosca-svg { flex: 0 0 200px; width: 200px; height: 200px; }
  .rosca-arco { transition: opacity .15s ease; }
  .rosca-bloco:hover .rosca-arco { opacity: .45; }
  .rosca-bloco .rosca-arco:hover { opacity: 1; }
  .rosca-centro-valor { font-size: 17px; font-weight: 620; letter-spacing: -0.02em; }
  .rosca-centro-label { font-size: 10.5px; letter-spacing: 0.04em; text-transform: uppercase; }
  .legenda-cat { flex: 1 1 240px; min-width: 220px; display: flex; flex-direction: column; gap: 2px; }
  .legenda-cat li {
    display: grid; grid-template-columns: 10px 1fr auto auto; align-items: center; gap: 9px;
    padding: 3px 6px; border-radius: 6px; font-size: 12.5px; list-style: none;
  }
  .legenda-cat li:hover { background: var(--surface-2); }
  .legenda-cat .marca { width: 10px; height: 10px; border-radius: 3px; }
  .legenda-cat .nome { color: var(--text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .legenda-cat .nome-detalhe { color: var(--text-muted); font-size: 11px; }
  .legenda-cat .nome-detalhe::before { content: " · "; }
  .legenda-cat .val { font-variant-numeric: tabular-nums; color: var(--text-primary); font-weight: 550; }
  .legenda-cat .pct { font-variant-numeric: tabular-nums; color: var(--text-muted); font-size: 12px; width: 38px; text-align: right; }
  /* Seletor de período: radios de verdade (teclado funciona), rótulos
     estilizados e troca de painel por :checked -- zero JS. */
  .rosca-grupo { position: relative; }
  .rosca-radio { position: absolute; opacity: 0; pointer-events: none; }
  .segmentado {
    display: inline-flex; gap: 2px; padding: 2px; margin-bottom: 16px;
    background: var(--surface-2); border: 1px solid var(--border); border-radius: var(--r-sm); flex-wrap: wrap;
  }
  .segmentado label {
    margin: 0; padding: 5px 11px; border-radius: 6px; font-size: 12px; color: var(--text-secondary);
    cursor: pointer; white-space: nowrap; transition: background .15s ease, color .15s ease;
  }
  .segmentado label:hover { color: var(--text-primary); }
  .rosca-painel { display: none; }
  /* As regras que ligam cada radio ao seu painel e ao seu rótulo são
     geradas em rosca_com_filtro() -- dependem das chaves de período. */
  .ver-tabela { margin-top: 14px; }
  .ver-tabela > summary { font-size: 12px; color: var(--text-muted); cursor: pointer; }
  .ver-tabela > summary:hover { color: var(--text-primary); }

  /* --- Colunas empilhadas (fixas x variáveis) ---------------------- */
  .legenda { display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 16px; font-size: 12px; color: var(--text-secondary); }
  .legenda span { display: inline-flex; align-items: center; gap: 6px; }
  .chave { width: 10px; height: 10px; border-radius: 3px; display: inline-block; }
  .chave.fixas { background: var(--fixas); }
  .chave.variaveis { background: var(--variaveis); }
  .chave.linha { width: 14px; height: 0; border-top: 2px dashed var(--receita); border-radius: 0; }
  .plot-colunas { position: relative; padding-top: 8px; }
  .colunas { display: flex; align-items: flex-end; gap: 10px; height: 215px; }
  .coluna { flex: 1 1 0; display: flex; flex-direction: column; justify-content: flex-end; height: 100%; min-width: 0; }
  .pilha { display: flex; flex-direction: column-reverse; gap: 2px; }
  .seg { border-radius: 3px; min-height: 2px; transition: filter .15s ease; }
  .seg:hover { filter: brightness(1.12); }
  .seg.fixas { background: var(--fixas); border-radius: 0 0 3px 3px; }
  .seg.variaveis { background: var(--variaveis); border-radius: 3px 3px 0 0; }
  .coluna .topo-valor {
    font-size: 11px; color: var(--text-secondary); text-align: center; margin-bottom: 6px;
    font-variant-numeric: tabular-nums; background: var(--surface-1); border-radius: 3px; position: relative; z-index: 2;
  }
  .eixo-x { display: flex; gap: 10px; border-top: 1px solid var(--axis); padding-top: 7px; margin-top: 0; }
  .eixo-x span { flex: 1 1 0; text-align: center; font-size: 11px; color: var(--text-muted); min-width: 0; }
  .eixo-x span.atual { color: var(--text-primary); font-weight: 600; }
  .linha-entrada { position: absolute; left: 0; right: 0; border-top: 2px dashed var(--receita); opacity: .85; pointer-events: none; }
  .linha-entrada b {
    position: absolute; right: 0; top: -16px; font-size: 10.5px; font-weight: 550; color: var(--receita);
    background: var(--surface-1); padding: 0 0 0 5px; letter-spacing: 0.01em;
  }

  /* --- Linha de saldo projetado ------------------------------------ */
  .plot-linha { position: relative; width: 100%; }
  .plot-linha svg { position: absolute; inset: 0; width: 100%; height: 100%; }
  .ponto-saldo {
    position: absolute; width: 11px; height: 11px; margin: -5.5px 0 0 -5.5px; border-radius: 50%;
    background: var(--receita); box-shadow: 0 0 0 2px var(--surface-1); transition: transform .12s ease;
  }
  .ponto-saldo:hover { transform: scale(1.35); }
  .ponto-saldo.negativo { background: var(--critical); }
  .rotulo-saldo {
    position: absolute; font-size: 11.5px; font-weight: 600; color: var(--text-primary);
    white-space: nowrap; font-variant-numeric: tabular-nums; pointer-events: none;
  }
  .marca-zero {
    position: absolute; left: 0; font-size: 10.5px; color: var(--text-muted);
    transform: translateY(-100%); padding-bottom: 2px; pointer-events: none;
  }
  .eixo-linha { margin-top: 6px; border-top: none; }
  .sr-only {
    position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
    overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; border: 0;
  }

  /* --- Parcelas que terminam ---------------------------------------- */
  .parc-resumo { font-size: 13px; color: var(--text-secondary); margin: 0 0 14px; }
  .parc-mes { border-top: 1px solid var(--grid); }
  .parc-mes:first-of-type { border-top: none; }
  .parc-mes > summary {
    display: grid; grid-template-columns: 1fr auto auto; align-items: baseline; gap: 12px;
    padding: 11px 6px; cursor: pointer; list-style: none; border-radius: 6px;
  }
  .parc-mes > summary::-webkit-details-marker { display: none; }
  .parc-mes > summary:hover { background: var(--surface-2); }
  .parc-mes > .parc-lista { padding: 2px 6px 14px; }
  .parc-qtd { font-size: 12px; color: var(--text-muted); }
  .parc-titulo { font-size: 13px; font-weight: 600; }
  .parc-titulo::before { content: "›"; color: var(--text-muted); display: inline-block; width: 14px; }
  .parc-mes[open] .parc-titulo::before { content: "⌄"; }
  .parc-alivio { font-size: 12.5px; font-weight: 600; color: var(--good); font-variant-numeric: tabular-nums; }
  .parc-lista { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 5px; }
  .parc-lista li { display: grid; grid-template-columns: 8px 1fr auto auto; align-items: center; gap: 9px; font-size: 12.5px; }
  .parc-nome { color: var(--text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .parc-parcela { font-size: 10.5px; padding: 1px 6px; border-radius: 999px; background: var(--surface-2); color: var(--text-muted); font-variant-numeric: tabular-nums; }
  .parc-valor { font-variant-numeric: tabular-nums; color: var(--text-primary); font-weight: 550; }

  /* --- Orçamento x real -------------------------------------------- */
  .orc-linha { display: grid; grid-template-columns: 132px 1fr 172px; align-items: center; gap: 12px; margin-bottom: 11px; }
  .orc-nome { font-size: 13px; color: var(--text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .orc-track { position: relative; background: var(--grid); border-radius: 4px; height: 16px; overflow: visible; }
  .orc-fill { height: 16px; border-radius: 4px; }
  .orc-fill.good { background: var(--good-mark); }
  .orc-fill.warn { background: var(--warn); }
  .orc-fill.critical { background: var(--critical); }
  .orc-marca { position: absolute; top: -3px; bottom: -3px; width: 2px; background: var(--text-primary); opacity: .55; border-radius: 1px; }
  .orc-valor { font-size: 12px; color: var(--text-secondary); text-align: right; font-variant-numeric: tabular-nums; }
  .orc-valor b { color: var(--text-primary); font-weight: 600; }
  @media (max-width: 560px) {
    .orc-linha { grid-template-columns: 1fr; gap: 4px; margin-bottom: 16px; }
    .orc-valor { text-align: left; }
    .rosca-svg { margin: 0 auto; }
  }
"""


def _agrupar_por_grande(itens: list[dict]) -> dict[str, float]:
    totais: dict[str, float] = defaultdict(float)
    for item in itens:
        chave = item.get("categoria_grande") or item.get("categoria") or "Outros"
        totais[chave] += item["valor"]
    return dict(totais)


def _ordenar_para_arcos(totais: dict[str, float]) -> tuple[list[tuple[str, float]], list[tuple[str, float]]]:
    """Ordem FIXA de desenho (ver docstring de ui.py): as grandes
    categorias com slot de cor, na ordem dos slots, e depois o balde
    cinza com tudo que não tem slot. Ordenar por valor deixaria qualquer
    par de cores encostado -- combinação que não passa na validação de
    daltonismo.

    Devolve também a composição do balde cinza, pra legenda dizer o que
    foi parar lá dentro (senão uma categoria real, como Combustível,
    simplesmente some da tela sem explicação)."""
    arcos = [(nome, totais[nome]) for nome in ORDEM_CATEGORIAS if totais.get(nome)]
    dentro_de_outros = sorted(
        ((nome, valor) for nome, valor in totais.items() if nome not in ORDEM_CATEGORIAS and valor),
        key=lambda kv: -kv[1],
    )
    sobra = sum(valor for _, valor in dentro_de_outros)
    if sobra:
        arcos.append(("Outros", sobra))
    # A própria categoria "Outros" não entra no detalhe do arco cinza --
    # "Outros · Combustível, Outros" é ruído, não informação.
    return arcos, [(nome, valor) for nome, valor in dentro_de_outros if nome != "Outros"]


def rosca_categorias(itens: list[dict], rotulo_periodo: str) -> str:
    """Rosca (pizza vazada) da composição das despesas por grande
    categoria. Sempre acompanhada de legenda com valor + % e de uma
    tabela alternativa -- a fatia sozinha responde "qual é a maior?", a
    legenda responde "quanto exatamente?"."""
    totais = _agrupar_por_grande(itens)
    arcos, dentro_de_outros = _ordenar_para_arcos(totais)
    total = sum(v for _, v in arcos)
    if not arcos or total <= 0:
        return '<p class="vazio">Nenhuma despesa lançada neste mês ainda.</p>'

    raio, largura = 68.0, 22.0
    circunferencia = 2 * 3.141592653589793 * raio
    folga = 3.0 if len(arcos) > 1 else 0.0

    segmentos, itens_legenda, linhas_tabela = "", "", ""
    acumulado = 0.0
    for nome, valor in arcos:
        fracao = valor / total
        comprimento = max(fracao * circunferencia - folga, 0.8)
        cor = var_serie(nome)
        pct = fracao * 100
        dica = f"<b>{nome}</b><br>{fmt_brl(valor)} · {pct:.0f}% do mês"
        if nome == "Outros" and dentro_de_outros:
            dica += "<br>" + "<br>".join(f"{n}: {fmt_brl(v)}" for n, v in dentro_de_outros[:6])
        segmentos += (
            f'<circle class="rosca-arco" cx="100" cy="100" r="{raio}" fill="none" stroke="{cor}" '
            f'stroke-width="{largura}" stroke-dasharray="{comprimento:.2f} {circunferencia - comprimento:.2f}" '
            f'stroke-dashoffset="{-acumulado:.2f}" data-tip="{dica}"><title>{nome}: {fmt_brl(valor)}</title></circle>'
        )
        detalhe_outros = ""
        if nome == "Outros" and dentro_de_outros:
            detalhe_outros = (
                '<span class="nome-detalhe">' + ", ".join(n for n, _ in dentro_de_outros[:3]) + "</span>"
            )
        itens_legenda += (
            f'<li data-tip="{dica}"><span class="marca" style="background:{cor}"></span>'
            f'<span class="nome">{nome}{detalhe_outros}</span><span class="val">{fmt_brl(valor)}</span>'
            f'<span class="pct">{pct:.0f}%</span></li>'
        )
        linhas_tabela += f'<tr><td>{nome}</td><td class="num">{fmt_brl(valor)}</td><td class="num">{pct:.1f}%</td></tr>'
        acumulado += fracao * circunferencia

    return f"""
<div class="rosca-bloco">
  <svg class="rosca-svg" viewBox="0 0 200 200" role="img"
       aria-label="Composição das despesas de {rotulo_periodo} por categoria. Total {fmt_brl(total)}.">
    <g transform="rotate(-90 100 100)">{segmentos}</g>
    <text x="100" y="97" text-anchor="middle" class="rosca-centro-valor" fill="var(--text-primary)">{_reais(total)}</text>
    <text x="100" y="113" text-anchor="middle" class="rosca-centro-label" fill="var(--text-muted)">no mês</text>
  </svg>
  <ul class="legenda-cat">{itens_legenda}</ul>
</div>
<details class="ver-tabela">
  <summary>Ver como tabela</summary>
  <table><thead><tr><th>Categoria</th><th class="num">Valor</th><th class="num">Participação</th></tr></thead>
  <tbody>{linhas_tabela}</tbody></table>
</details>"""


def rosca_com_filtro(periodos: list[tuple[str, str, list[dict]]]) -> str:
    """Mesma rosca, com um seletor de período (previsto / realizado /
    média). Trocar de período não recarrega a página nem depende de JS: os
    três blocos vêm no HTML e a troca é `input:checked ~ painel`. É o tipo
    de controle que precisa responder no toque -- ir ao servidor pra
    redesenhar uma rosca de 8 fatias seria um round-trip por curiosidade.
    """
    if not periodos:
        return ""

    entradas, botoes, paineis, regras = "", "", "", ""
    for i, (chave, rotulo, itens) in enumerate(periodos):
        marcado = " checked" if i == 0 else ""
        entradas += (
            f'<input type="radio" name="periodo-rosca" id="rosca-{chave}" class="rosca-radio"{marcado}>'
        )
        botoes += f'<label for="rosca-{chave}">{rotulo}</label>'
        paineis += f'<div class="rosca-painel rosca-painel-{chave}">{rosca_categorias(itens, rotulo)}</div>'
        regras += (
            f'#rosca-{chave}:checked ~ .rosca-painel-{chave} {{ display: block; }}'
            f'#rosca-{chave}:checked ~ .segmentado label[for="rosca-{chave}"] '
            f'{{ background: var(--surface-1); color: var(--text-primary); font-weight: 600; '
            f'box-shadow: var(--sombra); }}'
            f'#rosca-{chave}:focus-visible ~ .segmentado label[for="rosca-{chave}"] '
            f'{{ outline: 2px solid var(--receita); outline-offset: 1px; }}'
        )

    return f"""
<style>{regras}</style>
<div class="rosca-grupo">
  {entradas}
  <div class="segmentado" role="group" aria-label="Período mostrado na rosca">{botoes}</div>
  {paineis}
</div>"""


def parcelas_terminando(grupos: list[dict], mes_label) -> str:
    """Quanto de compromisso mensal cai quando a última parcela de cada
    compra sai da fatura. O gráfico de colunas já mostra o total
    diminuindo; aqui está o porquê, com nome e valor.

    Cada mês é um `<details>` fechado (só o primeiro abre): nos dados
    reais são 60+ parcelas terminando em 6 meses, e a lista aberta empurra
    o painel mensal -- que é onde o usuário age -- pra três telas abaixo.
    O que interessa na varredura é o alívio por mês; o item a item é
    detalhe sob demanda."""
    if not grupos:
        return (
            '<p class="vazio">Nenhuma compra parcelada termina dentro dos meses projetados — '
            'o compromisso variável não afrouxa sozinho nesse período.</p>'
        )

    total_alivio = sum(g["alivio"] for g in grupos)
    linhas = ""
    for indice, grupo in enumerate(grupos):
        itens = "".join(
            f'<li><span class="ponto-cat" style="background:{var_serie(i.get("categoria_grande") or "Outros")}"></span>'
            f'<span class="parc-nome">{i["descricao"]}</span>'
            f'<span class="parc-parcela">{i["parcela"]}</span>'
            f'<span class="parc-valor">{fmt_brl(i["valor"])}</span></li>'
            for i in grupo["itens"]
        )
        quantidade = len(grupo["itens"])
        linhas += f"""
        <details class="parc-mes"{' open' if indice == 0 else ''}>
          <summary>
            <span class="parc-titulo">{mes_label(grupo['mes'])}</span>
            <span class="parc-qtd">{quantidade} compra{'s' if quantidade > 1 else ''}</span>
            <span class="parc-alivio">−{fmt_brl(grupo['alivio'])}/mês depois</span>
          </summary>
          <ul class="parc-lista">{itens}</ul>
        </details>"""

    return f"""
<p class="parc-resumo">Somando tudo, <b>{fmt_brl(total_alivio)} por mês</b> de parcelas terminam
   dentro da janela projetada.</p>
{linhas}"""


def colunas_fixas_variaveis(panorama: list[dict], mes_label) -> str:
    """Composição do compromisso de cada mês: quanto é gasto fixo (que só
    muda se você cancelar algo) e quanto é variável (que dá pra cortar no
    mês seguinte). A linha tracejada é a entrada prevista: coluna que
    passa dela é mês que não fecha só com a renda."""
    if not panorama:
        return '<p class="vazio">Sem meses projetados.</p>'

    entrada = panorama[0]["entrada"]
    altura = 215.0
    maximo = max([linha["necessario"] for linha in panorama] + [entrada]) or 1.0

    colunas, eixo = "", ""
    for linha in panorama:
        h_fixas = linha["fixas_total"] / maximo * altura
        h_var = linha["variaveis_total"] / maximo * altura
        rotulo = mes_label(linha["mes"])
        pct_fixas = linha["fixas_total"] / linha["necessario"] * 100 if linha["necessario"] else 0
        colunas += f"""
        <div class="coluna">
          <div class="topo-valor">{fmt_brl_curto(linha['necessario'])}</div>
          <div class="pilha">
            <div class="seg fixas" style="height:{h_fixas:.1f}px"
                 data-tip="<b>Fixas · {rotulo}</b><br>{fmt_brl(linha['fixas_total'])} ({pct_fixas:.0f}% do mês)"></div>
            <div class="seg variaveis" style="height:{h_var:.1f}px"
                 data-tip="<b>Variáveis · {rotulo}</b><br>{fmt_brl(linha['variaveis_total'])} ({100 - pct_fixas:.0f}% do mês)"></div>
          </div>
        </div>"""
        eixo += f'<span class="{"atual" if linha.get("eh_atual") else ""}">{rotulo}</span>'

    y_entrada = altura - (entrada / maximo * altura)
    return f"""
<div class="legenda">
  <span><span class="chave fixas"></span>Fixas</span>
  <span><span class="chave variaveis"></span>Variáveis</span>
  <span><span class="chave linha"></span>Entrada prevista ({fmt_brl(entrada)})</span>
</div>
<div class="plot-colunas">
  <div class="colunas">{colunas}
  </div>
  <div class="linha-entrada" style="top:{y_entrada + 8:.1f}px"><b>entrada</b></div>
</div>
<div class="eixo-x">{eixo}</div>"""


def linha_saldo_projetado(panorama: list[dict], mes_label) -> str:
    """Saldo projetado no fim de cada mês. Uma série só -- sem legenda, o
    título já diz o que é. O que importa aqui é o cruzamento do zero: a
    faixa vermelha marca o território negativo e o primeiro ponto que cai
    nela ganha rótulo direto.

    Geometria em SVG, texto em HTML: um SVG com viewBox escalado encolhe
    junto o rótulo, e num celular de 390px os meses ficavam com ~7px --
    ilegíveis. Aqui o SVG cuida só das linhas (eixo X em % de 0 a 100,
    eixo Y em px reais, altura fixa) e todo texto/marcador é HTML
    posicionado por porcentagem, então o tamanho da fonte não depende da
    largura da tela.
    """
    pontos = [linha for linha in panorama if linha["saldo_final"] is not None]
    if len(pontos) < 2:
        return '<p class="vazio">Saldo projetado indisponível — falta o saldo atual da conta.</p>'

    altura, topo, base = 200.0, 28.0, 16.0
    margem_x = 5.0  # em % da largura, pra o primeiro e o último ponto não colarem na borda
    valores = [p["saldo_final"] for p in pontos]
    tem_negativo = any(v < 0 for v in valores)
    v_max, v_min = max(valores + [0.0]), min(valores + [0.0])
    intervalo = (v_max - v_min) or 1.0
    v_max += intervalo * 0.12
    # Sem nenhum mês negativo o zero é o próprio chão do gráfico -- abrir
    # espaço abaixo dele desenharia uma faixa de "território negativo" que
    # não existe nos dados, e inflaria a área do saldo.
    v_min = v_min - intervalo * 0.16 if tem_negativo else 0.0
    intervalo = v_max - v_min

    def x_de(i: int) -> float:
        return margem_x + i * (100 - 2 * margem_x) / (len(pontos) - 1)

    def y_de(v: float) -> float:
        return topo + (v_max - v) / intervalo * (altura - topo - base)

    y_zero = y_de(0.0)
    coords = [(x_de(i), y_de(p["saldo_final"])) for i, p in enumerate(pontos)]
    linha_path = " ".join(f"{'M' if i == 0 else 'L'}{x:.2f},{y:.1f}" for i, (x, y) in enumerate(coords))
    area_path = (
        f"M{coords[0][0]:.2f},{y_zero:.1f} "
        + " ".join(f"L{x:.2f},{y:.1f}" for x, y in coords)
        + f" L{coords[-1][0]:.2f},{y_zero:.1f} Z"
    )

    primeiro_negativo = next((i for i, p in enumerate(pontos) if p["saldo_final"] < 0), None)
    marcadores, rotulos, eixo = "", "", ""
    for i, (p, (x, y)) in enumerate(zip(pontos, coords)):
        negativo = p["saldo_final"] < 0
        classe = "negativo" if negativo else ""
        marcadores += (
            f'<span class="ponto-saldo {classe}" style="left:{x:.2f}%;top:{y:.1f}px"'
            f' data-tip="<b>{mes_label(p["mes"])}</b><br>Saldo projetado: {fmt_brl(p["saldo_final"])}"></span>'
        )
        if i in (0, len(pontos) - 1, primeiro_negativo):
            deslocamento = "translate(-50%,-100%)" if not negativo else "translate(-50%,0)"
            topo_rotulo = y - 10 if not negativo else y + 12
            rotulos += (
                f'<span class="rotulo-saldo" style="left:{x:.2f}%;top:{topo_rotulo:.1f}px;'
                f'transform:{deslocamento}">{fmt_brl_curto(p["saldo_final"])}</span>'
            )
        eixo += f'<span>{mes_label(p["mes"])}</span>'

    zona_negativa = ""
    if tem_negativo:
        zona_negativa = (
            f'<rect x="0" y="{y_zero:.1f}" width="100" height="{altura - base - y_zero:.1f}" '
            f'fill="var(--critical)" opacity="0.07"></rect>'
        )

    return f"""
<div class="plot-linha" style="height:{altura:.0f}px">
  <svg viewBox="0 0 100 {altura:.0f}" preserveAspectRatio="none" aria-hidden="true" focusable="false">
    {zona_negativa}
    <path d="{area_path}" fill="var(--receita)" opacity="0.09"></path>
    <line x1="0" y1="{y_zero:.1f}" x2="100" y2="{y_zero:.1f}" stroke="var(--axis)" stroke-width="1"
          vector-effect="non-scaling-stroke"></line>
    <path d="{linha_path}" fill="none" stroke="var(--receita)" stroke-width="2" stroke-linecap="round"
          stroke-linejoin="round" vector-effect="non-scaling-stroke"></path>
  </svg>
  <span class="marca-zero" style="top:{y_zero:.1f}px">R$ 0</span>
  {marcadores}{rotulos}
  <span class="sr-only">Saldo projetado no fim de cada mês, de {fmt_brl(valores[0])} em
    {mes_label(pontos[0]["mes"])} a {fmt_brl(valores[-1])} em {mes_label(pontos[-1]["mes"])}.</span>
</div>
<div class="eixo-x eixo-linha">{eixo}</div>"""


def _reais(valor: float) -> str:
    """Sem centavos: numa linha de orçamento o que importa é a ordem de
    grandeza, e "R$ 6.539 de R$ 2.295 · 285%" cabe onde o valor cheio
    quebraria em duas linhas."""
    return "R$ " + f"{valor:,.0f}".replace(",", ".")


def _classe_uso(pct: float) -> str:
    return "critical" if pct > 100 else "warn" if pct >= 80 else "good"


def orcamento_x_real(realizado: dict[str, float], limites: dict[str, float], hoje: date | None = None) -> str:
    """Quanto de cada teto mensal já foi gasto no mês corrente. A marca
    vertical é o ritmo esperado até hoje (dia 15 de 30 = 50% do teto):
    barra que passou da marca está gastando mais rápido que o mês
    permite, mesmo sem ter estourado ainda."""
    if not limites:
        return (
            '<p class="vazio">Nenhum teto definido ainda. '
            '<a href="/orcamento">Definir orçamento por categoria →</a></p>'
        )

    hoje = hoje or date.today()
    dias_no_mes = (date(hoje.year + (hoje.month == 12), hoje.month % 12 + 1, 1) - date(hoje.year, hoje.month, 1)).days
    ritmo = hoje.day / dias_no_mes * 100

    ordenado = sorted(limites.items(), key=lambda kv: -(realizado.get(kv[0], 0.0) / (kv[1] or 1)))
    linhas = ""
    for nome, limite in ordenado:
        if not limite:
            continue
        gasto = realizado.get(nome, 0.0)
        pct = gasto / limite * 100
        classe = _classe_uso(pct)
        dica = (
            f"<b>{nome}</b><br>{fmt_brl(gasto)} de {fmt_brl(limite)} ({pct:.0f}%)<br>"
            f"Ritmo esperado até hoje: {ritmo:.0f}%"
        )
        linhas += f"""
        <div class="orc-linha" data-tip="{dica}">
          <span class="orc-nome">{nome}</span>
          <div class="orc-track">
            <div class="orc-fill {classe}" style="width:{min(pct, 100):.1f}%"></div>
            <div class="orc-marca" style="left:{min(ritmo, 100):.1f}%"></div>
          </div>
          <span class="orc-valor"><b>{_reais(gasto)}</b> de {_reais(limite)} · {pct:.0f}%</span>
        </div>"""

    estourados = [n for n, l in limites.items() if l and realizado.get(n, 0.0) > l]
    resumo = (
        f'<span class="badge critical">{len(estourados)} categoria{"s" if len(estourados) > 1 else ""} '
        f'acima do teto</span>' if estourados else '<span class="badge good">Nenhuma categoria estourada</span>'
    )
    return f"""
<div style="display:flex;align-items:center;gap:10px;margin-bottom:16px;flex-wrap:wrap;">
  {resumo}
  <span style="font-size:12px;color:var(--text-muted);">
    Barra vertical = ritmo esperado até o dia {hoje.day} ({ritmo:.0f}% do mês)
  </span>
</div>
{linhas}"""
