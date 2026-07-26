"""Camada visual compartilhada entre o dashboard (gerar_dashboard.py) e as
páginas de formulário do Flask (app.py).

Antes desse módulo existiam DOIS sistemas visuais no projeto: o CSS do
dashboard (tokens, tema claro/escuro, cores de série) e o
ESTILO_PAGINA_SIMPLES do app.py (cores cravadas em hex, sem tema escuro
persistente, botão azul diferente do azul do dashboard). Quem clicava em
"editar" saía de um app pra outro. Aqui existe um conjunto único de
tokens + um shell de página (`pagina`) que as duas camadas usam.

## Paleta de séries (gráficos)

As oito cores categóricas e os tons de superfície/texto vêm da paleta de
referência da skill `dataviz`, validada com
`scripts/validate_palette.js`: banda de luminosidade, piso de croma,
separação para daltonismo e contraste contra a superfície.

Achado da validação (2026-07-26), que amarra como os gráficos podem ser
desenhados:
- A ordem dos slots (azul, laranja, água, amarelo, magenta, verde,
  violeta, vermelho) passa em todos os testes quando as cores aparecem
  **vizinhas nessa ordem** (barra empilhada, pizza, linha): pior par
  adjacente ΔE 9.1 claro / 8.4 escuro.
- Ela NÃO passa quando qualquer par pode encostar em qualquer outro
  (`--pairs all`): laranja x vermelho ΔE 7.1 e magenta x água ΔE 1.6 no
  escuro são indistinguíveis. Testei todos os subconjuntos de 5 e 6 cores
  das 8: nenhum passa. Por isso a rosca de categorias **desenha os arcos
  na ordem fixa dos slots** (nunca ordenados por valor) e sempre carrega
  legenda com nome + valor + % e uma tabela alternativa -- a identidade
  nunca depende só da cor.
- Fixas x Variáveis usa violeta x laranja, que passa até no teste
  `--pairs all` nos dois temas (ΔE 29.5 claro / 26.0 escuro), e deixa o
  azul reservado pro que é ENTRADA de dinheiro, como no resto do app.
"""

from fonte_numeros import FONTE_NUMEROS_WOFF2_BASE64

# --- Paleta categórica (dataviz) -------------------------------------
SERIES_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
SERIES_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]

# Cor por ENTIDADE, nunca por posição no ranking: se "Lazer" cair de 2º
# pra 5º lugar num mês, continua amarelo. Repintar os sobreviventes
# quando um filtro muda é o erro clássico de gráfico categórico.
# A ordem desse dict também é a ordem em que os arcos da rosca são
# desenhados (ver docstring do módulo).
# São 8 porque a paleta validada tem 8 slots -- e o projeto tem 10 grandes
# categorias. Quem fica de fora (hoje Combustível, Impostos e seguros e
# PerMax) entra no cinza "Outros" da rosca, e a legenda desse arco lista o
# que está lá dentro pra ninguém perder o rastro. Os 8 escolhidos são os
# de maior peso no gasto MENSAL (média de 3 meses + fixos), não no
# histórico acumulado -- é o mês que a rosca mostra. Revisar essa lista se
# a composição do gasto mudar de forma duradoura.
ORDEM_CATEGORIAS = ["Mercado", "Casa", "Transporte", "Assinaturas", "Família", "Saúde", "Compras", "Lazer"]
SLOT_CATEGORIA = {nome: i for i, nome in enumerate(ORDEM_CATEGORIAS)}

# "Outros" (e qualquer grande categoria sem slot, como PerMax) usa o
# cinza neutro: é o balde de sobra, não uma série com identidade própria.
CINZA_OUTROS_LIGHT, CINZA_OUTROS_DARK = "#a8a69d", "#6f6d67"

COR_FIXAS_LIGHT, COR_FIXAS_DARK = SERIES_LIGHT[6], SERIES_DARK[6]        # violeta
COR_VARIAVEIS_LIGHT, COR_VARIAVEIS_DARK = SERIES_LIGHT[1], SERIES_DARK[1]  # laranja


