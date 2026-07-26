# Diretiva: Agente Conversacional no Telegram

## Objetivo
Um agente LLM (via OpenRouter) que conversa com o usuário pelo mesmo bot do
Telegram que já manda o resumo diário (`telegram_diario.py`), com acesso de
leitura E escrita ao banco (`dados/app_financeiro.db`): responde perguntas
("qual foi meu gasto de ontem?", "agosto vai cobrir?") e executa ações que o
usuário pedir (renomear uma compra, trocar a categoria) -- sem precisar abrir
o dashboard. Pedido do usuário em 2026-07-25: "eu não precisar entrar no
dashboard e não perder o embalo" de registrar/organizar os gastos.

## Decisões de design (perguntadas ao usuário antes de implementar)
- **Modelo**: `deepseek/deepseek-v4-pro` via OpenRouter (escolha do usuário,
  configurável via `OPENROUTER_MODEL` no `.env` sem precisar mexer em código).
- **Confirmação de edição**: aplica direto e avisa o que fez (sem perguntar
  "confirma?" antes) quando encontra exatamente 1 transação compatível com o
  que o usuário descreveu. Se encontrar mais de uma (nome ambíguo), o agente
  deve listar as opções e perguntar qual, em vez de escolher sozinho -- isso
  está só no *system prompt* (não há trava de código pra isso, é
  comportamento do modelo).
- **Deploy**: já preparado pra rodar como serviço novo na VPS (Swarm), mas o
  deploy em si (build+push da imagem, `docker stack deploy`) **não foi
  executado** -- é ação em produção, decidir com o usuário o momento antes de
  rodar (ver `directives` do PerMax sobre deploy, mesmo padrão do `web`/
  `scheduler`).

## Arquitetura
```
Telegram (usuário digita)
   │  getUpdates (long polling, offset salvo em `meta`)
   ▼
execution/agente_llm.py ──► OpenRouter (chat completions + tool calling)
   │                              │
   │                              ▼ (quando o modelo pede uma ferramenta)
   └──────────────────► execution/agente_ferramentas.py ──► SQLite
                              (consultar_gastos, editar_transacao,
                               consultar_painel_mensal)
```

- **`execution/openrouter_client.py`**: cliente HTTP fino (`requests`) pra
  `POST /chat/completions` da OpenRouter -- API compatível com o formato
  OpenAI (`messages`, `tools`, `tool_choice`), sem SDK extra (mesmo padrão de
  `pluggy_client.py`). `from_env()` lê `OPENROUTER_API_KEY`/`OPENROUTER_MODEL`.
- **`execution/agente_ferramentas.py`**: as ferramentas de verdade (funções
  Python determinísticas, sem LLM dentro) que o modelo pode chamar:
  - `consultar_gastos(periodo, data_inicio?, data_fim?, descricao_contem?,
    categoria_grande?, forma?)` -- resolve "hoje"/"ontem"/"esta_semana"/
    "mes_atual"/"mes_passado"/"personalizado" em datas exatas **em Python**
    (o modelo nunca faz aritmética de data sozinho -- mesmo princípio de
    sempre neste projeto: empurrar a complexidade pro determinístico).
    Devolve total, breakdown por categoria e até 30 itens (cada um com
    `id`, necessário pra editar depois).
  - `editar_transacao(transacao_id, nova_descricao?, nova_categoria_grande?)`
    -- mesmos campos que a edição manual do dashboard usa
    (`description_custom`/`categoria_grande_custom`); nunca mexe em
    valor/data. Valida a categoria contra a lista de grandes categorias
    (`categorias_grandes.GRANDES_CATEGORIAS`).
  - `consultar_painel_mensal()` -- reaproveita
    `gerar_dashboard.construir_panorama_mensal()` (mesmo cálculo do
    dashboard, já com o mês reancorado no pagamento da fatura -- ver
    `directives/dashboard_fluxo_caixa.md`, seção 2026-07-25).
- **`execution/agente_llm.py`**: o loop -- long polling no Telegram
  (`getUpdates`), monta `[system_prompt] + histórico + mensagem_nova`,
  chama a OpenRouter, se vier `tool_calls` executa cada uma via
  `agente_ferramentas.EXECUTORES` e manda o resultado de volta pro modelo
  (loop de até 6 idas, trava contra tool-calling infinito), até o modelo
  responder texto final -- manda pro Telegram e persiste tudo.
- **Memória de conversa**: tabela nova `agente_mensagens` (`chat_id`,
  `mensagem` JSON, `criado_em`) -- guarda cada mensagem (user/assistant/tool)
  no formato bruto da API, pra sobreviver a redeploy/restart do container
  sem perder o contexto ("esse gasto aqui" continua resolvendo pro `id` que
  apareceu numa resposta 2 mensagens atrás, mesmo depois de um restart).
  Carrega as últimas 40 mensagens do chat como contexto a cada nova
  pergunta.
