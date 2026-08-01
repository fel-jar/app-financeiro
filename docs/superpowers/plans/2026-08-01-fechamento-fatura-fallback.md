# Fallback de Fechamento de Fatura por Cartão — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Corrigir o mês de pagamento da fatura de cartão quando `billForecastDate` (campo calculado pela Pluggy) vem nulo, usando o dia de fechamento real de cada cartão (confirmado pelo usuário) como rede de segurança — e implantar a correção em produção.

**Architecture:** Módulo novo e isolado (`execution/fechamento_cartoes.py`) com uma tabela estática `account_id -> dia de fechamento` e uma função pura `mes_fechamento()`. Uma única linha em `execution/gerar_dashboard.py` passa a consultar essa função antes de cair na data crua da compra. Nenhuma mudança de schema, sync ou API.

**Tech Stack:** Python 3.12 (sem framework de teste no projeto — validação por script ad-hoc contra o banco real, mesmo padrão que o resto do projeto usa).

## Global Constraints

- Projeto sem suite de testes (`.github/workflows/build.yml` não roda testes) — não introduzir pytest/framework novo; usar assert simples, mesmo padrão do resto do código.
- Nunca sobrescrever o valor da Pluggy quando ele existe: `mes_fechamento()` é usada só como fallback (`billForecastDate` nulo).
- `mes_fechamento()` devolve a mesma convenção que `billForecastDate` cru: mês em que a fatura FECHA, não o mês em que é paga (o `+1` já existe em `gerar_dashboard.py` e não muda).
- Todo texto voltado ao usuário (mensagens de commit, comentários) em português.
- Deploy é ação em produção (VPS real, usuários reais) — só executar após os testes locais/validação passarem.

---

### Task 1: Módulo `fechamento_cartoes.py` com o mapa de fechamento e a função de cálculo

**Files:**
- Create: `execution/fechamento_cartoes.py`

**Interfaces:**
- Produces: `FECHAMENTO_POR_CARTAO: dict[str, int]` (account_id -> dia de fechamento, 1-31) e `mes_fechamento(account_id: str | None, data_compra: str) -> str | None`, usados por `gerar_dashboard.py` na Task 2.

- [ ] **Step 1: Criar o arquivo com o mapa e a função**

```python
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
```

- [ ] **Step 2: Rodar o self-check e confirmar que todos os casos passam**

Run: `cd execution && python fechamento_cartoes.py`
Expected: 7 linhas `OK: ...` e a linha final `Todos os casos passaram.` (exit code 0). Se alguma linha vier `FALHOU`, a função tem um bug de off-by-one na comparação `dia > dia_fechamento` ou na virada de mês/ano -- revisar antes de continuar.

- [ ] **Step 3: Commit**

```bash
git add execution/fechamento_cartoes.py
git commit -m "$(cat <<'EOF'
feat: fallback de fechamento de fatura configurado por cartão

Quando billForecastDate vem nulo da Pluggy (82% dos casos no ELO NANQUIM
PRIME, 27% no THE PLATINUM CARD, 17% no VISA INFINITE PRIME -- medido
contra o banco real), a Pluggy também não expõe o fechamento no objeto da
conta (balanceCloseDate sempre null). Dia de fechamento confirmado pelo
usuário contra a fatura real de cada cartão.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Usar o fallback em `construir_panorama_mensal`

**Files:**
- Modify: `execution/gerar_dashboard.py:1-15` (import), `execution/gerar_dashboard.py:257` (linha do fallback)

**Interfaces:**
- Consumes: `mes_fechamento(account_id, data_compra)` da Task 1.

- [ ] **Step 1: Adicionar o import no topo do arquivo**

Adicionar junto aos outros imports locais (perto de `from gastos_fixos import ...` ou similar, no topo de `execution/gerar_dashboard.py`):

```python
from fechamento_cartoes import mes_fechamento
```

- [ ] **Step 2: Trocar a linha do fallback**

Em `execution/gerar_dashboard.py:257`, trocar:

```python
        bill_raw = meta.get("billForecastDate") or t["date"][:7]
```

por:

```python
        bill_raw = meta.get("billForecastDate") or mes_fechamento(t.get("accountId"), t["date"]) or t["date"][:7]
```

- [ ] **Step 3: Atualizar a docstring de `construir_panorama_mensal` (linhas 170-172) que descreve o fallback antigo**

Trecho atual (por volta da linha 170):

```
    Além disso, a Pluggy nomeia a fatura pelo mês de FECHAMENTO/referência,
    não pelo mês em que ela é efetivamente paga -- o pagamento cai no mês
    seguinte ao nome que a Pluggy dá (confirmado pelo usuário: fatura que a
    Pluggy chama de "julho" é paga em agosto), por isso todo `bill` é
    deslocado +1 mês (`_mes_seguinte(bill, 1)`) antes de virar chave do
    painel. Transação de cartão sem `billForecastDate` (acontece bastante
    nos dados reais -- ver achado 2026-07-25) cai de volta na data da
    própria compra, pra nunca sumir uma despesa real do painel.