def var_serie(grande_categoria: str) -> str:
    """Nome da custom property CSS com a cor daquela grande categoria."""
    slot = SLOT_CATEGORIA.get(grande_categoria)
    return "var(--serie-outros)" if slot is None else f"var(--serie-{slot + 1})"


def _bloco_tema(escuro: bool) -> str:
    """Bloco de tokens de um tema. O tema escuro é declarado duas vezes
    (media query + [data-theme]) porque o botão de tema precisa ganhar do
    sistema nos DOIS sentidos."""
    if not escuro:
        series = "".join(f"    --serie-{i + 1}: {c};\n" for i, c in enumerate(SERIES_LIGHT))
        return f"""
    color-scheme: light;
    --page: #f9f9f7; --surface-1: #fcfcfb; --surface-2: #f2f1ec;
    --text-primary: #0b0b0b; --text-secondary: #52514e; --text-muted: #898781;
    --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10); --border-forte: rgba(11,11,11,0.18);
    --receita: #2a78d6; --despesa: #e34948;
    --good: #006300; --good-mark: #0ca30c; --critical: #d03b3b; --warn: #9c6f06;
    --good-bg: rgba(12,163,12,0.12); --critical-bg: rgba(208,59,59,0.12); --warn-bg: rgba(156,111,6,0.12);
    --fixas: {COR_FIXAS_LIGHT}; --variaveis: {COR_VARIAVEIS_LIGHT}; --serie-outros: {CINZA_OUTROS_LIGHT};
{series}    --sombra: 0 1px 2px rgba(11,11,11,0.04), 0 8px 20px -12px rgba(11,11,11,0.18);
    --sombra-alta: 0 2px 4px rgba(11,11,11,0.05), 0 14px 32px -16px rgba(11,11,11,0.24);
"""
    series = "".join(f"    --serie-{i + 1}: {c};\n" for i, c in enumerate(SERIES_DARK))
    return f"""
    color-scheme: dark;
    --page: #0d0d0d; --surface-1: #1a1a19; --surface-2: #232321;
    --text-primary: #ffffff; --text-secondary: #c3c2b7; --text-muted: #99978f;
    --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10); --border-forte: rgba(255,255,255,0.20);
    --receita: #3987e5; --despesa: #e66767;
    --good: #0ca30c; --good-mark: #0ca30c; --critical: #e66767; --warn: #d4a017;
    --good-bg: rgba(12,163,12,0.16); --critical-bg: rgba(230,103,103,0.16); --warn-bg: rgba(212,160,23,0.16);
    --fixas: {COR_FIXAS_DARK}; --variaveis: {COR_VARIAVEIS_DARK}; --serie-outros: {CINZA_OUTROS_DARK};
{series}    --sombra: 0 1px 2px rgba(0,0,0,0.4), 0 8px 20px -12px rgba(0,0,0,0.6);
    --sombra-alta: 0 2px 4px rgba(0,0,0,0.45), 0 14px 32px -16px rgba(0,0,0,0.7);
"""


