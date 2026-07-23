"""App Flask que serve o dashboard financeiro a partir do banco local
(populado por sync.py) e permite editar descrição de compra, valor de
gasto fixo por mês e orçamento por categoria.

Uso local: python execution/app.py  (roda em http://localhost:8000)
Em produção (VPS): servido via gunicorn atrás do Traefik (ver Dockerfile).
"""
from flask import Flask, redirect, request

import db
from dados_db import carregar_transacoes_do_banco
from gerar_dashboard import montar_html, fmt_brl
from normalizacao import traduzir_categoria

app = Flask(__name__)
db.inicializar()

ESTILO_PAGINA_SIMPLES = """
<style>
  body { margin:0; padding:32px 24px; background:#f9f9f7; color:#0b0b0b; font-family:system-ui,-apple-system,"Segoe UI",sans-serif; }
  @media (prefers-color-scheme: dark) { body { background:#0d0d0d; color:#fff; } input { background:#1a1a19 !important; color:#fff !important; border-color:#383835 !important; } }
  .wrap { max-width:480px; margin:0 auto; }
  h1 { font-size:18px; }
  label { display:block; font-size:12px; color:#666; margin:14px 0 4px; }
  input { width:100%; padding:8px 10px; border-radius:6px; border:1px solid #ccc; font-size:14px; box-sizing:border-box; }
  button { margin-top:18px; padding:8px 16px; border-radius:6px; border:none; background:#2a78d6; color:#fff; font-size:13px; cursor:pointer; }
  table { width:100%; border-collapse:collapse; font-size:13px; margin-top:12px; }
  td, th { padding:6px 4px; text-align:left; border-bottom:1px solid #ddd; }
  a { color:#2a78d6; }
</style>
"""


@app.route("/")
def dashboard():
    transacoes, saldo = carregar_transacoes_do_banco()
    return montar_html(transacoes, saldo)


@app.route("/transacao/<transacao_id>/editar", methods=["GET", "POST"])
def editar_transacao(transacao_id):
    with db.sessao() as conexao:
        if request.method == "POST":
            nova_descricao = request.form.get("descricao", "").strip()
            conexao.execute(
                "UPDATE transacoes SET description_custom = ? WHERE id = ?",
                (nova_descricao or None, transacao_id),
            )
            return redirect("/")

        row = conexao.execute(
            "SELECT description, description_custom, category, amount, date FROM transacoes WHERE id = ?",
            (transacao_id,),
        ).fetchone()

    if row is None:
        return "Transação não encontrada.", 404

    valor_atual = row["description_custom"] or ""
    return f"""{ESTILO_PAGINA_SIMPLES}
    <div class="wrap">
      <a class="voltar" href="/">&larr; voltar</a>
      <h1>Editar descrição da compra</h1>
      <p>Original: <strong>{row['description']}</strong><br>
         {traduzir_categoria(row['category'] or 'Outros')} · {fmt_brl(abs(row['amount']))} · {row['date'][:10]}</p>
      <form method="post">
        <label>Sua descrição (fica visível no lugar da original)</label>
        <input type="text" name="descricao" value="{valor_atual}" placeholder="ex: presente de aniversário da Maria">
        <button type="submit">Salvar</button>
      </form>
    </div>"""


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
            for chave, valor in request.form.items():
                if not chave.startswith("fixo__") or not valor.strip():
                    continue
                nome = chave[len("fixo__"):]
                try:
                    valor_float = float(valor.replace(",", "."))
                except ValueError:
                    continue
                conexao.execute(
                    "UPDATE gastos_fixos SET valor = ? WHERE mes = ? AND nome = ?",
                    (valor_float, mes, nome),
                )
            return redirect(f"/fixos/{mes}")

        linhas = conexao.execute(
            "SELECT nome, forma, valor FROM gastos_fixos WHERE mes = ? ORDER BY forma, nome", (mes,)
        ).fetchall()

    if not linhas:
        return f"Sem gastos fixos cadastrados pra {mes}. Rode o sync.py primeiro.", 404

    linhas_html = "".join(
        f"""<tr><td>{r['nome']} <small>({'Pix' if r['forma'] == 'pix' else 'Cartão'})</small></td>
             <td><input type="text" name="fixo__{r['nome']}" value="{r['valor']:.2f}"></td></tr>"""
        for r in linhas
    )
    return f"""{ESTILO_PAGINA_SIMPLES}
    <div class="wrap">
      <a class="voltar" href="/">&larr; voltar</a>
      <h1>Gastos fixos — {mes}</h1>
      <form method="post">
        <table>{linhas_html}</table>
        <button type="submit">Salvar</button>
      </form>
    </div>"""


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
