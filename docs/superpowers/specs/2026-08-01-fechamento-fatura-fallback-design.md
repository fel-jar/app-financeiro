# Fallback de fechamento de fatura por cartão

## Problema

`construir_panorama_mensal()` (`execution/gerar_dashboard.py:257`) decide em qual mês uma compra de cartão cai na fatura usando `creditCardMetadata.billForecastDate`, campo calculado pela Pluggy. Quando esse campo vem `NULL` -- o que acontece com frequência bem diferente por cartão (82% no ELO NANQUIM PRIME, 27% no THE PLATINUM CARD, 17% no VISA INFINITE PRIME, 0% no Mercado Pago e no cartão "platinum" extra, medido em 2026-08-01 contra o banco real) -- o código cai de volta pra data crua da compra (`t["date"][:7]`), o que pode jogar a despesa no mês errado da fatura quando a compra foi feita perto do fechamento.

Não existe, hoje, nenhuma fonte alternativa confiável: `GET /accounts` da Pluggy devolve `creditData.balanceCloseDate` sempre `null` para os 5 cartões conectados (confirmado ao vivo em 2026-08-01) -- só `balanceDueDate` (vencimento) vem preenchido, e vencimento não é fechamento.

## Solução

Dia de fechamento configurado manualmente por cartão (informado pelo usuário, confirmado contra a fatura real), usado só como *fallback* quando `billForecastDate` vier nulo -- nunca substitui o valor da Pluggy quando ele existe.

### `execution/fechamento_cartoes.py` (novo arquivo)

```python
FECHAMENTO_POR_CARTAO = {
    "3c1e88cf-1059-43ad-8378-af4af02eb8c8": 25,  # THE PLATINUM CARD (final 3543)
    "8a5b3f15-c600-40e2-8f84-40786cfd0f4a": 25,  # VISA INFINITE PRIME (final 0808)
    "6a3cf9d1-c5a1-4f82-a982-7fe6493d875a": 25,  # ELO NANQUIM PRIME (final 4921, esposa)
    "5bf53027-563f-4e09-aad7-447203dcfaa1": 25,  # Mercado Pago (final 2459)
    "8c035820-2158-460b-96c7-1e4560fa438d": 5,   # platinum (final 1400, item extra)
}

def mes_fechamento(account_id: str, data_compra: str) -> str | None:
    """AAAA-MM em que a fatura FECHA (mesma convenção do billForecastDate
    cru da Pluggy -- não é o mês de pagamento). None se o cartão não
    estiver mapeado, pra quem chama cair no fallback antigo."""
```

Regra: dia da compra <= dia de fechamento -> cai na fatura que fecha nesse mês; dia > fechamento -> fatura do mês seguinte.

Chave por `account_id` porque é o identificador estável que o resto do projeto já usa (tabela `contas`, `transacoes.account_id`).

### Mudança em `execution/gerar_dashboard.py:257`

```python
bill_raw = meta.get("billForecastDate") or mes_fechamento(t.get("accountId"), t["date"]) or t["date"][:7]
```

Cadeia de prioridade: Pluggy > nosso cálculo > data crua da compra (último recurso, só se o cartão não estiver mapeado). `mes_fechamento` devolve o mesmo formato/convenção (`AAAA-MM`, mês de fechamento) que `billForecastDate` já tinha, então todo o resto da função (`_mes_seguinte(bill_raw, 1)`, comparação `bill_raw == mes_atual`, projeção de parcelas futuras) continua funcionando sem nenhuma mudança adicional.

Nenhuma mudança em `sync.py`, `dados_db.py` ou schema -- `accountId` já chega até `construir_panorama_mensal`.

## Fora de escopo

- UI/tabela no banco para editar o fechamento (dado estático, muda raríssimo -- YAGNI).
- Confiar em `balanceCloseDate` da Pluggy (sempre nulo nos dados reais, não é uma fonte viável).
- Qualquer mudança em `gasto_cartao_por_mes()` (métrica deliberadamente por data da compra, não da fatura -- não relacionada a este fix).

## Validação

Rodar contra o banco real (`dados/app_financeiro.db`) comparando, para as transações hoje com `billForecastDate` nulo, o mês que o fallback antigo (data crua) dava vs. o mês que `mes_fechamento` passa a dar -- e conferir alguns casos manualmente.
