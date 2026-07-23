# Diretiva: Dashboard de Fluxo de Caixa

## Objetivo
Gerar um dashboard HTML local com foco em **controle presente e futuro**, não
histórico: caixa disponível hoje, gastos fixos do mês, fatura de cartão atual
e projeção das parcelas em aberto nos próximos meses — para organizar o caixa
e planejar a quitação do saldo devedor. O histórico (receitas x despesas por
mês, top categorias) continua disponível, mas recolhido no fim da página —
não é o foco (pedido explícito do usuário em 2026-07-22: "não quero saber o
que passou, quero ver os gastos atuais recorrentes, as parcelas futuras de
cartão e o que tenho em caixa").

## Status atual (atualizado 2026-07-22) — PLUGGY REAL FUNCIONANDO
- **Confirmado: `meu.pluggy.ai` dá acesso a dados reais sem aprovação de
  produção.** O item `34472778-450a-4e0f-ac24-24cc8b05a79c` (conector
  "MeuPluggy", `institutionUrl: https://meu.pluggy.ai/`) tem
  `"isSandbox": false` na resposta de `GET /items/{id}` — é dado real do
  Bradesco do usuário (Felipe Jardel Santana Lima), não sandbox. **Correção
  de um erro meu anterior**: eu tinha concluído que esse item era sintético
  só pela aparência (nome "FELIPE JARDEL..." parecia genérico, limite
  redondo R$63.000, nomes de cartão "VISA INFINITE PRIME"/"THE PLATINUM
  CARD" pareciam fictícios) — **isso estava errado**. O sinal correto e
  verificável é o campo `connector.isSandbox` da API, nunca a aparência dos
  dados. Não inferir "é fake" por padrão visual de novo — sempre checar
  `GET /items/{itemId}` e olhar `isSandbox`.
- Fluxo real: usuário cria conta grátis em meu.pluggy.ai, conecta o banco
  com login de verdade (Open Finance real), depois vincula esse item à
  aplicação do dashboard.pluggy.ai — a partir daí o item é consultável via
  API normal (`GET /accounts?itemId=`, `GET /v2/transactions`) com o
  Client ID/Secret que já estava no `.env`, mesmo sem status de produção
  aprovado (a restrição `TRIAL_CLIENT_ITEM_CREATE_NOT_ALLOWED` só bloqueia
  **criar** item pelo Pluggy Connect direto do nosso client; não bloqueia
  **ler** um item já criado via meu.pluggy.ai).
- `PLUGGY_ITEM_ID=34472778-450a-4e0f-ac24-24cc8b05a79c` já está no `.env`.
  Esse item tem 1 conta corrente + 2 cartões de crédito, ~2440 transações
  reais após filtro. `gerar_dashboard.py` agora usa Pluggy como fonte
  primária (antes do e-mail), automático, sem depender do MacroDroit no
  celular.
- **Pipeline de e-mail (MacroDroid → Gmail → IMAP) também testado e
  funcionando** com compra real (R$1,06, "99*", cartão 4807) — fica como
  fallback caso o item Pluggy pare de sincronizar ou o usuário troque de
  banco/cartão ainda não conectado no meu.pluggy.ai.

## Status atual (2026-07-22, parte 7) — PRODUÇÃO: BANCO + APP + TELEGRAM
Objetivo final do usuário: um agente mandando mensagem diária no Telegram
com gasto do dia, categoria, quanto ainda cabe no orçamento daquela
categoria, e quanto tem de caixa no mês. Isso exige persistência real
(dados que sobrevivem entre execuções, editáveis) -- um HTML estático
gerado do zero a cada rodada não permite isso. Arquitetura nova:

```
Pluggy API
   │  (1x/dia, via scheduler.py)
   ▼
sync.py ──────────► SQLite (dados/app_financeiro.db)
                          │              │
                          ▼              ▼
                     app.py (Flask)  telegram_diario.py
                     dashboard web    (1x/dia, mesmo scheduler)
                     + edição              │
                                           ▼
                                     mensagem no Telegram
```

- **`execution/db.py`**: schema SQLite. Tabelas: `transacoes` (com
  `description_custom` -- override editável, nunca sobrescrito pelo sync),
  `contas` (saldo/limite por conta, snapshot atual), `gastos_fixos`
  (`mes`, `nome`, `forma`, `valor` -- editável por mês, diferente do
  `gastos_fixos.py` estático que só serve de seed inicial),
  `orcamento_categoria` (`categoria`, `limite_mensal`, `origem` --
  seedado com média histórica, editável).
- **`execution/normalizacao.py`**: extraído de `gerar_dashboard.py` (que
  antes tinha essa lógica embutida) -- `normalizar_transacoes_pluggy()`,
  `CATEGORIAS_MOVIMENTACAO_INTERNA`, tradução de categoria. Usado tanto
  por `sync.py` quanto por `gerar_dashboard.py` (modo estático antigo,
  ainda funciona, mantido como fallback/backup manual).
- **`execution/sync.py`**: puxa da Pluggy, grava/atualiza no banco.
  **Idempotente e testado**: rodar 2x não duplica nada (upsert por
  `id`/`(mes,nome)`/`categoria`), e uma edição manual (`description_custom`,
  valor de fixo, orçamento) **sobrevive a um novo sync** -- testado na
  prática em 2026-07-22 (editei uma transação, rodei o sync de novo, valor
  continuou lá). Roda 1x/dia via `scheduler.py`.
- **`execution/dados_db.py`**: recarrega transações do banco no mesmo
  formato de dict que a Pluggy devolve, pra reaproveitar toda a lógica de
  agregação/render de `gerar_dashboard.py` sem duplicar (o `creditCardMetadata`
  é reconstruído a partir das colunas `installment_current/total` +
  `bill_forecast_date` quando `account_type == 'CREDIT'`).
- **`execution/app.py`**: Flask. `GET /` serve o dashboard (mesmo HTML de
  sempre, só que os dados vêm do banco, não de chamada ao vivo pra
  Pluggy -- mais rápido, e reflete edições). Rotas de edição:
  - `GET/POST /transacao/<id>/editar` -- descrição customizada por compra
    (tem um link "✎" em cada item nas listas expansíveis do dashboard).
  - `GET/POST /orcamento` -- edita o limite mensal por categoria (usado
    pelo Telegram pra calcular "quanto ainda pode gastar").
  - `GET/POST /fixos/<mes>` -- edita valor de um gasto fixo específico
    daquele mês (ex.: luz variou, editar só naquele mês sem afetar os
    outros).
  Testado localmente: build sobe, `/` retorna 200 com dados reais do
  banco, edição de transação via POST persiste e é confirmada por query
  direta no SQLite.
- **`execution/telegram_diario.py`**: monta e manda a mensagem diária
  (gasto de hoje por categoria + quanto ainda cabe no orçamento daquela
  categoria + caixa disponível + total gasto no mês). Só lê do banco
  (não chama a Pluggy). **Testado a montagem da mensagem** (sem enviar,
  faltam credenciais) -- formato e números conferidos, batem com o banco.
  Requer `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` no `.env` (instruções de
  como conseguir estão no docstring do arquivo).
- **`execution/scheduler.py`**: loop simples (sem cron do sistema, sem
  APScheduler) que dorme até um horário fixo (`HORARIO_DIARIO = "08:00"`)
  e roda sync + Telegram todo dia. Roda como processo/container
  separado do `app.py` (serviço `scheduler` no `docker-compose.yml`).
- **Dockerfile + docker-compose.yml**: dois serviços a partir da mesma
  imagem (`web` = gunicorn servindo o Flask; `scheduler` = o loop
  diário), volume `dados` persistindo o SQLite entre deploys. **O
  compose é um template** -- rede do Traefik, domínio e tag da imagem no
  GHCR são placeholder, preciso ajustar pra bater com o stack real do
  PerMax na hora de subir (não tenho acesso ao arquivo real do PerMax
  neste projeto pra copiar exato).
- **Docker testado localmente em 2026-07-22**: build limpo (`docker build`
  sem erro), serviço `web` sobe via gunicorn e `GET /` responde 200 com
  dados reais do volume montado, serviço `scheduler` sobe sem erro de
  import e calcula corretamente o próximo horário de execução. Testei
  rodando os containers direto (`docker run`), não via `docker-compose`
  (não tentei subir o compose completo, só validei que a imagem funciona).
- **Pendente antes de ir pra VPS**: `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`
  do usuário (obrigatório pro `telegram_diario.py` funcionar); confirmar
  valores de `orcamento_categoria` (hoje são só sugestão por média
  histórica, o usuário pode querer metas diferentes); ajustar
  `docker-compose.yml` pra bater com a rede/domínio/imagem GHCR reais do
  stack do PerMax antes de subir de verdade.

## Status atual (atualizado 2026-07-22, parte 6) — FIXOS NO PAINEL, CATEGORIAS PT-BR
- A pedido do usuário, os fixos (Pix/cartão recorrente) saíram de cards
  soltos na frente e foram pra **dentro de cada card de mês** do painel,
  como `<details>` clicáveis (`render_fixos_detalhe()`), junto com a
  fatura do cartão e a composição da entrada prevista
  (`render_entrada_detalhe()`, mostra salário médio + renda extra médio
  separados). Motivo: fixos variam mês a mês (combustível, luz), então
  fica mais natural ver/editar dentro do mês específico do que numa lista
  genérica única -- ainda usa `GASTOS_FIXOS` (mesmo valor todo mês) até
  ter persistência pra valores por mês (ver seção de próximos passos).
- **Fórmula do "total necessário" simplificada**: `fixos_pix + fatura`
  (2 termos só). A fatura do cartão passou a já EMBUTIR o fixo recorrente
  do cartão nos meses futuros (`fatura_mes = projecao + total_cartao_fixo`
  quando `i > 0`); no mês atual a fatura real já naturalmente inclui esses
  fixos (foram cobrados de verdade). O card "Fixos no cartão recorrente"
  dentro do mês é só informativo/editável -- **não soma separado**, senão
  duplicava.
- **Categorias traduzidas pra português** (`CATEGORIAS_PT` +
  `traduzir_categoria()`) em todo lugar que exibe categoria pro usuário:
  breakdown da fatura, top categorias histórico, gastos do mês. A
  tradução só acontece na hora de EXIBIR -- os filtros que comparam contra
  `CATEGORIAS_MOVIMENTACAO_INTERNA` (que são em inglês, vindos direto da
  API) continuam rodando sobre a categoria original, antes da tradução.

## Status atual (atualizado 2026-07-22, parte 5) — PAINEL MÊS A MÊS
- Reestruturação grande a pedido do usuário: em vez de números soltos no
  topo, o dashboard agora abre com um **painel mês a mês**
  (`construir_panorama_mensal()`), um card por mês (atual + 5 seguintes),
  cada um mostrando fatura de cartão, fixos, total necessário, entrada
  prevista (salário + renda extra) e um **saldo de caixa projetado
  rodando de mês a mês** (o saldo final de um mês vira o "caixa no
  início" do próximo) -- com badge "Cobre"/"Não cobre".
- **Achado 2026-07-22**: no ritmo atual, jul/26 e ago/26 cobrem, mas a
  partir de **set/26 o caixa projetado fica negativo** ("Não cobre") e
  segue assim até pelo menos dez/26 -- a folga de caixa hoje (R$18,5k) se
  esgota em ~2 meses se nada mudar. Isso é novidade importante: antes só
  olhávamos mês a mês isolado, agora dá pra ver o acúmulo.
- Cada card do painel tem um `<details>` "Ver o que compõe a fatura do
  cartão" com as compras agrupadas por categoria (barra + valor), e cada
  categoria é ela mesma um `<details>` que abre a lista de compras
  individuais (com a parcela, ex.: "AMAZON BR (5/10)"). Funciona tanto pro
  mês atual (compras reais) quanto pros meses futuros (parcelas já
  parceladas que vão cair naquela fatura).
- Gastos fixos agora são **dois cards expansíveis na frente** (não uma
  tabela única lá embaixo): "Gastos fixos no Pix" e "Gastos fixos no
  cartão", cada um com resumo (nome + total) no `<summary>` e a lista
  completa dentro. `render_fixos_card()`.
- Função antiga `fatura_atual_e_projecao()` foi **removida** -- toda a
  lógica de fatura atual + projeção de parcelas + detalhe por transação
  agora mora em `construir_panorama_mensal()`, que é superset dela.
- Seções que existiam soltas (tiles "fatura do mês"/"sobra estimada",
  tabela combinada de fixos, gráfico "parcelas em aberto") foram
  **removidas** por ficarem redundantes com o painel novo -- não foram só
  duplicadas, a informação foi incorporada no painel de forma mais
  completa (com o saldo projetado acumulado, que as versões antigas não
  tinham).

## Status atual (atualizado 2026-07-22, parte 4) — RENDA EXTRA E TENDÊNCIA
- **Renda extra identificada e confirmada pelo usuário**: PIX recebidos de
  "PERMAX CONSULTORIA LTDA" são pagamento de cliente da assessoria
  esportiva (renda extra real, varia ~R$1-3k/mês). A Pluggy categoriza
  esses PIX como `Transfer - PIX` (igual transferência entre contas
  próprias), então antes eram descartados junto com a movimentação
  interna. Corrigido: em `normalizar_transacoes_pluggy()`, transações
  `Transfer - PIX` do tipo `CREDIT` cuja descrição contém
  `FONTE_RENDA_EXTRA` ("PERMAX CONSULTORIA") são recategorizadas pra
  `"Renda extra"` e mantidas, em vez de descartadas. **Cuidado**: outros
  remetentes de PIX no extrato (ex.: "Ingledi Nayara Rodrig", "FELIPE J
  SANTANA LIMA" mandando pra si mesmo) são transferência
  familiar/pessoal, não renda -- continuam excluídos. Se a fonte de renda
  extra mudar (novo cliente, outro nome), atualizar `FONTE_RENDA_EXTRA`
  em `gerar_dashboard.py`.
- Nova seção "Salário + renda extra x gastos fixos": mostra a renda extra
  média (últimos meses fechados, via `renda_extra_media_recente()`), a
  renda extra mínima necessária pra fechar só os fixos
  (`max(0, total_fixo - salario_medio)`), e a sobra considerando os dois.
  **Achado 2026-07-22**: sobra fixa sozinha é -R$906,00 (déficit), mas
  com a renda extra média (R$2.335,26) sobra +R$1.429,25 -- a renda extra
  já cobre o buraco dos fixos, mesmo antes de contar gasto variável.
- Nova seção "Gasto no cartão por mês" (`gasto_cartao_por_mes()`): soma o
  valor gasto no cartão pela DATA DA COMPRA (não pela fatura prevista),
  mês a mês -- serve pra responder diretamente "o sangramento tá estancando?".
  **Achado 2026-07-22**: pico em dez/25-abr/26 (R$21-24k/mês), caindo pra
  R$18,5k em mai/26 e R$12,7k em jun/26 -- tendência de queda real, sinal
  positivo. Jul/26 estava parcial no momento da checagem (mês em curso,
  não comparável ainda).
- Prioridade do usuário confirmada: por ora, o objetivo é **estancar o
  sangramento** (parar de aumentar a dívida do cartão), não montar ainda
  um cronograma agressivo de quitação. Gastos fixos como financiamento do
  carro e IR são temporários (terminam no fim do ano) -- não cortar/ignorar
  agora, mas já esperar a sobra fixa melhorar quando isso acabar.

## Status atual (atualizado 2026-07-22, parte 3) — DIAGNÓSTICO E GASTO DO MÊS
- `execution/gastos_fixos.py` agora tem campo `forma` ("pix"/"cartao") por
  item, definido pelo usuário: Pix = Psicóloga, Financiamento carro,
  Internet, Condomínio, Luz, PNR, IR. Cartão = todos os outros (Vivo,
  Faculdade, Escolinha do Guel, Tim, Combustível, Mercado, YouTube,
  Spotify, Smiles). Isso importa porque os fixos no cartão já estão
  embutidos na fatura atual puxada da Pluggy -- somar de novo duplicava a
  "sobra estimada". `total_fixo_pix()` é o valor certo a somar por fora.
- Nova seção "Salário x gastos fixos": `salario_medio_recente()` calcula a
  média do salário dos últimos 3 meses **fechados** (exclui o mês atual,
  que fica parcial) usando a categoria real `Salary` das transações da
  conta corrente -- evita usar um mês com bônus/13º como se fosse típico.
- **Achado crítico (2026-07-22)**: salário médio R$ 10.563,53 vs total de
  gastos fixos R$ 11.469,53 → sobra fixa de **-R$ 906,00 antes de qualquer
  gasto variável** (mercado/lazer/etc. já estão dentro do valor fixo pela
  faixa conservadora, mas Shopping/Bookstore/Eletrônicos/etc. são
  variáveis e somam mais ainda). Ou seja, o déficit não é só de gasto
  descontrolado -- os fixos sozinhos já não cabem no salário. Qualquer
  "plano macro" de quitação de dívida depende de decisão do usuário sobre
  o que cortar/renegociar ou aumentar de renda; não adianta só cortar
  variável.
- Nova seção "Gastos do mês atual por categoria" + "Compra a compra — mês
  atual": usa `transacoes_mes_atual()` (filtra pelo mês corrente) e
  `agregar_categorias_despesa()` reaproveitado com `top_n=999` pra listar
  todas as categorias, não só o top 5. `compras_mes` lista cada transação
  individual do mês, ordenada da mais recente pra mais antiga.
- Dívida real nos cartões (consultado via `GET /accounts`, campo
  `creditData`): VISA INFINITE PRIME limite R$63.000, usado R$40.994,32,
  disponível R$22.005,68, pagamento mínimo R$1.222,74 (venc. 2026-07-08).
  THE PLATINUM CARD usado R$2.801,78, mínimo R$183,08 (venc. 2026-07-07).
  Total de dívida em cartão ~R$43.796 + conta corrente negativa
  R$6.423,95 = posição negativa total ~R$50.220. **A API não retorna taxa
  de juros do rotativo** -- se for relevante pro plano de quitação, essa
  informação só existe na fatura/app do banco, não no Pluggy.

## Status atual (atualizado 2026-07-22, parte 2) — PLANEJAMENTO E PROJEÇÃO
- Dashboard redesenhado: topo agora mostra Caixa disponível, Fatura de
  cartão do mês atual, Gastos fixos do mês e Sobra estimada
  (caixa − fatura − fixos). Histórico (gráfico mensal, top categorias,
  tabela) foi movido pra um `<details>` recolhido no fim da página.
- `execution/gastos_fixos.py`: lista de gastos fixos mantida manualmente
  pelo usuário (Vivo, Psicóloga, Faculdade, Escolinha, Tim, Financiamento
  do carro, Internet, Condomínio, Luz, Combustível, Mercado, YouTube,
  Spotify, Smiles, PNR, IR). Itens com faixa (luz, combustível, mercado)
  usam o teto da faixa no planejamento (cenário conservador). **Atualizar
  esse arquivo sempre que um valor mudar.**
- `fatura_atual_e_projecao()` em `gerar_dashboard.py`: projeta as parcelas
  de cartão em aberto pros próximos 6 meses, usando `creditCardMetadata`
  (`installmentNumber`/`totalInstallments`/`billForecastDate`) das
  transações de cartão da Pluggy. Só existe pra fonte Pluggy — e-mail/mock
  não têm esse metadado, então a projeção fica zerada nesses casos (não é
  bug, é limitação da fonte).

## Status anterior (bloqueio trial/demo)
- Confirmado por erro real da API: **qualquer aplicação Pluggy em "trial"/demo
  só pode criar items do conector sandbox "Pluggy Bank"**
  (`TRIAL_CLIENT_ITEM_CREATE_NOT_ALLOWED`), mesmo que `GET /connectors` liste
  235 conectores reais (incluindo Bradesco, id 603) — listar não é o mesmo
  que poder conectar. Isso vale tanto para a aplicação "Demo" quanto para a
  aplicação "App Financeiro" (trial de 15 dias) — o usuário já solicitou
  acesso a dados reais pela dashboard da Pluggy, pedido pendente de análise
  (até 1h em horário comercial, ou próximo dia útil).
- Enquanto o acesso real não é aprovado, a via primária de automação passou a
  ser **leitura de notificação push do app Bradesco Cartões** (não SMS — SMS
  mostrou formato irregular demais). MacroDroid encaminha a notificação por
  e-mail para um Gmail, que `email_source.py` lê via IMAP e parseia. **Já
  testado com compra real em 2026-07-22, funcionando ponta a ponta.** Pluggy
  fica como via secundária, a ativar quando aprovado (ou se a pista do
  meu.pluggy.ai confirmar acesso a dados reais sem aprovação).

## Status anterior (contexto)
- Provedor escolhido: **Pluggy** (agregador BR, sandbox gratuito).
- Credenciais: **ainda não existem**. O usuário precisa criar conta em
  https://dashboard.pluggy.ai e gerar `CLIENT_ID`/`CLIENT_SECRET`.
- Enquanto isso, o pipeline roda em **modo mock** (dados sintéticos com o
  mesmo formato da API real) para permitir construir e testar o dashboard hoje.

## Entradas
- `.env` com `PLUGGY_CLIENT_ID`, `PLUGGY_CLIENT_SECRET` e `PLUGGY_ITEM_ID`
  (item conectado via meu.pluggy.ai) — fonte primária hoje.
- `.env` com `EMAIL_IMAP_USER`/`EMAIL_IMAP_APP_PASSWORD` — fonte de fallback
  (notificação de compra do Bradesco Cartões encaminhada por e-mail).
- Sem nenhuma das duas, o script usa `execution/mock_data.py` automaticamente.
- Prioridade em `carregar_transacoes()`: Pluggy → e-mail → mock.

## Ferramentas / Scripts
1. `execution/pluggy_client.py` — cliente da API Pluggy (autenticação via
   `POST /auth`, `GET /accounts?itemId=`, `GET /v2/transactions` com
   paginação por cursor).
2. `execution/email_source.py` — lê notificações de compra do Bradesco
   Cartões encaminhadas por e-mail (MacroDroid) via IMAP, últimos 31 dias.
3. `execution/mock_data.py` — gera transações sintéticas (mesmo schema da
   Pluggy: `id, description, amount, date, balance, category, accountId, type`)
   cobrindo ~6 meses, múltiplas categorias, receitas e despesas.
4. `execution/gerar_dashboard.py` — orquestra: busca transações (Pluggy,
   e-mail ou mock) → normaliza sinal/filtra movimentação interna → agrega
   por mês/categoria → renderiza `dashboard/index.html`.

## Saída
- `dashboard/index.html`: deliverable local, autocontido (sem dependência de
  internet/CDN), com tema claro/escuro automático. Regenerável a qualquer
  momento rodando `python execution/gerar_dashboard.py`.
- Contém: saldo atual, receitas/despesas do período, resultado líquido,
  gráfico de barras mensal (receitas x despesas) e top categorias de despesa.

## Aprendizados (self-anneal)
- A documentação de referência da Pluggy lista o endpoint de connect token como
  `POST /tokens/connect`, mas o endpoint que **realmente funciona** é
  `POST /connect_token` (sem `/tokens`). `/tokens/connect` retorna 403 Forbidden
  mesmo com credenciais válidas. Usar sempre `/connect_token`.
- Mesmo padrão de divergência para contas: a doc lista
  `GET /items/{itemId}/accounts` (retorna 403), o correto é
  `GET /accounts?itemId={itemId}`.
- O dashboard.pluggy.ai (`dashboard.pluggy.ai/applications/{id}/demo`) é a
  aplicação **Demo**, e o widget Pluggy Connect ali só oferece o conector
  sandbox "Pluggy Bank"/"MeuPluggy" com dados 100% fictícios (fluxo
  diferente do meu.pluggy.ai — mesmo nome de conector "MeuPluggy", mas
  origem diferente). **Não dá pra saber se é real ou fake pela aparência
  dos dados** (nome do dono, valores redondos, nomes de cartão) — o único
  jeito confiável é consultar `GET /items/{itemId}` e checar
  `connector.isSandbox` (`false` = real) e `connector.institutionUrl`
  (`https://meu.pluggy.ai/` = veio do Data Passport pessoal real).
- **Contas tipo `CREDIT` (cartão) têm `amount` sempre positivo** — quem
  indica a direção é o campo `type` da transação (`DEBIT` = compra,
  `CREDIT` = pagamento de fatura/estorno). Contas tipo `BANK` já vêm com o
  sinal correto (negativo = saída). Sem normalizar isso, toda compra de
  cartão aparecia como receita gigante no agregado mensal.
- **Categorias que representam movimentação interna, não gasto/renda
  real**: `Investments`, `Fixed income`, `Transfer - Bank Slip`,
  `Transfer - PIX`, `Same person transfer`, `Transfers`, `Transfer - Cash`,
  `Credit card payment` (pagamento da fatura, já contado compra a compra
  no cartão). Excluir essas categorias do agregado de fluxo de caixa —
  lista em `CATEGORIAS_MOVIMENTACAO_INTERNA` no `gerar_dashboard.py`.
- **Bug de paginação corrigido**: `data["next"]` retornado pela API vem
  com o cursor já URL-encoded; extrair a substring com `.split("after=")`
  e passar direto pro `requests` fazia um segundo encoding (`%3D` virava
  `%253D`), quebrando a página seguinte com 400. Corrigido usando
  `urllib.parse.urlparse` + `parse_qs` pra extrair o valor decodificado.
- **Bug de encoding em `email_source.py`**: alguns clientes de e-mail
  (MacroDroid/Gmail) geram charset inválido `unknown-8bit` no header
  `Subject` e no corpo. Python não reconhece esse nome e lança
  `LookupError`. Corrigido com fallback pra `utf-8` quando o charset
  declarado não existe.
- **Performance do IMAP**: `buscar_transacoes()` fazia `SEARCH ALL` e
  percorria a caixa inteira (13k+ e-mails) uma a uma — muito lento. Trocado
  pra `SEARCH SINCE <data>` com janela padrão de 31 dias, suficiente pro
  caso de uso (fluxo de caixa do mês).
- **Projeção de parcelas futuras — cuidado com o histórico duplicando o
  futuro**: a API guarda um "retrato" da parcela em CADA fatura já fechada
  (ex.: uma compra em 10x aparece uma vez por mês, com
  `installmentNumber` incrementando a cada fatura). Projetar a partir de
  TODO o histórico (todo `t` com `creditCardMetadata`) faz cada retrato
  passado gerar sua própria "parcela futura", duplicando e poluindo os
  valores — inclusive jogando valores pra meses que já passaram. A
  projeção correta só usa as transações cujo `billForecastDate == mês
  atual` (a fatura vigente) e projeta o valor da parcela pros meses
  seguintes até a última parcela. Ver `fatura_atual_e_projecao()`.
- **`%b` do `strftime` depende da locale do sistema** e saía em inglês
  (Aug, Sep) mesmo com todo o resto do projeto em português. Trocado por
  mapeamento fixo `MESES_PT` (Jan..Dez) em vez de confiar na locale.

## Edge cases
- **Sem credenciais Pluggy**: cai em modo mock automaticamente (não é erro).
- **Item sem transações no período**: mês aparece com barras zeradas, não quebra o layout.
- **Categoria nula/vazia**: agrupar em "Outros".
- **Múltiplas contas no mesmo item**: somar saldos e transações de todas.
- **Rate limit da API Pluggy**: se acontecer, documentar aqui o limite exato e
  o backoff aplicado (ainda não testado com credenciais reais).
- **Fonte sem `creditCardMetadata`** (e-mail, mock): `fatura_atual_e_projecao()`
  retorna 0.0 e projeção vazia — normal, não é erro.
- **Timeouts/DNS transientes da API Pluggy**: aconteceram algumas vezes
  durante testes (`ReadTimeoutError`, `NameResolutionError`) sem relação com
  o código — só repetir a chamada.

## Próximos passos
1. Rodar `gerar_dashboard.py` periodicamente (ou automatizar via agendador)
   pra manter o `dashboard/index.html` atualizado com dados do mês.
2. Investigar `saldo_atual()` retornando `None` com dados reais da Pluggy —
   as transações da API não trazem `balance` por transação; considerar
   usar o `balance` da própria conta (`GET /accounts`) em vez de derivar
   das transações.
3. Avaliar se a categoria "Automotive" (R$65.000 em ago/2025) é uma compra
   real pontual (carro/consórcio) ou merece tratamento como parcelamento —
   confirmar com o usuário se aparecer de novo em outro mês.
4. Se o usuário conectar outro banco/cartão fora do Bradesco, repetir o
   fluxo do meu.pluggy.ai e adicionar o novo item (múltiplos items por
   `.env` ainda não suportado — hoje só lê `PLUGGY_ITEM_ID` único).
