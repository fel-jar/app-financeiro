"""Agente conversacional no Telegram: o usuário conversa (pergunta gasto de
ontem, pede pra renomear/recategorizar uma compra, pergunta se o mês
fecha) e o LLM (via OpenRouter) decide quais ferramentas de
`agente_ferramentas.py` chamar pra responder ou executar a ação.

Uso: python execution/agente_llm.py  (roda em loop, long polling no
Telegram -- pensado pra rodar como serviço separado 24/7, igual
scheduler.py, não junto com o gunicorn do dashboard).

Requer no .env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (mesmos do
telegram_diario.py), OPENROUTER_API_KEY (e opcionalmente OPENROUTER_MODEL).
"""
import json
import os
import sys
import time
from datetime import datetime

import requests
from dotenv import load_dotenv

import db
import openrouter_client
from agente_ferramentas import CATEGORIAS_VALIDAS, EXECUTORES, FERRAMENTAS

load_dotenv()

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

TELEGRAM_BASE = "https://api.telegram.org/bot{token}/{metodo}"
MAX_MENSAGENS_CONTEXTO = 40  # histórico carregado do banco pra dar memória entre reinícios
MAX_IDAS_FERRAMENTA = 6  # trava de segurança contra loop de tool-calling
CHAVE_OFFSET = "telegram_agente_offset"


def _system_prompt() -> str:
    hoje = datetime.now().strftime("%A, %d/%m/%Y")
    return (
        "Você é o assistente financeiro da família, conversando num grupo do Telegram "
        "com o usuário e a esposa dele -- os dois são donos legítimos dessas finanças "
        "(conta corrente conjunta + cartão de cada um). Cada mensagem do grupo chega "
        "prefixada com '[Nome]: ', identificando quem falou -- use isso só pra entender "
        "contexto (ex.: 'meu cartão' pode ser de qualquer um dos dois), não repita o "
        "prefixo na resposta. "
        f"Hoje é {hoje}. Responda sempre em português do Brasil, direto e objetivo "
        "(sem enrolação, sem repetir a pergunta). Valores em R$ com vírgula decimal.\n\n"
        "Use as ferramentas disponíveis pra consultar dados reais (nunca invente valores "
        "ou datas) e pra executar ações que qualquer um dos dois pedir (renomear uma "
        "compra, mudar categoria). Regras importantes:\n"
        "- 'gasto de ontem/hoje/esta semana/mês' -> use consultar_gastos com o período certo.\n"
        "- Se o usuário disser algo como 'esse gasto aqui, muda o nome/categoria' "
        "referindo-se a algo já mostrado na conversa, use o 'id' que já apareceu no "
        "resultado de uma consultar_gastos anterior -- não peça o id de novo se já está "
        "no histórico.\n"
        "- Se consultar_gastos encontrar mais de uma transação compatível com o que o "
        "usuário descreveu (nome ambíguo), NÃO escolha sozinho -- liste as opções e "
        "pergunte qual é.\n"
        "- Se encontrar exatamente uma transação compatível, pode aplicar a edição direto "
        "e depois confirmar o que foi feito (não peça confirmação antes -- só avise "
        "depois).\n"
        f"- Grande categoria só pode ser uma destas: {', '.join(CATEGORIAS_VALIDAS)}.\n"
        "- Perguntas sobre 'o mês fecha', 'dá pra cobrir', projeção de caixa -> use "
        "consultar_painel_mensal.\n"
        "- Se pedirem pra atualizar/sincronizar os dados agora (fora do horário fixo "
        "diário), use sincronizar_agora -- avise que pode levar alguns segundos antes de "
        "chamar, e confirme quantas transações vieram depois."
    )


def _telegram(metodo: str, token: str, **params) -> dict:
    resp = requests.get(TELEGRAM_BASE.format(token=token, metodo=metodo), params=params, timeout=35)
    resp.raise_for_status()
    return resp.json()


def enviar_mensagem(token: str, chat_id: str, texto: str):
    # Telegram limita 4096 caracteres por mensagem.
    for i in range(0, len(texto), 4000):
        _telegram("sendMessage", token, chat_id=chat_id, text=texto[i:i + 4000])


