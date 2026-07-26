"""App Flask que serve o dashboard financeiro a partir do banco local
(populado por sync.py) e permite editar descrição de compra, valor de
gasto fixo por mês e orçamento por categoria.

Uso local: python execution/app.py  (roda em http://localhost:8000)
Em produção (VPS): servido via gunicorn atrás do Traefik (ver Dockerfile).
"""
import sqlite3
from datetime import datetime

from flask import Flask, redirect, request

import db
from categorias_grandes import GRANDES_CATEGORIAS, grande_categoria
from dados_db import (
    carregar_transacoes_do_banco, carregar_gastos_fixos_do_banco, carregar_caixa_externo,
    carregar_variaveis_manuais_do_banco,
)
from gerar_dashboard import montar_html, fmt_brl
from normalizacao import traduzir_categoria
from sync import MESES_SEED_FIXOS, _mes_seguinte

CATEGORIAS_DISPONIVEIS = sorted(GRANDES_CATEGORIAS.keys()) + ["Outros"]

app = Flask(__name__)
db.inicializar()

ESTILO_PAGINA_SIMPLES = """
<style>
  body { margin:0; padding:32px 24px; background:#f9f9f7; color:#0b0b0b; font-family:system-ui,-apple-system,"Segoe UI",sans-serif; }
  @media (prefers-color-scheme: dark) { body { background:#0d0d0d; color:#fff; } input { background:#1a1a19 !important; color:#fff !important; border-color:#383835 !important; } }
  .wrap { max-width:480px; margin:0 auto; }
  h1 { font-size:18px; }
  label { display:block; font-size:12px; color:#666; margin:14px 0 4px; }
  input, select { width:100%; padding:8px 10px; border-radius:6px; border:1px solid #ccc; font-size:14px; box-sizing:border-box; }
  button { margin-top:18px; padding:8px 16px; border-radius:6px; border:none; background:#2a78d6; color:#fff; font-size:13px; cursor:pointer; }
  table { width:100%; border-collapse:collapse; font-size:13px; margin-top:12px; }
  td, th { padding:6px 4px; text-align:left; border-bottom:1px solid #ddd; }
  a { color:#2a78d6; }
  .btn-remover { margin-top:0; padding:6px 10px; background:#b0392f; }
  .btn-add { background:#2f8f5b; }
  .secao { margin-top:32px; padding-top:16px; border-top:1px solid #ddd; }
  @media (prefers-color-scheme: dark) { .secao { border-color:#383835; } }
  .col-valor { width:30%; }
  .col-categoria { width:32%; }
  .col-remover { width:80px; text-align:center; }
  .link-botao { margin:0; padding:0; background:none; border:none; color:#666; font-size:11px; text-decoration:underline; cursor:pointer; }
  @media (prefers-color-scheme: dark) { .link-botao { color:#999; } }
</style>
"""


@app.route("/")
def dashboard():
    transacoes, saldo = carregar_transacoes_do_banco()
    gastos_fixos_por_mes = carregar_gastos_fixos_do_banco()
    caixa_externo = carregar_caixa_externo()
    variaveis_manuais_por_mes = carregar_variaveis_manuais_do_banco()
    return montar_html(transacoes, saldo, gastos_fixos_por_mes, caixa_externo, variaveis_manuais_por_mes)


@app.route("/transacao/<transacao_id>/editar", methods=["GET", "POST"])
def editar_transacao(transacao_id):
    with db.sessao() as conexao:
        if request.method == "POST":
            nova_descricao = request.form.get("descricao", "").strip()
            nova_categoria = request.form.get("categoria_grande", "").strip()
            conexao.execute(
                "UPDATE transacoes SET description_custom = ?, categoria_grande_custom = ? WHERE id = ?",
                (nova_descricao or None, nova_categoria or None, transacao_id),
            )
            return redirect("/")

        row = conexao.execute(
            """SELECT description, description_custom, category, amount, date, categoria_grande_custom
               FROM transacoes WHERE id = ?""",
            (transacao_id,),
        ).fetchone()

    if row is None:
        return "Transação não encontrada.", 404

    valor_atual = row["description_custom"] or ""
    categoria_atual = row["categoria_grande_custom"] or grande_categoria(traduzir_categoria(row["category"] or "Outros"))
    opcoes_categoria = "".join(
        f'<option value="{c}"{" selected" if c == categoria_atual else ""}>{c}</option>'
        for c in CATEGORIAS_DISPONIVEIS
    )
    return f"""{ESTILO_PAGINA_SIMPLES}
    <div class="wrap">
      <a class="voltar" href="/">&larr; voltar</a>
      <h1>Editar lançamento</h1>
      <p>Original: <strong>{row['description']}</strong><br>
         {traduzir_categoria(row['category'] or 'Outros')} · {fmt_brl(abs(row['amount']))} · {row['date'][:10]}</p>
      <form method="post">
        <label>Sua descrição (fica visível no lugar da original)</label>
        <input type="text" name="descricao" value="{valor_atual}" placeholder="ex: presente de aniversário da Maria">
        <label>Grande categoria (agrupamento no painel)</label>
        <select name="categoria_grande">{opcoes_categoria}</select>
        <button type="submit">Salvar</button>
      </form>
    </div>"""


