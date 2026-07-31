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

## Auditoria de fatura em PDF (2026-07-26)

Mandar o PDF da fatura no Telegram dispara uma auditoria contra o que já
está sincronizado: `agente_llm.processar_atualizacao` detecta
`message.document`, baixa o arquivo (`getFile` + `api.telegram.org/file/`),
roda `agente_ferramentas.auditar_fatura_pdf` e injeta o RESULTADO no
prompt como contexto — o LLM só explica e propõe, nunca faz a extração.

**Por que o parser é determinístico** (`fatura_parser.py`, pdfplumber, sem
LLM): conferir fatura é comparação de números, não interpretação. Pedir
pro modelo ler o PDF custa tokens a cada envio e erra silenciosamente em
valor/data — exatamente o que a auditoria existe pra pegar.
`auditar_fatura_pdf` **não** está em `FERRAMENTAS`/`EXECUTORES` de
propósito: roda antes do modelo, não por escolha dele.

Formato do PDF (aprendido testando faturas reais):
- `extract_text()` mistura a coluna de lançamentos com a barra lateral de
  limites/taxas na ordem visual — inútil pros itens. `extract_tables()`
  isola a tabela de Lançamentos numa célula só, uma linha por lançamento.
- Linha = `DD/MM DESCRIÇÃO [CIDADE] VALOR[-]`; o `-` final marca
  CRÉDITO/estorno. Linhas de troca de portador e subtotal não começam com
  `DD/MM` e caem fora do regex sozinhas.
- Lançamento não traz o ano: assume o do vencimento, menos quando o mês do
  lançamento é maior que o do vencimento (fatura de janeiro com compra de
  dezembro).
- O nome do cartão não tem posição fixa. Em vez de extrair, procura qual
  `contas.account_name` já cadastrado aparece no texto da página 1 —
  `NOMES_CARTOES_CONHECIDOS` em `agente_ferramentas.py` precisa bater
  exatamente com o nome no banco (hoje: VISA INFINITE PRIME, THE PLATINUM
  CARD, ELO NANQUIM PRIME). Cartão novo = acrescentar ali.

**Cuidado com `sobrando_no_banco`**: a comparação usa uma janela de datas
(min/max dos itens da fatura), não o ciclo real de fechamento — então o
banco pode ter lançamento de OUTRO ciclo dentro da janela. O próprio
retorno carrega esse aviso pro modelo, e só um estorno/duplicata óbvio
deve ser tratado como divergência de verdade. `faltando_no_banco` é o
lado confiável.

Dependência nova: `pdfplumber` no `requirements.txt`. Só
`agente_llm`/`agente_ferramentas` importam — o app web não precisa dela
pra subir.

## Compras "pendentes" via e-mail em quase tempo real (2026-07-29)

**Problema encontrado**: o resumo diário (`telegram_diario.py`, 20h) dizia
"nenhum gasto registrado hoje" em dias que TIVERAM gasto real (ex.: 26/07,
compras de R$ 177,23 e R$ 1.974,19 que só apareceram no banco dias
depois). Causa: o emissor do cartão leva de 1 a 3 dias pra liquidar uma
compra e só aí a Pluggy expõe a transação — já com a data retroativa da
compra original. Um único sync/dia às 20h nunca vê o que ainda não
liquidou, então o "gasto de hoje" estava sistematicamente subestimado (não
é bug de query, é atraso estrutural da fonte de dados).

**Ideia do usuário (proposta e aprovada em 2026-07-29)**: reaproveitar o
canal de e-mail que já existia (`email_source.py`, notificações do app do
Bradesco encaminhadas por MacroDroid) como fonte "quente" em PARALELO com
a Pluggy, não só como fallback de quando ela falta:

```
MacroDroid → e-mail (Bradesco) ──► email_pendente.py (a cada 15min)
                                         │ grava status='pendente'
                                         ▼
                                    transacoes (SQLite)
                                         │ notifica Telegram
                                         │ usuário pode corrigir nome/categoria
                                         │ na hora (agente_llm já sabe editar
                                         │ qualquer id, pendente ou não)
                                         ▼
sync.py (Pluggy, 1x/dia 20h) ──► reconciliar_pendentes_email()
   grava a transação OFICIAL          casa por valor + data (±4 dias),
                                       herda descrição/categoria que o
                                       usuário já tiver corrigido, apaga
                                       a linha pendente
```

**Schema**: `transacoes` ganhou `status` (`'confirmada'` default /
`'pendente'`) e `origem` (`'pluggy'` default / `'email'`) — migração em
`db._migrar` (`ALTER TABLE ... DEFAULT`, cobre linhas antigas sem UPDATE
manual).

