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
import ui
from categorias_grandes import GRANDES_CATEGORIAS, grande_categoria
from dados_db import (
    carregar_transacoes_do_banco, carregar_gastos_fixos_do_banco, carregar_caixa_externo,
    carregar_variaveis_manuais_do_banco, carregar_orcamento_por_grande,
    carregar_ultima_sincronizacao,
)
from gerar_dashboard import montar_html, fmt_brl, media_por_grande_categoria
from normalizacao import traduzir_categoria
from sync import MESES_SEED_FIXOS, _mes_seguinte

CATEGORIAS_DISPONIVEIS = sorted(GRANDES_CATEGORIAS.keys()) + ["Outros"]
MESES_PT_LONGO = ["janeiro", "fevereiro", "março", "abril", "maio", "junho",
                  "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]

app = Flask(__name__)
db.inicializar()


def mes_por_extenso(mes: str) -> str:
    """'2026-08' -> 'agosto de 2026'. As páginas internas mostravam a chave
    crua ('Gastos fixos — 2026-08'), que obriga o usuário a decodificar."""
    try:
        ano, numero = mes.split("-")
        return f"{MESES_PT_LONGO[int(numero) - 1]} de {ano}"
    except (ValueError, IndexError):
        return mes


@app.route("/")
def dashboard():
    transacoes, saldo = carregar_transacoes_do_banco()
    gastos_fixos_por_mes = carregar_gastos_fixos_do_banco()
    caixa_externo = carregar_caixa_externo()
    variaveis_manuais_por_mes = carregar_variaveis_manuais_do_banco()
    orcamento = carregar_orcamento_por_grande()
    return montar_html(
        transacoes, saldo, gastos_fixos_por_mes, caixa_externo, variaveis_manuais_por_mes, orcamento,
        carregar_ultima_sincronizacao(),
    )


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
    data_br = "/".join(reversed(row["date"][:10].split("-")))
    corpo = f"""
      <div class="card">
        <div class="label" style="margin:0 0 6px;">Como veio do banco</div>
        <div style="font-size:15px;font-weight:600;">{row['description']}</div>
        <div style="font-size:13px;color:var(--text-secondary);margin-top:4px;">
          {traduzir_categoria(row['category'] or 'Outros')} · {fmt_brl(abs(row['amount']))} · {data_br}
        </div>
      </div>
      <form method="post" class="card">
        <label for="descricao">Sua descrição</label>
        <input id="descricao" type="text" name="descricao" value="{valor_atual}"
               placeholder="ex: presente de aniversário da Maria" autofocus>
        <div class="ajuda" style="font-size:12px;margin-top:5px;">
          Aparece no painel no lugar do nome do banco. Deixe vazio pra voltar ao original.
        </div>
        <label for="categoria_grande">Categoria no painel</label>
        <select id="categoria_grande" name="categoria_grande">{opcoes_categoria}</select>
        <div class="acoes">
          <button type="submit" class="botao botao-primario">Salvar</button>
          <a class="botao" href="/">Cancelar</a>
        </div>
      </form>"""
    return ui.pagina_formulario("Editar lançamento", corpo, "O valor e a data vêm do banco e não são editáveis.")


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
    """Teto mensal por GRANDE categoria (Mercado, Casa, Lazer...).

    Até 2026-07-26 essa tela editava as ~50 categorias finas da Pluggy e o
    painel somava tudo em 9 grupos pra comparar com o gasto real -- ou
    seja, o usuário ajustava 50 números pra ver 9. Agora edita-se
    exatamente o que o painel mostra. A tabela fina continua sendo
    alimentada pela sincronização e é a fonte da sugestão inicial (ver
    db._semear_orcamento_grande)."""
    with db.sessao() as conexao:
        if request.method == "POST":
            for chave, valor in request.form.items():
                if not chave.startswith("cat__"):
                    continue
                grande = chave[len("cat__"):]
                bruto = valor.strip()
                if not bruto:
                    # Campo apagado = "não quero teto pra essa categoria".
                    conexao.execute("DELETE FROM orcamento_grande WHERE grande = ?", (grande,))
                    continue
                try:
                    limite = float(bruto.replace(".", "").replace(",", ".")) if "," in bruto else float(bruto)
                except ValueError:
                    continue
                conexao.execute(
                    """INSERT INTO orcamento_grande (grande, limite_mensal, origem) VALUES (?, ?, 'manual')
                       ON CONFLICT(grande) DO UPDATE SET limite_mensal = excluded.limite_mensal, origem = 'manual'""",
                    (grande, limite),
                )
            return redirect("/orcamento")

        linhas = conexao.execute("SELECT grande, limite_mensal, origem FROM orcamento_grande").fetchall()

    atual = {r["grande"]: r for r in linhas}
    transacoes, _ = carregar_transacoes_do_banco()
    media = media_por_grande_categoria(transacoes)

    linhas_html = ""
    for grande in CATEGORIAS_DISPONIVEIS:
        row = atual.get(grande)
        valor = f"{row['limite_mensal']:.2f}" if row else ""
        sugerido = ' <span class="badge neutro">sugerido</span>' if row and row["origem"] == "media_historica" else ""
        referencia = media.get(grande)
        nota = f"média de 3 meses: {fmt_brl(referencia)}" if referencia else "sem histórico"
        linhas_html += f"""
        <tr>
          <td>
            <div style="font-size:13.5px;">{grande}{sugerido}</div>
            <div style="font-size:11.5px;color:var(--text-muted);margin-top:2px;">{nota}</div>
          </td>
          <td style="width:132px;">
            <input type="text" name="cat__{grande}" value="{valor}" inputmode="decimal"
                   placeholder="sem teto" aria-label="Teto mensal de {grande}">
          </td>
        </tr>"""

    total = sum(r["limite_mensal"] for r in linhas)
    total_media = sum(media.values())
    corpo = f"""
      <form method="post">
        <div class="card">
          <div style="display:flex;justify-content:space-between;align-items:baseline;gap:12px;">
            <span class="label">Teto somado</span>
            <span style="font-size:20px;font-weight:620;font-variant-numeric:tabular-nums;">{fmt_brl(total)}</span>
          </div>
          <div style="display:flex;justify-content:space-between;align-items:baseline;gap:12px;margin-top:8px;">
            <span class="label">Gasto médio dos últimos 3 meses</span>
            <span style="font-size:13px;color:var(--text-secondary);font-variant-numeric:tabular-nums;">{fmt_brl(total_media)}</span>
          </div>
        </div>
        <div class="card">
          <table>{linhas_html}</table>
        </div>
        <div class="acoes">
          <button type="submit" class="botao botao-primario">Salvar tudo</button>
          <a class="botao" href="/">Voltar sem salvar</a>
        </div>
      </form>"""
    return ui.pagina_formulario(
        "Orçamento por categoria", corpo,
        "Um teto por categoria, no mesmo agrupamento do painel. Campo vazio = sem teto.",
    )


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
            return (
                f'<span class="valor-travado" title="Vinculado à cobrança real do cartão — '
                f'o valor sempre reflete o que o banco cobrou">🔒 {fmt_brl(r["valor"])}</span>'
            )
        return (
            f'<input type="text" name="fixo__{r["nome"]}" value="{r["valor"]:.2f}" inputmode="decimal" '
            f'aria-label="Valor de {r["nome"]}">'
        )

    def celula_remover(r):
        if r["forma"] == "cartao":
            return (
                f'<button type="submit" name="desfazer__{r["nome"]}" value="1" class="link-botao" '
                f'onclick="return confirm(\'Mandar de volta pra Variáveis? A transação real continua lá.\');">'
                f'→ variável</button>'
            )
        return (
            f'<input type="checkbox" name="remover__{r["nome"]}" value="1" '
            f'aria-label="Remover {r["nome"]} deste mês">'
        )

    linhas_html = "".join(
        f"""<tr>
             <td>
               <span class="celula-nome">
                 <input type="text" name="novo_nome__{r['nome']}" value="{r['nome']}"
                        title="O nome vale pra todos os meses" aria-label="Nome do gasto fixo">
                 <span class="badge neutro">{'Pix' if r['forma'] == 'pix' else 'Cartão'}</span>
               </span>
             </td>
             <td class="col-valor">{celula_valor(r)}</td>
             <td class="col-categoria"><select name="categoria__{r['nome']}" title="A categoria vale pra todos os meses"
                 aria-label="Categoria de {r['nome']}">{opcoes_categoria(r['categoria'] or 'Outros')}</select></td>
             <td class="col-remover">{celula_remover(r)}</td>
           </tr>"""
        for r in linhas
    )

    total_mes = sum(r["valor"] for r in linhas)
    tabela = f"""<div class="tabela-rolagem"><table>
        <thead><tr><th>Gasto</th><th>Valor</th><th>Categoria</th><th style="text-align:center;">Tirar</th></tr></thead>
        <tbody>{linhas_html}</tbody></table></div>""" if linhas else (
        '<p class="vazio">Nenhum gasto fixo cadastrado ainda pra este mês.</p>'
    )

    opcoes_novo = opcoes_categoria(None)
    corpo = f"""
      <form method="post" class="card">
        <input type="hidden" name="acao" value="editar">
        <div style="display:flex;justify-content:space-between;align-items:baseline;gap:12px;margin-bottom:6px;">
          <h3 style="margin:0;font-size:14px;">{len(linhas)} gasto{'s' if len(linhas) != 1 else ''} fixo{'s' if len(linhas) != 1 else ''}</h3>
          <span style="font-size:17px;font-weight:620;font-variant-numeric:tabular-nums;">{fmt_brl(total_mes)}</span>
        </div>
        {tabela}
        <div class="acoes"><button type="submit" class="botao botao-primario">Salvar alterações</button></div>
      </form>

      <form method="post" class="card">
        <input type="hidden" name="acao" value="adicionar">
        <h3 style="margin:0 0 4px;font-size:14px;">Adicionar gasto fixo em Pix</h3>
        <p class="ajuda">Gasto fixo no cartão não se digita aqui: abra a transação em Variáveis, no painel,
           e use "→ tornar fixo". Assim o valor nunca fica desatualizado em relação à cobrança real.</p>
        <label for="novo_nome">Nome</label>
        <input id="novo_nome" type="text" name="novo_nome" placeholder="ex: Academia" required>
        <label for="novo_valor">Valor mensal (R$)</label>
        <input id="novo_valor" type="text" name="novo_valor" placeholder="ex: 150,00" inputmode="decimal" required>
        <label for="novo_categoria">Categoria</label>
        <select id="novo_categoria" name="novo_categoria">{opcoes_novo}</select>
        <div class="acoes"><button type="submit" class="botao botao-primario">Adicionar</button></div>
      </form>"""
    return ui.pagina_formulario(
        f"Gastos fixos — {mes_por_extenso(mes)}", corpo,
        "Nome e categoria valem pra todos os meses; o valor vale só pra este.",
    )


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
             <td>{r['descricao']} <span class="badge neutro">Pix</span></td>
             <td class="col-valor"><input type="text" name="valor__{r['id']}" value="{r['valor']:.2f}"
                 inputmode="decimal" aria-label="Valor de {r['descricao']}"></td>
             <td class="col-categoria"><select name="categoria__{r['id']}"
                 aria-label="Categoria de {r['descricao']}">{opcoes_categoria(r['categoria'] or 'Outros')}</select></td>
             <td class="col-remover"><input type="checkbox" name="remover__{r['id']}" value="1"
                 aria-label="Remover {r['descricao']}"></td>
           </tr>"""
        for r in linhas
    )
    total_mes = sum(r["valor"] for r in linhas)
    tabela = f"""<div class="tabela-rolagem"><table>
        <thead><tr><th>Gasto</th><th>Valor</th><th>Categoria</th><th style="text-align:center;">Tirar</th></tr></thead>
        <tbody>{linhas_html}</tbody></table></div>""" if linhas else (
        '<p class="vazio">Nenhum gasto variável manual cadastrado ainda pra este mês.</p>'
    )

    opcoes_novo = opcoes_categoria(None)
    corpo = f"""
      <form method="post" class="card">
        <input type="hidden" name="acao" value="editar">
        <div style="display:flex;justify-content:space-between;align-items:baseline;gap:12px;margin-bottom:6px;">
          <h3 style="margin:0;font-size:14px;">{len(linhas)} lançamento{'s' if len(linhas) != 1 else ''} manual{'is' if len(linhas) != 1 else ''}</h3>
          <span style="font-size:17px;font-weight:620;font-variant-numeric:tabular-nums;">{fmt_brl(total_mes)}</span>
        </div>
        {tabela}
        <div class="acoes"><button type="submit" class="botao botao-primario">Salvar alterações</button></div>
      </form>

      <form method="post" class="card">
        <input type="hidden" name="acao" value="adicionar">
        <h3 style="margin:0 0 4px;font-size:14px;">Adicionar gasto variável em Pix</h3>
        <p class="ajuda">Só pra despesa em Pix que não passa pelo banco conectado. Cobrança de cartão
           não entra aqui — ela já aparece sozinha em Variáveis, puxada do banco.</p>
        <label for="nova_descricao">Descrição</label>
        <input id="nova_descricao" type="text" name="nova_descricao" placeholder="ex: Presente de aniversário" required>
        <label for="novo_valor">Valor (R$)</label>
        <input id="novo_valor" type="text" name="novo_valor" placeholder="ex: 80,00" inputmode="decimal" required>
        <label for="nova_categoria">Categoria</label>
        <select id="nova_categoria" name="nova_categoria">{opcoes_novo}</select>
        <div class="acoes"><button type="submit" class="botao botao-primario">Adicionar</button></div>
      </form>"""
    return ui.pagina_formulario(
        f"Gastos variáveis — {mes_por_extenso(mes)}", corpo,
        "Lançamentos digitados à mão; as compras do cartão já entram sozinhas no painel.",
    )


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

    corpo = f"""
      <form method="post" class="card">
        <p class="ajuda">Dinheiro e contas que ficam fora do banco conectado — espécie, conta em outro
           banco, reserva guardada. Soma no "Caixa disponível hoje" do painel e no caixa inicial de
           cada mês projetado.</p>
        <label for="valor">Valor total (R$)</label>
        <input id="valor" type="text" name="valor" value="{valor_atual}" inputmode="decimal" autofocus>
        <div class="acoes">
          <button type="submit" class="botao botao-primario">Salvar</button>
          <a class="botao" href="/">Cancelar</a>
        </div>
      </form>"""
    return ui.pagina_formulario("Caixa externo", corpo)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