@app.route("/transacao/<transacao_id>/tornar-fixo", methods=["POST"])
def tornar_fixo(transacao_id):
    """Promove uma transação real (Variáveis) a gasto fixo recorrente: cria
    linhas em gastos_fixos pro mês atual + próximos meses (mesma janela do
    seed automático), guardando `transacao_id_origem` pra essa transação
    parar de aparecer duplicada em Variáveis (ver eh_fixo em
    gerar_dashboard.py). A transação original em si nunca é apagada nem
    alterada -- só passa a ser contada como fixa daqui pra frente."""
    with db.sessao() as conexao:
        row = conexao.execute(
            """SELECT account_type, COALESCE(description_custom, description) AS nome,
                      category, amount, categoria_grande_custom
               FROM transacoes WHERE id = ?""",
            (transacao_id,),
        ).fetchone()
        if row is None:
            return "Transação não encontrada.", 404

        nome = row["nome"] or "—"
        forma = "cartao" if row["account_type"] == "CREDIT" else "pix"
        valor = abs(row["amount"])
        categoria = row["categoria_grande_custom"] or grande_categoria(traduzir_categoria(row["category"] or "Outros"))

        mes_atual = datetime.now().strftime("%Y-%m")
        for i in range(MESES_SEED_FIXOS):
            mes = _mes_seguinte(mes_atual, i)
            conexao.execute(
                """INSERT INTO gastos_fixos (mes, nome, forma, valor, categoria, transacao_id_origem)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(mes, nome) DO UPDATE SET forma = excluded.forma,
                       valor = excluded.valor, categoria = excluded.categoria,
                       transacao_id_origem = excluded.transacao_id_origem""",
                (mes, nome, forma, valor, categoria, transacao_id),
            )
    return redirect("/")


@app.route("/orcamento", methods=["GET", "POST"])
def orcamento():
    with db.sessao() as conexao:
        if request.method == "POST":
            for chave, valor in request.form.items():
                if not chave.startswith("cat__") or not valor.strip():
                    continue
                categoria = chave[len("cat__"):]
                try:
                    limite = float(valor.replace(",", "."))
                except ValueError:
                    continue
                conexao.execute(
                    """INSERT INTO orcamento_categoria (categoria, limite_mensal, origem) VALUES (?, ?, 'manual')
                       ON CONFLICT(categoria) DO UPDATE SET limite_mensal = excluded.limite_mensal, origem = 'manual'""",
                    (categoria, limite),
                )
            return redirect("/orcamento")

        linhas = conexao.execute(
            "SELECT categoria, limite_mensal, origem FROM orcamento_categoria ORDER BY limite_mensal DESC"
        ).fetchall()

    linhas_html = "".join(
        f"""<tr><td>{traduzir_categoria(r['categoria'])}{' <small>(sugerido)</small>' if r['origem'] == 'media_historica' else ''}</td>
             <td><input type="text" name="cat__{r['categoria']}" value="{r['limite_mensal']:.2f}"></td></tr>"""
        for r in linhas
    )
    return f"""{ESTILO_PAGINA_SIMPLES}
    <div class="wrap">
      <a class="voltar" href="/">&larr; voltar</a>
      <h1>Orçamento por categoria</h1>
      <p>Os valores marcados "(sugerido)" foram calculados pela média dos últimos meses -- ajuste pra sua meta real.</p>
      <form method="post">
        <table>{linhas_html}</table>
        <button type="submit">Salvar tudo</button>
      </form>
    </div>"""