def _executar_ferramenta(nome: str, argumentos_json: str) -> str:
    funcao = EXECUTORES.get(nome)
    if funcao is None:
        return json.dumps({"erro": f"ferramenta desconhecida: {nome}"})
    try:
        argumentos = json.loads(argumentos_json) if argumentos_json else {}
    except json.JSONDecodeError:
        return json.dumps({"erro": "argumentos inválidos (JSON malformado)."})
    try:
        resultado = funcao(**argumentos)
    except TypeError as e:
        resultado = {"erro": f"argumentos inválidos pra {nome}: {e}"}
    except Exception as e:
        resultado = {"erro": f"falha ao executar {nome}: {e}"}
    return json.dumps(resultado, ensure_ascii=False)


def responder(cliente: openrouter_client.OpenRouterClient, chat_id: str, texto_usuario: str) -> str:
    agora = datetime.now().isoformat()
    with db.sessao() as conexao:
        historico = db.carregar_mensagens_agente(conexao, chat_id, MAX_MENSAGENS_CONTEXTO)
        mensagem_usuario = {"role": "user", "content": texto_usuario}
        db.gravar_mensagem_agente(conexao, chat_id, mensagem_usuario, agora)

    mensagens = [{"role": "system", "content": _system_prompt()}] + historico + [mensagem_usuario]
    novas_mensagens = []

    for _ in range(MAX_IDAS_FERRAMENTA):
        resposta = cliente.chat(mensagens, ferramentas=FERRAMENTAS)
        mensagens.append(resposta)
        novas_mensagens.append(resposta)

        tool_calls = resposta.get("tool_calls")
        if not tool_calls:
            texto_final = resposta.get("content") or "Não consegui gerar uma resposta agora."
            with db.sessao() as conexao:
                for m in novas_mensagens:
                    db.gravar_mensagem_agente(conexao, chat_id, m, datetime.now().isoformat())
            return texto_final

        for chamada in tool_calls:
            resultado = _executar_ferramenta(
                chamada["function"]["name"], chamada["function"].get("arguments", "{}")
            )
            mensagem_tool = {
                "role": "tool",
                "tool_call_id": chamada["id"],
                "name": chamada["function"]["name"],
                "content": resultado,
            }
            mensagens.append(mensagem_tool)
            novas_mensagens.append(mensagem_tool)

    with db.sessao() as conexao:
        for m in novas_mensagens:
            db.gravar_mensagem_agente(conexao, chat_id, m, datetime.now().isoformat())
    return "Precisei de passos demais pra responder isso -- tenta reformular de um jeito mais direto?"


def processar_atualizacao(cliente, token: str, chat_id_permitido: str, atualizacao: dict):
    mensagem = atualizacao.get("message") or atualizacao.get("edited_message")
    if not mensagem:
        return
    chat_id = str(mensagem["chat"]["id"])
    if chat_id != chat_id_permitido:
        print(f"Ignorando mensagem de chat não autorizado: {chat_id}")
        return

    texto = mensagem.get("text")
    if not texto:
        enviar_mensagem(token, chat_id, "Só entendo texto por enquanto.")
        return

    remetente = mensagem.get("from") or {}
    nome_remetente = remetente.get("first_name") or remetente.get("username") or "Alguém"
    texto_com_remetente = f"[{nome_remetente}]: {texto}"

    try:
        texto_resposta = responder(cliente, chat_id, texto_com_remetente)
    except requests.HTTPError as e:
        texto_resposta = f"Deu erro ao falar com o modelo (OpenRouter): {e}"
    except Exception as e:
        texto_resposta = f"Deu erro inesperado processando isso: {e}"
    enviar_mensagem(token, chat_id, texto_resposta)


def main():
    db.inicializar()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id_permitido = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id_permitido:
        raise SystemExit("Faltam TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID no .env.")

    cliente = openrouter_client.from_env()
    if cliente is None:
        raise SystemExit("Falta OPENROUTER_API_KEY no .env.")

    with db.sessao() as conexao:
        offset = int(db.obter_meta(conexao, CHAVE_OFFSET) or "0")

    print(f"Agente rodando (modelo {cliente.modelo}), aguardando mensagens no Telegram...")
    while True:
        try:
            dados = _telegram("getUpdates", token, offset=offset, timeout=25)
        except requests.RequestException as e:
            print(f"Erro ao consultar Telegram, tentando de novo em 5s: {e}")
            time.sleep(5)
            continue

        for atualizacao in dados.get("result", []):
            offset = atualizacao["update_id"] + 1
            with db.sessao() as conexao:
                db.definir_meta(conexao, CHAVE_OFFSET, str(offset))
            try:
                processar_atualizacao(cliente, token, chat_id_permitido, atualizacao)
            except Exception as e:
                print(f"Erro processando atualização {atualizacao.get('update_id')}: {e}")


if __name__ == "__main__":
    main()