CSS = f"""
  /* Ver fonte_numeros.py: só dígitos e sinais de moeda, 10,8 KB. */
  @font-face {{
    font-family: "Numeros";
    src: url(data:font/woff2;base64,{FONTE_NUMEROS_WOFF2_BASE64}) format("woff2");
    font-weight: 400 700;
    font-display: swap;
  }}

  :root {{{_bloco_tema(escuro=False)}
    --r-sm: 8px; --r-md: 12px; --r-lg: 16px;
    --fonte: system-ui, -apple-system, "Segoe UI", sans-serif;
    --fonte-numero: "Numeros", var(--fonte);
  }}

  /* Todo valor em dinheiro usa a mesma régua: mesma fonte, algarismos de
     largura fixa e o mesmo aperto de entreletra. É o que faz uma coluna
     de valores ler como coluna e não como texto corrido. */
  .num, .valor, .tile .valor, .cat-valor, .classe-total, .mes-resumo-valor,
  .rosca-centro-valor, .legenda-cat .val, .legenda-cat .pct, .orc-valor,
  .parc-valor, .parc-alivio, .rotulo-saldo, .delta, .topo-valor {{
    font-family: var(--fonte-numero);
    font-variant-numeric: tabular-nums;
    font-feature-settings: "tnum" 1;
    letter-spacing: -0.01em;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:where(:not([data-theme="light"])) {{{_bloco_tema(escuro=True)}  }}
  }}
  :root[data-theme="dark"] {{{_bloco_tema(escuro=True)}  }}

  * {{ box-sizing: border-box; }}
  html {{ -webkit-text-size-adjust: 100%; }}
  body {{
    margin: 0; padding: 0 0 72px; background: var(--page); color: var(--text-primary);
    font-family: var(--fonte); font-size: 14px; line-height: 1.45;
    -webkit-font-smoothing: antialiased;
  }}
  .wrap {{ max-width: 1040px; margin: 0 auto; padding: 0 20px; }}
  .wrap-estreito {{ max-width: 560px; }}
  :focus-visible {{ outline: 2px solid var(--receita); outline-offset: 2px; border-radius: 4px; }}

  /* --- Cabeçalho ------------------------------------------------- */
  .topo {{
    position: sticky; top: 0; z-index: 20; background: var(--page);
    border-bottom: 1px solid var(--border); margin-bottom: 28px;
  }}
  .topo-inner {{
    display: flex; align-items: center; justify-content: space-between; gap: 16px;
    padding: 14px 20px; max-width: 1040px; margin: 0 auto;
  }}
  .topo h1 {{ font-size: 17px; font-weight: 650; margin: 0; letter-spacing: -0.01em; }}
  .topo .meta {{ font-size: 12px; color: var(--text-muted); margin-top: 2px; }}
  .topo-acoes {{ display: flex; align-items: center; gap: 6px; flex-wrap: wrap; justify-content: flex-end; }}

  .botao {{
    display: inline-flex; align-items: center; gap: 6px; border: 1px solid var(--border);
    background: var(--surface-1); color: var(--text-secondary); border-radius: var(--r-sm);
    padding: 7px 11px; font-size: 12.5px; font-family: inherit; cursor: pointer;
    text-decoration: none; white-space: nowrap; transition: background .15s ease, color .15s ease, border-color .15s ease;
  }}
  .botao:hover {{ background: var(--surface-2); color: var(--text-primary); border-color: var(--border-forte); }}
  .botao:active {{ transform: translateY(0.5px); }}
  .botao-primario {{
    background: var(--receita); border-color: var(--receita); color: #fff; font-weight: 550;
    padding: 9px 18px; font-size: 13px;
  }}
  .botao-primario:hover {{ background: var(--receita); color: #fff; filter: brightness(1.06); }}
  .botao-perigo {{ background: var(--surface-1); color: var(--critical); border-color: var(--border); }}
  .botao-perigo:hover {{ background: var(--critical-bg); color: var(--critical); }}

  /* --- Estrutura -------------------------------------------------- */
  .secao {{ margin: 36px 0 14px; }}
  .secao h2 {{ font-size: 12px; font-weight: 650; letter-spacing: 0.06em; text-transform: uppercase; color: var(--text-muted); margin: 0; }}
  .secao p {{ font-size: 13px; color: var(--text-secondary); margin: 6px 0 0; max-width: 68ch; }}

  .card {{
    background: var(--surface-1); border: 1px solid var(--border); border-radius: var(--r-md);
    padding: 20px; margin-bottom: 16px; box-shadow: var(--sombra);
  }}
  .card > h3 {{ font-size: 14px; font-weight: 600; margin: 0 0 4px; color: var(--text-primary); }}
  .card > .ajuda {{ font-size: 12.5px; color: var(--text-secondary); margin: 0 0 18px; max-width: 62ch; }}
  .grade-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; align-items: start; }}
  .grade-2 > .card {{ margin-bottom: 0; height: 100%; }}
  @media (max-width: 880px) {{ .grade-2 {{ grid-template-columns: 1fr; }} }}

  /* --- Indicadores ------------------------------------------------ */
  .tiles {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; }}
  .tile {{ background: var(--surface-1); border: 1px solid var(--border); border-radius: var(--r-md); padding: 14px 16px; }}
  .tile .label {{ color: var(--text-secondary); font-size: 12px; line-height: 1.3; }}
  .tile .valor {{ font-size: 23px; font-weight: 620; letter-spacing: -0.02em; margin-top: 7px; }}
  .tile .nota {{ font-size: 11.5px; color: var(--text-muted); margin-top: 4px; }}
  .tile.destaque {{ background: var(--surface-2); border-color: var(--border-forte); }}
  .valor.good {{ color: var(--good); }}
  .valor.critical {{ color: var(--critical); }}
  .valor.warn {{ color: var(--warn); }}

  .badge {{
    display: inline-flex; align-items: center; gap: 5px; font-size: 11px; padding: 3px 9px;
    border-radius: 999px; font-weight: 600; letter-spacing: 0.01em;
  }}
  .badge.good {{ background: var(--good-bg); color: var(--good); }}
  .badge.critical {{ background: var(--critical-bg); color: var(--critical); }}
  .badge.warn {{ background: var(--warn-bg); color: var(--warn); }}
  .badge.neutro {{ background: var(--surface-2); color: var(--text-secondary); }}

  /* --- Tabelas ---------------------------------------------------- */
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ text-align: left; padding: 7px 8px; border-bottom: 1px solid var(--grid); }}
  th {{ font-size: 11.5px; font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.04em; }}
  tbody tr:last-child td {{ border-bottom: none; }}
  td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .tabela-rolagem {{ overflow-x: auto; }}

  /* --- Formulários ------------------------------------------------ */
  label {{ display: block; font-size: 12px; color: var(--text-secondary); margin: 14px 0 5px; font-weight: 500; }}
  input[type="text"], input[type="number"], select {{
    width: 100%; padding: 9px 11px; border-radius: var(--r-sm); border: 1px solid var(--border-forte);
    background: var(--surface-1); color: var(--text-primary); font-size: 14px; font-family: inherit;
  }}
  input[type="text"]::placeholder {{ color: var(--text-muted); }}
  input[type="text"]:focus, select:focus {{ border-color: var(--receita); }}
  input[type="checkbox"] {{ width: 17px; height: 17px; accent-color: var(--critical); cursor: pointer; }}
  form .acoes {{ margin-top: 20px; display: flex; gap: 10px; align-items: center; }}

  .col-valor {{ width: 120px; }}
  .col-categoria {{ width: 150px; }}
  .col-remover {{ width: 78px; text-align: center; }}
  .col-valor input, .col-categoria select {{ padding: 7px 9px; font-size: 13px; }}
  .celula-nome {{ display: flex; align-items: center; gap: 8px; }}
  .celula-nome input {{ flex: 1 1 auto; min-width: 0; }}
  .celula-nome .badge {{ flex: 0 0 auto; }}
  .valor-travado {{ font-size: 13px; color: var(--text-secondary); font-variant-numeric: tabular-nums; white-space: nowrap; }}
  .label {{ font-size: 12px; color: var(--text-secondary); }}
  .ajuda {{ font-size: 12.5px; color: var(--text-secondary); margin: 0 0 16px; max-width: 62ch; line-height: 1.5; }}
  details > summary {{ cursor: pointer; }}
  details.card > summary::-webkit-details-marker {{ display: none; }}

  a {{ color: var(--receita); }}
  .link-discreto {{ color: var(--text-muted); text-decoration: none; font-size: 11.5px; }}
  .link-discreto:hover {{ color: var(--text-primary); text-decoration: underline; }}
  .link-botao {{
    background: none; border: none; padding: 0; margin: 0; color: var(--text-muted); font-size: 11.5px;
    cursor: pointer; text-decoration: underline; font-family: inherit;
  }}
  .link-botao:hover {{ color: var(--text-primary); }}
  .voltar {{ display: inline-block; color: var(--text-secondary); font-size: 13px; text-decoration: none; margin-bottom: 4px; }}
  .voltar:hover {{ color: var(--text-primary); }}
  .vazio {{ color: var(--text-muted); font-size: 13px; padding: 14px 0; }}

  /* --- Tooltip compartilhado -------------------------------------- */
  #tooltip {{
    position: fixed; display: none; background: var(--text-primary); color: var(--page);
    font-size: 12px; line-height: 1.4; padding: 6px 9px; border-radius: 6px; pointer-events: none;
    z-index: 60; max-width: 240px; box-shadow: var(--sombra-alta); font-variant-numeric: tabular-nums;
  }}
  [data-tip] {{ cursor: default; }}

  @media (prefers-reduced-motion: reduce) {{
    *, *::before, *::after {{ animation-duration: 0.01ms !important; transition-duration: 0.01ms !important; }}
  }}
"""