- **`execution/email_pendente.py`**: `checar_email_pendente()` busca
  e-mails dos últimos 2 dias (`email_source.buscar_transacoes`), insere
  `INSERT OR IGNORE` (dedup pelo `id`) com `status='pendente'`,
  `account_type='CREDIT'`, `account_id='cartao-final-XXXX'` (conta
  "virtual" — sem linha correspondente em `contas`, mas nada no dashboard
  faz JOIN por `account_id`, só `auditar_fatura_pdf` faz e usa contas
  reais da Pluggy, não afeta). Manda um Telegram por compra nova.
- **UID do IMAP, não número de sequência**: `email_source.buscar_transacoes`
  foi corrigido pra usar `conexao.uid("search"/"fetch", ...)` em vez de
  `conexao.search`/`conexao.fetch`. Número de sequência reindexa toda vez
  que uma mensagem é apagada/movida — com a checagem rodando a cada 15min
  e deduplicando pelo `id` no banco, um id instável faria a MESMA compra
  ser tratada como nova e notificada de novo indefinidamente. UID é
  estável (RFC 3501) enquanto o UIDVALIDITY da caixa não mudar.
- **`sync.reconciliar_pendentes_email(conexao)`**: roda no fim de
  `sync.main()` (e de `agente_ferramentas.sincronizar_agora`) — pra cada
  pendente, procura uma confirmada com valor exatamente igual (tolerância
  R$0,01) e data a até `JANELA_RECONCILIACAO_DIAS=4` dias de distância
  (a Pluggy pode postar com a data real da compra, diferente do dia em
  que o e-mail chegou). Ao casar: copia `description_custom`/
  `categoria_grande_custom` da pendente pra confirmada (só se a pendente
  tiver algo customizado — `COALESCE` preserva o que já existia na
  confirmada senão) e DELETA a pendente. Isso evita dupla contagem (dois
  `id`s diferentes pra mesma compra) e preserva correção feita via
  Telegram enquanto a compra ainda estava pendente.
- **Correção de categoria/nome**: não precisou de ferramenta nova —
  `agente_ferramentas.editar_transacao` já edita qualquer `id` da tabela
  `transacoes`, pendente ou confirmada, então "essa compra aí foi
  categoria X" funciona igual, minutos depois da notificação chegar.
- **`telegram_diario.py`**: o "gasto de hoje" agora soma confirmadas +
  pendentes do dia (é o gasto real, não uma estimativa) e sinaliza com
  "⚠️ inclui compra(s) só detectada(s) por e-mail" quando tem pendente
  no meio; um rodapé mostra quantas pendentes seguem em aberto no total
  (pode incluir dias anteriores que ainda não reconciliaram).
