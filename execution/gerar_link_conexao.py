"""Gera conectar.html: a tela oficial do Pluggy Connect para o usuário logar
no banco/cartão com segurança e autorizar o acesso.

Uso: python execution/gerar_link_conexao.py
Depois: abrir conectar.html, conectar a conta, copiar o Item ID mostrado
na tela e colocar em PLUGGY_ITEM_ID no .env.
"""
from pathlib import Path

from pluggy_client import from_env

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "conectar.html"

HTML = """<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<title>Conectar conta - App Financeiro</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdn.pluggy.ai/pluggy-connect/v2.7.0/pluggy-connect.js"></script>
<style>
  body {{ font-family: system-ui, -apple-system, "Segoe UI", sans-serif; max-width: 480px;
         margin: 60px auto; padding: 0 20px; }}
  #resultado {{ display: none; margin-top: 24px; padding: 16px; border-radius: 8px;
               background: #eafaf0; border: 1px solid #0ca30c; }}
  #resultado code {{ display: block; margin-top: 8px; padding: 8px; background: #fff;
                     border-radius: 4px; word-break: break-all; }}
  button {{ padding: 10px 18px; border-radius: 6px; border: none; background: #2a78d6;
           color: #fff; font-size: 14px; cursor: pointer; }}
</style>
</head>
<body>
  <h1>Conectar sua conta</h1>
  <p>Clique no botão e faça login no seu banco/cartão pela tela oficial do Pluggy.
     Sua senha nunca passa pelo nosso script.</p>
  <button onclick="pluggyConnect.init()">Conectar conta</button>

  <div id="resultado">
    <strong>Conectado! Item ID:</strong>
    <code id="item-id"></code>
    <p>Copie esse valor e me envie (ou coloque direto em <code>PLUGGY_ITEM_ID</code> no .env).</p>
  </div>

  <script>
    const pluggyConnect = new PluggyConnect({{
      connectToken: "{connect_token}",
      includeSandbox: false,
      onSuccess: (itemData) => {{
        document.getElementById('item-id').textContent = itemData.item.id;
        document.getElementById('resultado').style.display = 'block';
      }},
      onError: (error) => {{
        alert('Erro ao conectar: ' + JSON.stringify(error));
      }},
    }});
  </script>
</body>
</html>
"""


def main():
    cliente = from_env()
    if cliente is None:
        raise SystemExit("Faltam PLUGGY_CLIENT_ID/PLUGGY_CLIENT_SECRET no .env.")

    token = cliente.create_connect_token()
    OUT_PATH.write_text(HTML.format(connect_token=token), encoding="utf-8")
    print(f"Gerado {OUT_PATH}. Abra esse arquivo no navegador (o token expira em pouco tempo).")


if __name__ == "__main__":
    main()