# Aplicado antes da primeira pintura (fica no <head>, não no fim do body):
# se ficasse no fim, a página piscava clara antes de virar escura.
SCRIPT_TEMA_INICIAL = """
  (function () {
    try {
      var t = localStorage.getItem('tema-dashboard');
      if (t) document.documentElement.setAttribute('data-theme', t);
    } catch (e) {}
  })();
"""

SCRIPT_BASE = """
  var tooltip = document.getElementById('tooltip');
  function mostrarTip(el, x, y) {
    tooltip.innerHTML = el.getAttribute('data-tip');
    var w = tooltip.offsetWidth || 200;
    tooltip.style.left = Math.min(x + 14, window.innerWidth - w - 12) + 'px';
    tooltip.style.top = (y + 16) + 'px';
    tooltip.style.display = 'block';
  }
  function esconderTip() { tooltip.style.display = 'none'; }
  document.addEventListener('mousemove', function (e) {
    var el = e.target.closest ? e.target.closest('[data-tip]') : null;
    if (el) { mostrarTip(el, e.clientX, e.clientY); } else { esconderTip(); }
  });
  document.addEventListener('mouseleave', esconderTip);

  function alternarTema() {
    var atual = document.documentElement.getAttribute('data-theme');
    if (!atual) {
      atual = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    }
    var novo = atual === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', novo);
    try { localStorage.setItem('tema-dashboard', novo); } catch (e) {}
  }
"""