- **Offset do Telegram**: salvo na tabela `meta` (`telegram_agente_offset`,
  reaproveitando `db.obter_meta`/`definir_meta` que já existiam) -- não
  reprocessa mensagem antiga depois de um restart.
- **Segurança básica**: só processa mensagem vindo do `TELEGRAM_CHAT_ID` do
  `.env` -- mensagem de qualquer outro chat é ignorada e logada.

## Testado (2026-07-25)
- Cliente OpenRouter: chamada simples confirmou API key válida e o modelo
  `deepseek/deepseek-v4-pro` responde.
- Fluxo completo local (sem Telegram, chamando `agente_llm.responder()`
  direto): "Qual foi meu gasto de ontem?" → tool call correto
  (`consultar_gastos(periodo="ontem")`) → resposta em português.
- "Quanto eu gastei no dia 23/07/2026? me mostra os itens" → listou as 2
  transações reais daquele dia com valores corretos.
- Fluxo completo do pedido do usuário -- consultar, depois referenciar
  "esse gasto" pelo nome e pedir troca de nome + categoria na MESMA
  conversa: funcionou, `editar_transacao` aplicou direto (transação real
  "BRUNO AGUEDA OVELHA" → "Feira do bairro", categoria Mercado → Lazer) e o
  agente confirmou a mudança na resposta. **Revertido manualmente depois do
  teste** (era dado real do usuário, não sobrou alterado).
- "Agosto vai cobrir?" → `consultar_painel_mensal()` trouxe os mesmos
  números do dashboard (caixa R$19.680,08, saldo final R$4.312,35, alerta
  de que setembro já não cobre) -- confirma que a ferramenta reaproveita
  exatamente o cálculo do painel, sem duplicar lógica.
- Registros de teste (chat_id `teste_local`/`teste_local2`) apagados de
  `agente_mensagens` depois -- não é conversa real do usuário.

## Edge cases
- **Telegram só entende texto por enquanto**: mensagem de voz/foto/sticker
  recebe um aviso simples, não tenta processar.
- **Mais de 30 transações num período**: `consultar_gastos` devolve só as
  30 primeiras (mais recentes) + contagem de quantas ficaram de fora
  (`itens_omitidos`) -- evita estourar o contexto do modelo; o usuário pode
  refinar com `descricao_contem`/`categoria_grande`/`forma`.
- **Categoria inválida pedida pelo usuário**: `editar_transacao` recusa e
  devolve a lista de categorias válidas pro modelo tentar de novo ou avisar
  o usuário -- nunca grava uma categoria fora da lista.
- **`transacao_id` que não existe** (o modelo alucinou ou o histórico já
  saiu do contexto): `editar_transacao` devolve erro explícito, o agente
  não finge que funcionou.
- **Erro da API da OpenRouter (rate limit, timeout)**: o loop principal
  (`agente_llm.main()`) captura e manda uma mensagem de erro pro usuário em
  vez de derrubar o processo -- próxima mensagem continua funcionando.

## Pendente
1. **Rodar de verdade no Telegram** -- só testei chamando `responder()`
   direto em Python; falta confirmar o long polling reagindo a mensagem
   real digitada no app do Telegram (rodar `python execution/agente_llm.py`
   localmente e mandar mensagem pro bot).
2. **Deploy na VPS** -- `docker-compose.yml` (local) e
   `deploy/app-financeiro-stack.yml` (real, com segredos) já têm o serviço
   `agente` pronto (`replicas: 1` -- **nunca subir com mais de 1 réplica**,
   senão duas instâncias disputam o mesmo `getUpdates`/offset e respondem
   a mensagem em duplicidade). Falta: build+push da imagem nova pro GHCR e
   `docker stack deploy` -- ação em produção, fazer só quando o usuário
   confirmar o momento.
3. **Rotacionar a `OPENROUTER_API_KEY`** -- o usuário colou a key em texto
   puro na conversa; recomendado gerar uma nova na OpenRouter e atualizar
   `.env` + `deploy/app-financeiro-stack.yml` antes de considerar
   definitivo (a key atual já está funcional e em uso nos dois arquivos).
4. Sem trava de código pra "não editar sozinho quando ambíguo" -- hoje é só
   instrução no system prompt (`agente_llm._system_prompt()`). Se na
   prática o modelo editar a transação errada por ambiguidade, considerar
   mover essa checagem pra `agente_ferramentas.editar_transacao` (ex.: exigir
   um parâmetro de confirmação quando `consultar_gastos` recente listou mais
   de uma opção).