@app.route("/fixos/<mes>", methods=["GET", "POST"])
def fixos_mes(mes):
    with db.sessao() as conexao:
        if request.method == "POST":
            acao = request.form.get("acao", "editar")

            if acao == "adicionar":
                # Só pix pode ser digitado à mão -- gasto fixo no cartão só
                # existe via conversão de uma transação real (ver
                # tornar_fixo), pra nunca divergir do valor cobrado de
                # verdade.
                nome = request.form.get("novo_nome", "").strip()
                valor_str = request.form.get("novo_valor", "").strip()
                categoria = request.form.get("novo_categoria", "Outros")
                try:
                    valor_float = float(valor_str.replace(",", "."))
                except ValueError:
                    valor_float = None
                forma_existente = conexao.execute(
                    "SELECT forma FROM gastos_fixos WHERE mes = ? AND nome = ?", (mes, nome)
                ).fetchone()
                if nome and valor_float is not None and (forma_existente is None or forma_existente["forma"] != "cartao"):
                    conexao.execute(
                        """INSERT INTO gastos_fixos (mes, nome, forma, valor, categoria) VALUES (?, ?, 'pix', ?, ?)
                           ON CONFLICT(mes, nome) DO UPDATE SET
                               valor = excluded.valor, categoria = excluded.categoria""",
                        (mes, nome, valor_float, categoria),
                    )
                return redirect(f"/fixos/{mes}")

            formas_existentes = {r["nome"]: r["forma"] for r in conexao.execute(
                "SELECT nome, forma FROM gastos_fixos WHERE mes = ?", (mes,)
            )}
            renomear_depois = []  # (nome_atual, novo_nome) -- só aplicado no fim, ver abaixo
            for nome, forma in formas_existentes.items():
                # Categoria é classificação permanente do item -- replica pra
                # TODOS os meses (passado e futuro), não só o mês aberto
                # (pedido do usuário em 2026-07-26: "com os valores não
                # precisa replicar mês a mês... categoria e nome pode sim").
                categoria = request.form.get(f"categoria__{nome}", "Outros")
                conexao.execute(
                    "UPDATE gastos_fixos SET categoria = ? WHERE nome = ?",
                    (categoria, nome),
                )

                if forma == "cartao":
                    # Valor é travado (reflete a cobrança real do Pluggy),
                    # mas dá pra desfazer a conversão e mandar de volta pra
                    # Variáveis nesse mês -- a transação original nunca é
                    # tocada, só a linha em gastos_fixos que a linkava.
                    if request.form.get(f"desfazer__{nome}"):
                        conexao.execute("DELETE FROM gastos_fixos WHERE mes = ? AND nome = ?", (mes, nome))
                        continue
                else:
                    if request.form.get(f"remover__{nome}"):
                        conexao.execute("DELETE FROM gastos_fixos WHERE mes = ? AND nome = ?", (mes, nome))
                        continue

                    valor_str = request.form.get(f"fixo__{nome}", "").strip()
                    if valor_str:
                        try:
                            valor_float = float(valor_str.replace(",", "."))
                        except ValueError:
                            valor_float = None
                        if valor_float is not None:
                            conexao.execute(
                                "UPDATE gastos_fixos SET valor = ? WHERE mes = ? AND nome = ?",
                                (valor_float, mes, nome),
                            )

                novo_nome = request.form.get(f"novo_nome__{nome}", "").strip()
                if novo_nome and novo_nome != nome:
                    renomear_depois.append((nome, novo_nome))

            # Nome também é permanente -- renomeia em TODOS os meses. Feito
            # por último (depois de categoria/valor/remoção acima, que usam
            # o nome ANTIGO como chave dos campos do formulário). Se der
            # colisão com um nome já existente em algum mês, não renomeia
            # nada (mantém o nome antigo em todos os meses) em vez de deixar
            # inconsistência pela metade.
            for nome_atual, novo_nome in renomear_depois:
                try:
                    conexao.execute("UPDATE gastos_fixos SET nome = ? WHERE nome = ?", (novo_nome, nome_atual))
                except sqlite3.IntegrityError:
                    pass
            return redirect(f"/fixos/{mes}")

        linhas = conexao.execute(
            "SELECT nome, forma, valor, categoria FROM gastos_fixos WHERE mes = ? ORDER BY forma, nome", (mes,)
        ).fetchall()

    def opcoes_categoria(atual):
        return "".join(
            f'<option value="{c}"{" selected" if c == atual else ""}>{c}</option>'
            for c in CATEGORIAS_DISPONIVEIS
        )

    def celula_valor(r):
        if r["forma"] == "cartao":
            return f'<span title="Vinculado à cobrança real do Pluggy -- valor não editável">🔒 {fmt_brl(r["valor"])}</span>'
        return f'<input type="text" name="fixo__{r["nome"]}" value="{r["valor"]:.2f}">'

    def celula_remover(r):
        if r["forma"] == "cartao":
            return (
                f'<button type="submit" name="desfazer__{r["nome"]}" value="1" class="link-botao" '
                f'onclick="return confirm(\'Mandar de volta pra Variáveis? A transação real continua lá.\');">'
                f'→ variável</button>'
            )
        return f'<input type="checkbox" name="remover__{r["nome"]}" value="1" title="remover">'

    linhas_html = "".join(
        f"""<tr>
             <td><input type="text" name="novo_nome__{r['nome']}" value="{r['nome']}" title="Nome vale pra todos os meses">
                 <small>({'Pix' if r['forma'] == 'pix' else 'Cartão'})</small></td>
             <td class="col-valor">{celula_valor(r)}</td>
             <td class="col-categoria"><select name="categoria__{r['nome']}" title="Categoria vale pra todos os meses">{opcoes_categoria(r['categoria'] or 'Outros')}</select></td>
             <td class="col-remover">{celula_remover(r)}</td>
           </tr>"""
        for r in linhas
    )

    if not linhas:
        linhas_html = '<tr><td colspan="4"><small>Nenhum gasto fixo cadastrado ainda pra este mês.</small></td></tr>'

    opcoes_novo = opcoes_categoria(None)
    return f"""{ESTILO_PAGINA_SIMPLES}
    <div class="wrap">
      <a class="voltar" href="/">&larr; voltar</a>
      <h1>Gastos fixos — {mes}</h1>
      <form method="post">
        <input type="hidden" name="acao" value="editar">
        <table>{linhas_html}</table>
        <button type="submit">Salvar</button>
      </form>

      <div class="secao">
        <h1>Adicionar gasto fixo (Pix)</h1>
        <p><small>Gasto fixo no cartão não dá pra digitar à mão -- vá até a
           transação real em Variáveis e use o botão "→ fixo", assim o
           valor nunca fica desatualizado.</small></p>
        <form method="post">
          <input type="hidden" name="acao" value="adicionar">
          <label>Nome</label>
          <input type="text" name="novo_nome" placeholder="ex: Academia" required>
          <label>Valor (R$)</label>
          <input type="text" name="novo_valor" placeholder="ex: 150,00" required>
          <label>Categoria</label>
          <select name="novo_categoria">{opcoes_novo}</select>
          <button type="submit" class="btn-add">Adicionar</button>
        </form>
      </div>
    </div>"""