```

Trocar o final (a partir de "Transação de cartão sem `billForecastDate`") por:

```
    Transação de cartão sem `billForecastDate` (acontece bastante nos
    dados reais, com frequência bem diferente por cartão -- ver achado
    2026-07-25) usa o dia de fechamento configurado em
    `fechamento_cartoes.FECHAMENTO_POR_CARTAO` como fallback (achado
    2026-08-01: a Pluggy também não expõe o fechamento no objeto da conta,
    `creditData.balanceCloseDate` vem sempre `null`). Só cartão fora desse
    mapa cai de volta na data crua da própria compra, pra nunca sumir uma
    despesa real do painel.
```

- [ ] **Step 4: Rodar o self-check da Task 1 de novo, garantindo que o import não quebrou nada**

Run: `cd execution && python -c "import gerar_dashboard"`
Expected: sem erro (import silencioso, exit code 0).

- [ ] **Step 5: Commit**

```bash
git add execution/gerar_dashboard.py
git commit -m "$(cat <<'EOF'
fix(dashboard): usa dia de fechamento configurado quando Pluggy não informa

bill_raw caía direto na data da compra quando billForecastDate vinha
nulo, jogando compras feitas perto do fechamento no mês de fatura errado.
Agora passa primeiro pelo dia de fechamento real do cartão antes desse
último recurso.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Validar contra o banco de produção real (dados já sincronizados)

**Files:**
- Nenhum arquivo do projeto -- script ad-hoc no diretório scratchpad da sessão (não é deliverable, só validação).

**Interfaces:**
- Consumes: `dados_db.carregar_transacoes_do_banco()` (já existe), `fechamento_cartoes.mes_fechamento()` (Task 1).

- [ ] **Step 1: Escrever e rodar o script de comparação**

Salvar em `<diretório scratchpad da sessão>/validar_fechamento.py` (fora do repo) e rodar com `PYTHONPATH` apontando pra `execution/`:

```python
import sys
sys.path.insert(0, r"c:\Users\ingle\.claude\App finaceiro\execution")

from dados_db import carregar_transacoes_do_banco
from fechamento_cartoes import mes_fechamento, FECHAMENTO_POR_CARTAO

transacoes, _ = carregar_transacoes_do_banco()

nulos = [
    t for t in transacoes
    if t.get("creditCardMetadata") and t["type"] == "DEBIT" and t["amount"] < 0
    and not t["creditCardMetadata"].get("billForecastDate")
]

print(f"Total de transações de cartão com billForecastDate nulo: {len(nulos)}")

sem_mapa = [t for t in nulos if t["accountId"] not in FECHAMENTO_POR_CARTAO]
print(f"Sem cartão mapeado (cai no fallback antigo, data crua): {len(sem_mapa)}")

mudou_de_mes = 0
exemplos = []
for t in nulos:
    antigo = t["date"][:7]
    novo = mes_fechamento(t["accountId"], t["date"]) or antigo
    if novo != antigo:
        mudou_de_mes += 1
        if len(exemplos) < 10:
            exemplos.append((t["date"][:10], t.get("description"), antigo, novo))

print(f"Transações que mudam de mês de fatura com o fix: {mudou_de_mes}")
print()
print("Exemplos (data compra | descrição | mês antigo -> mês novo):")
for data, desc, antigo, novo in exemplos:
    print(f"  {data} | {desc} | {antigo} -> {novo}")
```

Run: `python "<diretório scratchpad da sessão>/validar_fechamento.py"`

- [ ] **Step 2: Conferir manualmente 2-3 dos exemplos impressos**

Pra cada exemplo de "mudou de mês", checar se a data da compra está de fato depois do dia de fechamento configurado daquele cartão (ex.: compra em `2026-06-27` num cartão com fechamento dia 25 deveria mesmo mudar de junho pra julho). Se algum exemplo não bater, revisar `FECHAMENTO_POR_CARTAO` ou a lógica de `mes_fechamento` antes de prosseguir -- não seguir pro deploy com uma inconsistência não explicada.

Expected: todos os exemplos conferidos fazem sentido (mudam de mês exatamente quando a data é posterior ao dia de fechamento do cartão).

- [ ] **Step 3: Nenhum commit nesta task** (script de validação não é deliverable do projeto, fica só no scratchpad da sessão).

---

### Task 4: Registrar o aprendizado na diretiva

**Files:**
- Modify: `directives/dashboard_fluxo_caixa.md` (seção "Achado importante sobre `billForecastDate`", por volta da linha 680-727, e a lista de achados relacionados)

**Interfaces:**
- Nenhuma (documentação).

- [ ] **Step 1: Adicionar um parágrafo logo após o achado existente sobre `billForecastDate` nulo**