- **`execution/telegram_semanal.py`** (novo): fechamento de domingo, só
  `status='confirmada'` — é o número "de verdade" pra conferir contra
  fatura/extrato (pedido do usuário: "no final da semana... fazer o
  ratchet"). Roda dentro do `scheduler.rodar_ciclo_diario()` quando
  `datetime.now().weekday() == 6`.
- **`scheduler.py`**: reescrito de "dorme até o próximo 20h" pra um loop
  de tick de 30s que checa dois relógios independentes — e-mail a cada
  `INTERVALO_EMAIL_MINUTOS=15` e o ciclo diário (sync+resumo, +semanal aos
  domingos) 1x/dia às `HORARIO_DIARIO`. Não precisou de container/serviço
  novo no `docker-compose.yml`/`deploy/app-financeiro-stack.yml` — o
  serviço `scheduler` que já existia agora faz as duas coisas.

### Edge cases
- **Pendente nunca reconcilia** (ex.: compra cancelada, estorno, ou não
  bateu por descasamento de valor): fica pendente pra sempre e continua
  aparecendo no rodapé do resumo diário — nenhuma limpeza automática
  existe ainda. Se isso virar ruído no dia a dia, considerar expirar
  pendente com mais de N dias (perguntar ao usuário antes de apagar
  qualquer coisa).
- **Duas compras iguais no mesmo dia** (mesmo valor, mesmo estabelecimento
  — ex.: dois cafés de R$ 10 na padaria): a reconciliação casa a PRIMEIRA
  confirmada disponível dentro da janela, não necessariamente a mesma
  fisicamente — sem impacto no total (as duas continuam contando), só
  risco cosmético de uma correção de nome/categoria feita numa pendente
  "vazar" pra outra ocorrência do mesmo valor.
- **IMAP indisponível/timeout**: `email_pendente.checar_email_pendente()`
  deixa a exceção subir pra `scheduler.rodar_ciclo_email()`, que só loga e
  segue — não derruba o processo nem atrasa o ciclo diário.

### Pendente
- Rodar de verdade em produção por alguns dias e conferir se a janela de
  4 dias da reconciliação é suficiente (ou generosa demais) pro atraso
  real do Bradesco.
- Nenhum teste automatizado ainda — validado só por leitura de código
  nesta sessão (sem credenciais de e-mail/Pluggy disponíveis pra rodar
  fim-a-fim).

## Sugestão automática na notificação de pendente (2026-07-29)

**Feedback do usuário** depois da 1ª notificação real: a mensagem original
("Ainda não confirmada pelo banco... responda aqui normalmente.") tinha
texto redundante (já dava pra saber pelo "🔔 Compra pendente") e pedia pro
usuário classificar do zero uma compra que ÀS VEZES já é conhecida (ex.:
TIM — mensalidade de celular já classificada como fixa em meses
anteriores). Pedido: casar com o histórico primeiro, sugerir tipo de
gasto/categoria/descrição, e só pedir CONFIRMAÇÃO — sugestão genérica só
quando não achar nada parecido.

- **`email_pendente.sugerir_classificacao(conexao, descricao)`**: busca
  por palavras-chave extraídas da descrição (`_palavras_chave` — só
  alfabéticas, ≥3 letras, das mais longas pras mais curtas; descarta
  número de telefone/terminal e naturalmente prioriza o nome do
  estabelecimento sobre o código genérico da processadora, sem precisar
  de lista de prefixos conhecidos tipo "IFD*"/"MP*"). Duas fontes, nessa
  ordem:
  1. `gastos_fixos.nome LIKE` — se já foi marcada fixa antes (usuário já
     usou "→ fixo" nela), a resposta é direta: `tipo_gasto='fixo'`,
     categoria e nome vêm de lá.
  2. `transacoes` confirmadas com `description`/`description_custom
     LIKE` — se já apareceu antes mas nunca virou fixo, sugere
     `variável` com a categoria/descrição da ocorrência mais recente; se
     apareceu em ≥2 meses distintos, acrescenta nota sugerindo marcar
     como fixo (recorrência não capturada ainda).
  Sem achado nenhum: sugestão genérica (`variável`/`Outros`/descrição
  limpa com `.title()`).
- **Duas mensagens por notificação** (`_montar_mensagens`): a que vai pro
  Telegram é só cabeçalho + sugestão + "confirma?" (limpa, sem o textão
  de antes). Uma segunda versão, com um bloco `[contexto interno pro
  agente...]` grudado (id da transação, tipo/categoria/descrição
  sugeridos, instrução de qual ferramenta chamar), é gravada direto em
  `agente_mensagens` (`db.gravar_mensagem_agente`, role `assistant`,
  mesmo `chat_id` do `.env`) — é isso que faz o agente conversacional
  (`agente_llm.py`) já ter contexto completo quando o usuário responde só
  "confirma"/"sim", sem precisar repetir nada. O usuário nunca vê esse
  bloco (só existe no histórico que alimenta o LLM).
- **`agente_ferramentas.marcar_como_fixo(transacao_id, nome?,
  categoria_grande?)`** (ferramenta nova): mesma lógica de
  `/transacao/<id>/tornar-fixo` do `app.py` (cria linhas em
  `gastos_fixos` pros próximos `MESES_SEED_FIXOS` meses, ligadas via
  `transacao_id_origem`), exposta ao LLM — permite corrigir nome/
  categoria E marcar fixo numa chamada só, em vez de duas.
- **System prompt** (`agente_llm._system_prompt`): instruído a nunca ler
  o bloco de contexto interno em voz alta, aplicar `editar_transacao` +
  (se `tipo_gasto_sugerido == 'fixo'`) `marcar_como_fixo` quando o
  usuário confirmar, e usar o que a pessoa disser em vez da sugestão se
  ela corrigir.

### Edge case novo
- **Falso-positivo de palavra-chave curta** (ex.: um merchant novo cujo
  nome de 3 letras colide por acaso com outro estabelecimento não
  relacionado no histórico): a sugestão pode vir errada. Não é
  destrutivo — é só uma sugestão que o usuário confirma ou corrige na
  resposta; nenhuma ferramenta de escrita roda sem o "sim" do usuário.

## Resumo diário removido, só fica o semanal (2026-07-31)

**Pedido do usuário**: tirar o resumo diário (`telegram_diario.py`, 20h) do
Telegram — o fechamento semanal (`telegram_semanal.py`, domingo) já é o
número "de verdade" (só confirmadas), e a notificação de compra pendente
(quase em tempo real, ver seção 2026-07-29 acima) já cobre o "o que tá
acontecendo agora".

- **`scheduler.rodar_ciclo_diario()`**: não chama mais `telegram_diario.main()`
  depois do `sync.main()` — só sincroniza e, aos domingos, dispara
  `telegram_semanal.main()`.
- **`execution/telegram_diario.py`**: virou só um módulo de utilidades
  (`fmt_brl`, `enviar_telegram`) reaproveitado por `telegram_semanal.py` e
  `email_pendente.py` — a função `montar_resumo_diario()`/`main()` foi
  removida (não tinha mais chamador depois do corte no scheduler). Nome do
  arquivo mantido de propósito (evitar mexer nos imports de dois outros
  módulos + nas referências históricas nesta diretiva por uma troca de nome
  só cosmética).

## Fechamento semanal também sinaliza compra não supervisionada (2026-07-31)

**Pergunta do usuário**: o e-mail (`email_pendente.py`) só cobre o cartão
Bradesco (MacroDroid encaminha só as notificações do app dele) — compras de
outras contas/cartões (Nubank, Mercado Pago, os cartões auditados por PDF...)
nunca passam pela notificação de pendente, então nunca tiveram uma chance de
revisão humana antes de virar estatística no painel. O fechamento semanal
precisava discriminar essas.

**Por que não bastou checar `description_custom`/`categoria_grande_custom`
`IS NULL`**: medido contra o banco real, só 19 das 2967 transações
confirmadas da história inteira (0,6%) têm algum campo customizado — ou
seja, quase nada é editado manualmente mesmo quando a categoria automática
está certa. Usar isso como proxy de "não supervisionada" marcaria ~92% de
qualquer semana, virando ruído (mensagem gigante, sem sinal).

**Solução**: `transacoes.origem` já existia (`'pluggy'` default / `'email'`
pra linha pendente) mas nunca era propagado pra transação confirmada depois
da reconciliação. `sync.reconciliar_pendentes_email` (linha ~118-127) agora
marca `origem = 'email'` na transação confirmada sempre que ela casa com uma
pendente vinda do e-mail — é um fato permanente e barato ("essa compra teve
uma notificação/oportunidade de revisão"), diferente de customização (que é
opcional e rara mesmo quando a revisão aconteceu).

- **`telegram_semanal.montar_resumo_semanal()`**: "supervisionada" agora é
  `origem == 'email'` OU tem `description_custom`/`categoria_grande_custom`
  preenchido (edição manual conta como revisão, mesmo sem ter vindo pelo
  e-mail). O resto lista `data`, `descrição`, `categoria automática (crua da
  Pluggy)` e `valor`, com um cabeçalho `👀 N compra(s) ainda não
  supervisionada(s)`. Capado em 20 linhas (`+ e mais N.` senão) pra não
  estourar o limite de 4096 caracteres do Telegram numa semana ruidosa.
- **Limitação conhecida**: o marcador só existe daqui pra frente — histórico
  reconciliado ANTES dessa mudança tem `origem = 'pluggy'` mesmo quando veio
  do Bradesco, então vai aparecer como "não supervisionada" indevidamente
  até a janela de dados girar (sem backfill, não vale reescrever histórico
  por uma métrica de exibição).
- **Fechamento semanal também passou a agrupar pela GRANDE categoria** (com
  orçamento mensal ao lado de cada uma) -- ver seção logo abaixo.

**Gasto no mês x orçamento na notificação de compra pendente**: pedido
relacionado do usuário, já resolvido pelo `email_pendente.obter_resumo_categoria()`
(seção acima) — toda notificação de compra pendente já mostra `📊 {categoria}
no mês: {gasto} / {limite} (sobra {valor})`, ou "sem teto definido" quando a
grande categoria não tem `orcamento_grande.limite_mensal` configurado.

**Fechamento semanal também ganhou o orçamento por categoria** (mesmo pedido,
estendido ao `telegram_semanal.py`): a lista de categorias da semana era
agrupada pela categoria FINA da Pluggy (`traduzir_categoria(category)`), que
não bate com a granularidade em que o orçamento existe (`orcamento_grande`,
por GRANDE categoria — Mercado, Casa, Lazer...). Trocado para agrupar do
mesmo jeito que o painel e a notificação de pendente já fazem
(`categoria_grande_custom` ou `grande_categoria(traduzir_categoria(category))`)
e cada linha agora mostra `{grande}: {total_da_semana} (orçamento mensal:
{limite})`, ou "sem orçamento definido" quando a grande categoria não tem
teto. Reaproveita `dados_db.carregar_orcamento_por_grande()` (já existia,
usado pelo painel) em vez de duplicar a query de limites.