@app.route("/variaveis/<mes>", methods=["GET", "POST"])
def variaveis_mes(mes):
    """Gasto variável em Pix digitado à mão (ex.: uma conta que não passa
    pelo Pluggy) -- ao contrário das transações reais (que nunca podem ser
    apagadas/alteradas por valor), esses itens são 100% nossos e totalmente
    editáveis/apagáveis, igual aos gastos fixos em pix."""
    with db.sessao() as conexao:
        if request.method == "POST":
            acao = request.form.get("acao", "editar")

            if acao == "adicionar":
                descricao = request.form.get("nova_descricao", "").strip()
                valor_str = request.form.get("novo_valor", "").strip()
                categoria = request.form.get("nova_categoria", "Outros")
                try:
                    valor_float = float(valor_str.replace(",", "."))
                except ValueError:
                    valor_float = None
                if descricao and valor_float is not None:
                    conexao.execute(
                        """INSERT INTO gastos_variaveis_manuais (mes, descricao, forma, valor, categoria)
                           VALUES (?, ?, 'pix', ?, ?)""",
                        (mes, descricao, valor_float, categoria),
                    )
                return redirect(f"/variaveis/{mes}")

            ids_existentes = {r["id"] for r in conexao.execute(
                "SELECT id FROM gastos_variaveis_manuais WHERE mes = ?", (mes,)
            )}
            for item_id in ids_existentes:
                if request.form.get(f"remover__{item_id}"):
                    conexao.execute("DELETE FROM gastos_variaveis_manuais WHERE id = ?", (item_id,))
                    continue

                valor_str = request.form.get(f"valor__{item_id}", "").strip()
                categoria = request.form.get(f"categoria__{item_id}", "Outros")
                if valor_str:
                    try:
                        valor_float = float(valor_str.replace(",", "."))
                    except ValueError:
                        valor_float = None
                    if valor_float is not None:
                        conexao.execute(
                            "UPDATE gastos_variaveis_manuais SET valor = ? WHERE id = ?",
                            (valor_float, item_id),
                        )
                conexao.execute(
                    "UPDATE gastos_variaveis_manuais SET categoria = ? WHERE id = ?",
                    (categoria, item_id),
                )
            return redirect(f"/variaveis/{mes}")

        linhas = conexao.execute(
            "SELECT id, descricao, valor, categoria FROM gastos_variaveis_manuais WHERE mes = ? ORDER BY descricao",
            (mes,),
        ).fetchall()

    def opcoes_categoria(atual):
        return "".join(
            f'<option value="{c}"{" selected" if c == atual else ""}>{c}</option>'
            for c in CATEGORIAS_DISPONIVEIS
        )

    linhas_html = "".join(
        f"""<tr>
             <td>{r['descricao']} <small>(Pix)</small></td>
             <td class="col-valor"><input type="text" name="valor__{r['id']}" value="{r['valor']:.2f}"></td>
             <td class="col-categoria"><select name="categoria__{r['id']}">{opcoes_categoria(r['categoria'] or 'Outros')}</select></td>
             <td class="col-remover"><input type="checkbox" name="remover__{r['id']}" value="1" title="remover"></td>
           </tr>"""
        for r in linhas
    )
    if not linhas:
        linhas_html = '<tr><td colspan="4"><small>Nenhum gasto variável manual cadastrado ainda pra este mês.</small></td></tr>'

    opcoes_novo = opcoes_categoria(None)
    return f"""{ESTILO_PAGINA_SIMPLES}
    <div class="wrap">
      <a class="voltar" href="/">&larr; voltar</a>
      <h1>Gastos variáveis manuais — {mes}</h1>
      <p><small>Só pra despesas em Pix que não passam pelo Pluggy (ex.:
         conta paga de outra forma). Cobrança de cartão real nunca entra
         aqui -- ela já aparece sozinha em Variáveis, puxada do Pluggy.</small></p>
      <form method="post">
        <input type="hidden" name="acao" value="editar">
        <table>{linhas_html}</table>
        <button type="submit">Salvar</button>
      </form>

      <div class="secao">
        <h1>Adicionar gasto variável (Pix)</h1>
        <form method="post">
          <input type="hidden" name="acao" value="adicionar">
          <label>Descrição</label>
          <input type="text" name="nova_descricao" placeholder="ex: Presente aniversário" required>
          <label>Valor (R$)</label>
          <input type="text" name="novo_valor" placeholder="ex: 80,00" required>
          <label>Categoria</label>
          <select name="nova_categoria">{opcoes_novo}</select>
          <button type="submit" class="btn-add">Adicionar</button>
        </form>
      </div>
    </div>"""


@app.route("/caixa-externo", methods=["GET", "POST"])
def caixa_externo():
    with db.sessao() as conexao:
        if request.method == "POST":
            valor_str = request.form.get("valor", "0").strip()
            try:
                valor = float(valor_str.replace(",", "."))
            except ValueError:
                valor = 0.0
            db.definir_meta(conexao, "caixa_externo", str(valor))
            return redirect("/")

        valor_atual = db.obter_meta(conexao, "caixa_externo") or "0"

    return f"""{ESTILO_PAGINA_SIMPLES}
    <div class="wrap">
      <a class="voltar" href="/">&larr; voltar</a>
      <h1>Caixa externo</h1>
      <p>Dinheiro/contas fora do Pluggy (ex.: dinheiro em espécie, conta em
         banco não conectado) -- soma no "Caixa disponível" do topo e no
         "Caixa no início do mês" de cada card do painel mensal.</p>
      <form method="post">
        <label>Valor total (R$)</label>
        <input type="text" name="valor" value="{valor_atual}">
        <button type="submit">Salvar</button>
      </form>
    </div>"""


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