Localizar o trecho (por volta da linha 680-727) que descreve:
> **Achado crítico — `billForecastDate` vem `NULL` com frequência nos dados reais**: [...] Corrigido com fallback: `bill_raw = meta.get("billForecastDate") or t["date"][:7]`.

Adicionar logo depois:

```
**Atualização 2026-08-01 — fallback melhorado com dia de fechamento configurado por cartão**:
a frequência do nulo varia MUITO por cartão, não é uniforme (medido contra
o banco real): 82% no ELO NANQUIM PRIME (final 4921), 27% no THE PLATINUM
CARD (final 3543), 17% no VISA INFINITE PRIME (final 0808), 0% no Mercado
Pago e no cartão "platinum" extra. Investiguei se dava pra pegar o
fechamento pela própria API (`GET /accounts` → `creditData.balanceCloseDate`)
em vez de cair direto na data da compra -- não dá, o campo vem sempre
`null` pros 5 cartões conectados aqui (só `balanceDueDate`, o vencimento,
vem preenchido). Solução: dia de fechamento configurado manualmente por
cartão (confirmado pelo usuário contra a fatura real), em
`execution/fechamento_cartoes.py::FECHAMENTO_POR_CARTAO`, usado como
fallback ANTES de cair na data crua da compra -- só entra em último caso
se o cartão nem estiver mapeado. Ver
`docs/superpowers/specs/2026-08-01-fechamento-fatura-fallback-design.md`
pro desenho completo.
```

- [ ] **Step 2: Commit**

```bash
git add directives/dashboard_fluxo_caixa.md
git commit -m "$(cat <<'EOF'
docs: registra o fallback de fechamento por cartão na diretiva

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Deploy em produção (VPS via GHCR + Docker Swarm)

**Files:**
- Modify: `deploy/app-financeiro-stack.yml` (arquivo real, gitignored, NÃO commitado -- só atualiza a tag da imagem local pra manter em sincronia com o que está rodando)

**Interfaces:**
- Nenhuma (infraestrutura).

- [ ] **Step 1: Push pra main (dispara o build no GHCR)**

```bash
git push origin main
```

- [ ] **Step 2: Pegar o SHA do commit que acabou de subir e aguardar o build no GitHub Actions**

```bash
git rev-parse HEAD
```

Depois checar em `https://github.com/fel-jar/app-financeiro/actions` que o workflow `build-and-push` terminou com sucesso pro commit acima (leva poucos minutos). Não prosseguir enquanto o workflow não aparecer `completed`/verde.

- [ ] **Step 3: Confirmar que a imagem nova chegou no GHCR**

```bash
curl -s -o /dev/null -w "%{http_code}\n" "https://ghcr.io/v2/fel-jar/app-financeiro/manifests/$(git rev-parse HEAD)"
```

Expected: `200` (ou `401` se o pacote precisar de auth pra HEAD anônimo -- nesse caso confirmar visualmente em Packages no GitHub em vez de insistir no curl).

- [ ] **Step 4: Atualizar a tag da imagem no arquivo de deploy real (local, não commitado)**

Editar `deploy/app-financeiro-stack.yml`: trocar as 3 ocorrências de `image: ghcr.io/fel-jar/app-financeiro:028f6a9ffe5383b4016fd893913549a38c96e906` pelo novo SHA obtido no Step 2, em `web`, `scheduler` e `agente`.

- [ ] **Step 5: Redeploy via SSH (só a imagem mudou, nenhum label/env/volume -- método 1, um `service update` por serviço)**

```bash
SHA=$(git rev-parse HEAD)
for servico in web scheduler agente; do
  ssh -i ~/.ssh/garmin_vps root@147.79.81.66 \
    "docker service update --image ghcr.io/fel-jar/app-financeiro:$SHA --with-registry-auth app-financeiro_$servico"
done
```

- [ ] **Step 6: Verificar que os 3 serviços subiram com a imagem nova**

```bash
for servico in web scheduler agente; do
  ssh -i ~/.ssh/garmin_vps root@147.79.81.66 \
    "docker service ps app-financeiro_$servico --no-trunc --format 'table {{.Image}}\t{{.CurrentState}}'" | head -3
done
```

Expected: pra cada serviço, a linha mais recente mostra a imagem com o SHA novo e `Running X seconds/minutes ago`; a réplica antiga aparece `Shutdown`/`Complete` logo abaixo.

- [ ] **Step 7: Smoke test do domínio público**

```bash
curl -s -o /dev/null -w "GET / -> %{http_code}\n" https://financaspessoais.pelotaopermax.com.br/
```

Expected: `200` em menos de 1s. Se vier `504` mesmo com os serviços `Running`, ver a pegadinha de rede/Traefik documentada em `~/.claude/PerMax/Sistema/directives/deploy_producao.md` (rede ambígua do Traefik).

- [ ] **Step 8: Sem commit nesta task** (o arquivo alterado no Step 4 é gitignored por conter segredos reais -- fica só atualizado localmente, não vai pro git).
