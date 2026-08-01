"""Dia de fechamento de cada cartão -- usado só como rede de segurança
quando a Pluggy não devolve billForecastDate na transação (falha com
frequência bem diferente por cartão: 82% no ELO NANQUIM PRIME, 27% no THE
PLATINUM CARD, 17% no VISA INFINITE PRIME, medido em 2026-08-01 contra o
banco real -- ver directives/dashboard_fluxo_caixa.md).

Não dá pra pegar isso da própria API: `GET /accounts` devolve
`creditData.balanceCloseDate` sempre `null` para todos os cartões
conectados aqui (só `balanceDueDate`, o vencimento, vem preenchido) --
checado ao vivo em 2026-08-01. Por isso o dado é estático, confirmado pelo
usuário contra a fatura real de cada cartão.

Mapeado por account_id (não por nome) porque é o identificador estável que
o resto do projeto já usa (tabela `contas`, `transacoes.account_id`).
"""

FECHAMENTO_POR_CARTAO = {
    "3c1e88cf-1059-43ad-8378-af4af02eb8c8": 25,  # THE PLATINUM CARD (final 3543)
    "8a5b3f15-c600-40e2-8f84-40786cfd0f4a": 25,  # VISA INFINITE PRIME (final 0808)
    "6a3cf9d1-c5a1-4f82-a982-7fe6493d875a": 25,  # ELO NANQUIM PRIME (final 4921, esposa)
    "5bf53027-563f-4e09-aad7-447203dcfaa1": 25,  # Mercado Pago (final 2459)
    "8c035820-2158-460b-96c7-1e4560fa438d": 5,   # platinum (final 1400, item extra)
}


def mes_fechamento(account_id: str | None, data_compra: str) -> str | None:
    """AAAA-MM em que a fatura FECHA (mesma convenção do billForecastDate
    cru da Pluggy -- não é o mês em que é paga). None se o cartão não
    estiver mapeado, pra quem chama cair no fallback antigo (data crua da
    compra)."""
    dia_fechamento = FECHAMENTO_POR_CARTAO.get(account_id or "")
    if dia_fechamento is None:
        return None
    ano, mes, dia = (int(p) for p in data_compra[:10].split("-"))
    if dia > dia_fechamento:
        mes += 1
        if mes > 12:
            mes = 1
            ano += 1
    return f"{ano:04d}-{mes:02d}"


if __name__ == "__main__":
    # Sanity check manual (projeto não tem suite de testes -- ver
    # .github/workflows/build.yml). Rodar: python execution/fechamento_cartoes.py
    casos = [
        # (account_id, data_compra, esperado)
        ("3c1e88cf-1059-43ad-8378-af4af02eb8c8", "2026-07-25", "2026-07"),  # exatamente no dia de fechamento -> ainda entra
        ("3c1e88cf-1059-43ad-8378-af4af02eb8c8", "2026-07-26", "2026-08"),  # 1 dia depois -> mês seguinte
        ("3c1e88cf-1059-43ad-8378-af4af02eb8c8", "2026-01-01", "2026-01"),  # início de ano, dentro do fechamento
        ("8c035820-2158-460b-96c7-1e4560fa438d", "2026-07-05", "2026-07"),  # cartão com fechamento dia 5, exatamente no dia
        ("8c035820-2158-460b-96c7-1e4560fa438d", "2026-07-06", "2026-08"),  # 1 dia depois
        ("8c035820-2158-460b-96c7-1e4560fa438d", "2026-12-06", "2027-01"),  # virada de ano
        ("cartao-nao-mapeado", "2026-07-15", None),  # cartão desconhecido -> None
    ]
    falhas = 0
    for account_id, data_compra, esperado in casos:
        resultado = mes_fechamento(account_id, data_compra)
        status = "OK" if resultado == esperado else "FALHOU"
        if status == "FALHOU":
            falhas += 1
        print(f"{status}: mes_fechamento({account_id!r}, {data_compra!r}) = {resultado!r} (esperado {esperado!r})")
    if falhas:
        raise SystemExit(f"{falhas} caso(s) falharam")
    print("Todos os casos passaram.")