BOTAO_TEMA = (
    '<button class="botao" type="button" onclick="alternarTema()" '
    'aria-label="Alternar entre tema claro e escuro" title="Alternar tema">'
    '<span aria-hidden="true">◐</span> Tema</button>'
)


def fmt_brl(valor: float) -> str:
    s = f"{valor:,.2f}"
    s = s.replace(",", "_").replace(".", ",").replace("_", ".")
    return f"R$ {s}"


def fmt_brl_curto(valor: float) -> str:
    """Versão compacta pros eixos dos gráficos, onde o valor cheio não cabe."""
    if abs(valor) >= 1000:
        return f"{valor / 1000:,.1f}k".replace(".", ",")
    return f"{valor:,.0f}"


def fmt_brl_ou_indisponivel(valor: float | None) -> str:
    return "Indisponível" if valor is None else fmt_brl(valor)


def documento(titulo: str, cabecalho: str, corpo: str, css_extra: str = "", script_extra: str = "") -> str:
    """Shell HTML usado por todas as páginas do app."""
    return f"""<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{titulo}</title>
<script>{SCRIPT_TEMA_INICIAL}</script>
<style>{CSS}{css_extra}</style>
</head>
<body>
{cabecalho}
<main class="wrap">
{corpo}
</main>
<div id="tooltip" role="status" aria-live="polite"></div>
<script>{SCRIPT_BASE}{script_extra}</script>
</body>
</html>
"""


def cabecalho(titulo: str, subtitulo: str = "", acoes: str = "") -> str:
    sub = f'<div class="meta">{subtitulo}</div>' if subtitulo else ""
    return f"""<header class="topo">
  <div class="topo-inner">
    <div><h1>{titulo}</h1>{sub}</div>
    <div class="topo-acoes">{acoes}{BOTAO_TEMA}</div>
  </div>
</header>"""


def pagina_formulario(titulo: str, corpo: str, subtitulo: str = "") -> str:
    """Página interna (gastos fixos, variáveis, orçamento, edição): mesmo
    shell e mesmos tokens do dashboard, coluna estreita."""
    acoes = '<a class="botao" href="/">← Voltar ao painel</a>'
    return documento(
        titulo,
        cabecalho(titulo, subtitulo, acoes),
        f'<div class="wrap-estreito" style="padding:0;margin:0 auto;">{corpo}</div>',
    )
