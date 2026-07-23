"""Cliente HTTP para a API da Pluggy (Open Finance).

Requer PLUGGY_CLIENT_ID e PLUGGY_CLIENT_SECRET no .env.
Docs: https://docs.pluggy.ai
"""
import os
from urllib.parse import urlparse, parse_qs

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.pluggy.ai"


class PluggyClient:
    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self._api_key = None

    def _auth(self) -> str:
        if self._api_key:
            return self._api_key
        resp = requests.post(
            f"{BASE_URL}/auth",
            json={"clientId": self.client_id, "clientSecret": self.client_secret},
            timeout=30,
        )
        resp.raise_for_status()
        self._api_key = resp.json()["apiKey"]
        return self._api_key

    def _headers(self) -> dict:
        return {"X-API-KEY": self._auth()}

    def create_connect_token(self, client_user_id: str = "app-finaceiro-user") -> str:
        resp = requests.post(
            f"{BASE_URL}/connect_token",
            headers=self._headers(),
            json={"options": {"clientUserId": client_user_id}},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["accessToken"]

    def list_accounts(self, item_id: str) -> list[dict]:
        resp = requests.get(
            f"{BASE_URL}/accounts",
            headers=self._headers(),
            params={"itemId": item_id},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["results"]

    def list_transactions(self, account_id: str, start_date: str | None = None,
                           end_date: str | None = None) -> list[dict]:
        params = {"accountId": account_id}
        if start_date:
            params["startDate"] = start_date
        if end_date:
            params["endDate"] = end_date

        transactions = []
        cursor_params = dict(params)
        while True:
            resp = requests.get(
                f"{BASE_URL}/v2/transactions",
                headers=self._headers(),
                params=cursor_params,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            transactions.extend(data["results"])
            if not data.get("next"):
                break
            proximo = urlparse(data["next"])
            cursor_params = dict(params)
            cursor_params["after"] = parse_qs(proximo.query)["after"][0]
        return transactions


def from_env() -> PluggyClient | None:
    client_id = os.getenv("PLUGGY_CLIENT_ID")
    client_secret = os.getenv("PLUGGY_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None
    return PluggyClient(client_id, client_secret)
