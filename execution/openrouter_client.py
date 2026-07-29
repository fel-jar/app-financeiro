"""Cliente HTTP para a API da OpenRouter (chat completions, compatível com
o formato OpenAI, incluindo tool calling).

Requer OPENROUTER_API_KEY no .env. Modelo configurável via OPENROUTER_MODEL
(default: deepseek/deepseek-v4-pro -- escolha do usuário em 2026-07-25).
Docs: https://openrouter.ai/docs
"""
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://openrouter.ai/api/v1"
MODELO_PADRAO = "deepseek/deepseek-v4-pro"
TENTATIVAS_REDE = 3  # ex.: falha de DNS/conexão transitória no host
ESPERA_BASE_SEGUNDOS = 2  # backoff exponencial: 2s, 4s


class OpenRouterClient:
    def __init__(self, api_key: str, modelo: str):
        self.api_key = api_key
        self.modelo = modelo

    def _postar_com_retry(self, payload: dict) -> requests.Response:
        """POST com retry e backoff exponencial só em erro de rede (ex.:
        DNS falhou, conexão recusada) -- erros da API (4xx/5xx) não são
        retentados aqui, sobem direto pro chamador tratar."""
        for tentativa in range(1, TENTATIVAS_REDE + 1):
            try:
                return requests.post(
                    f"{BASE_URL}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=60,
                )
            except (requests.ConnectionError, requests.Timeout):
                if tentativa == TENTATIVAS_REDE:
                    raise
                time.sleep(ESPERA_BASE_SEGUNDOS * tentativa)
        raise AssertionError("inalcançável")  # loop sempre retorna ou levanta

    def chat(self, mensagens: list[dict], ferramentas: list[dict] | None = None) -> dict:
        """Uma chamada de chat completion. Retorna o dict `message` da
        resposta (role, content, e `tool_calls` quando o modelo decide
        chamar uma ferramenta em vez de responder direto)."""
        payload = {"model": self.modelo, "messages": mensagens}
        if ferramentas:
            payload["tools"] = ferramentas
            payload["tool_choice"] = "auto"

        resp = self._postar_com_retry(payload)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]


def from_env() -> OpenRouterClient | None:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return None
    modelo = os.getenv("OPENROUTER_MODEL", MODELO_PADRAO)
    return OpenRouterClient(api_key, modelo)
