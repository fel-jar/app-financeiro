"""Cliente HTTP para a API da OpenRouter (chat completions, compatível com
o formato OpenAI, incluindo tool calling).

Requer OPENROUTER_API_KEY no .env. Modelo configurável via OPENROUTER_MODEL
(default: deepseek/deepseek-v4-pro -- escolha do usuário em 2026-07-25).
Docs: https://openrouter.ai/docs
"""
import os

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://openrouter.ai/api/v1"
MODELO_PADRAO = "deepseek/deepseek-v4-pro"


class OpenRouterClient:
    def __init__(self, api_key: str, modelo: str):
        self.api_key = api_key
        self.modelo = modelo

    def chat(self, mensagens: list[dict], ferramentas: list[dict] | None = None) -> dict:
        """Uma chamada de chat completion. Retorna o dict `message` da
        resposta (role, content, e `tool_calls` quando o modelo decide
        chamar uma ferramenta em vez de responder direto)."""
        payload = {"model": self.modelo, "messages": mensagens}
        if ferramentas:
            payload["tools"] = ferramentas
            payload["tool_choice"] = "auto"
        resp = requests.post(
            f"{BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]


def from_env() -> OpenRouterClient | None:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return None
    modelo = os.getenv("OPENROUTER_MODEL", MODELO_PADRAO)
    return OpenRouterClient(api_key, modelo)
