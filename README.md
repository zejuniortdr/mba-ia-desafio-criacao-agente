# Residencial Aurora: assistente com Google ADK

API em Python que expõe o assistente dos moradores do Residencial Aurora. O morador conversa pelo chat; o modelo decide o caminho, e o código decide o que é permitido. Cada regra crítica do condomínio está em código (schema do banco, assinatura das tools, rota de confirmação), não no prompt.

Stack: Python 3.12+, uv, `google-adk==2.11.0`, FastAPI, SQLite.

## Arquitetura

```
POST /sessoes/{id}/mensagens ──► Runner (App resumable, sessão em SQLite)
                                    │
                                aurora (agente principal, sem tools de dados)
                                ├── reservas     (sub_agent, transferência)
                                ├── visitantes   (sub_agent, transferência)
                                └── regulamento  (AgentTool)
```

| Agente | Responsabilidade | Como é acionado | Por quê |
|---|---|---|---|
| `aurora` | Entende o pedido e encaminha. Não lê nem grava dados. | Raiz do `App` | Concentrar o roteamento num agente sem tools de escrita reduz o que uma mensagem maliciosa consegue disparar a partir dele. |
| `reservas` | Disponibilidade, listagem, criação e cancelamento de reservas | `sub_agent` (transferência via `transfer_to_agent`) | Quem pede a confirmação precisa ser o agente que recebe a resposta. Como sub_agent, ele fica dono da conversa e o Runner devolve a resposta da confirmação a ele. |
| `visitantes` | Listagem e autorização de visitantes | `sub_agent` (transferência) | Mesmo motivo de `reservas`. Ficou separado porque autorizar acesso é outra regra de negócio, com outra tool e outra confirmação. |
| `regulamento` | Responde dúvidas consultando um capítulo do `dados/regulamento.md` | `AgentTool` do agente principal | O `AgentTool` roda o especialista numa sessão em memória própria. A leitura do capítulo fica lá; na sessão do morador entra só a resposta final. |

Decisões que valem para todos:

- Modelo: `FallbackModel` do ADK com `GEMINI_MODEL` (padrão `gemini-3.8-flash`) e `GEMINI_MODEL_FALLBACK` (padrão `gemini-3.5-flash`), cada um com retry e backoff para 429 e 5xx ([aurora/agents.py#L27](aurora/agents.py#L27)). Um pico de demanda no modelo principal não derruba o fluxo.
- O `App` tem `ResumabilityConfig(is_resumable=True)` ([aurora/agents.py#L102](aurora/agents.py#L102)). Com isso o roteador do ADK manda a resposta de uma confirmação ao agente autor do pedido, e não ao último que falou.
- Tools síncronas rodam num thread pool (`RunConfig.tool_thread_pool_config`, [aurora/api.py#L23](aurora/api.py#L23)), então o SQLite não bloqueia o event loop.
- Armazenamento: dois arquivos SQLite em `var/`. `var/aurora.db` guarda reservas e visitantes ([aurora/db.py](aurora/db.py)); `var/sessoes.db` guarda sessões e eventos do ADK (`SqliteSessionService`). Nada de serviço externo.

## Garantias

### 1. Cobrança ou acesso só com confirmação

- [aurora/agents.py#L56](aurora/agents.py#L56): `FunctionTool(reservar_area, require_confirmation=precisa_confirmar_reserva)`.
- [aurora/tools.py#L59](aurora/tools.py#L59): `precisa_confirmar_reserva` devolve `True` só quando a área tem taxa maior que zero e a data está livre. Quadra (taxa 0) não pede confirmação; data ocupada também não, porque não há o que cobrar.
- [aurora/agents.py#L71](aurora/agents.py#L71): `autorizar_visitante` com `require_confirmation=True`, sempre.
- [aurora/api.py#L39](aurora/api.py#L39): `confirmacoes_pendentes` lê os eventos da sessão e lista cada `adk_request_confirmation` que ainda não tem `FunctionResponse`. `detalhes` são os argumentos da chamada original.
- [aurora/api.py#L95](aurora/api.py#L95): a rota de confirmações só aceita um `id` dessa lista; qualquer outro, inclusive um já respondido, recebe `409` antes de chegar ao Runner ([aurora/api.py#L98](aurora/api.py#L98)). Um `id` válido vira um `FunctionResponse` com `{"confirmed": true|false}` enviado ao Runner.

Por que não depende do modelo: a pausa acontece dentro do `FunctionTool` do ADK antes de a função rodar. O modelo pode dizer que o morador já confirmou; a tool continua sem executar até chegar um `FunctionResponse`, e só a rota HTTP cria um. Depois de respondido, o `id` sai da lista de pendentes, então reenviar não executa de novo.

### 2. Cada sessão pertence a um apartamento

- [aurora/api.py#L83-L85](aurora/api.py#L83-L85): o apartamento entra no `state` da sessão uma vez, na criação. Nenhuma rota altera esse valor depois.
- [aurora/tools.py#L21](aurora/tools.py#L21): `_apartamento(tool_context)` lê `tool_context.state["apartamento"]`. Nenhuma tool tem parâmetro `apartamento`; o teste [tests/test_agents.py](tests/test_agents.py) percorre todos os agentes e falha se alguma tiver.
- [aurora/db.py#L114](aurora/db.py#L114): cancelar filtra por `apartamento = ?`. Pedir o cancelamento de uma reserva alheia devolve "Você não tem reserva dessa área nessa data", sem código nem dono.
- `verificar_disponibilidade` e `reservar_area` devolvem só livre/ocupada, nunca de quem é a reserva.

Por que não depende do modelo: o modelo não tem como escolher o apartamento. Ele não aparece em nenhum argumento de tool, e as consultas SQL sempre usam o valor da sessão.

### 3. Nada se perde no reinício

- [aurora/api.py#L59-L60](aurora/api.py#L59-L60): `SqliteSessionService(db_path="var/sessoes.db")` persiste sessões, `state` e eventos.
- [aurora/db.py](aurora/db.py): reservas e visitantes em `var/aurora.db`.
- A pendência de confirmação é derivada dos eventos persistidos, então sobrevive ao reinício e pode ser aprovada depois dele. Isso está coberto em [tests/test_api.py](tests/test_api.py) (`test_reinicio_preserva_eventos_e_retoma_confirmacao`) e foi conferido com o uvicorn real, parando com SIGINT e subindo de novo.

Por que não depende do modelo: o estado mora em arquivo, não em memória do processo nem no histórico que o modelo lembra.

### 4. O regulamento é consultado, não carregado

- [aurora/tools.py#L15](aurora/tools.py#L15): o regulamento é dividido por `## Capítulo` em 14 blocos.
- [aurora/tools.py#L120](aurora/tools.py#L120): `consultar_regulamento(capitulo)` devolve um único capítulo.
- [aurora/tools.py#L115](aurora/tools.py#L115): a instrução do especialista leva só os títulos dos capítulos.
- [aurora/agents.py#L74-L99](aurora/agents.py#L74-L99): o especialista é um `AgentTool`. A chamada da tool e o texto do capítulo ficam na sessão interna dele; a sessão do morador recebe a resposta final.
- O agente principal não tem o regulamento na instrução; [tests/test_agents.py](tests/test_agents.py) confere isso.

Por que não depende do modelo: o modelo pode pedir qualquer capítulo, mas a tool nunca devolve mais de um, e esse texto nem chega à sessão do morador.

### 5. Dois moradores, uma reserva

- [aurora/db.py#L23](aurora/db.py#L23): `CREATE UNIQUE INDEX ux_reserva_ativa ON reservas(area, data) WHERE cancelada = 0`.
- [aurora/db.py#L90-L105](aurora/db.py#L90-L105): `criar_reserva` faz o `INSERT` direto. Se o índice recusar, captura o `IntegrityError` e devolve `None`; a tool responde "A área já está reservada nessa data" e a API responde `200`.
- Cancelar marca `cancelada = 1` em vez de apagar a linha ([aurora/db.py#L114](aurora/db.py#L114)). A data volta a ficar livre e o código antigo continua na tabela, com `PRIMARY KEY`, então nunca é reutilizado. Códigos novos são `RSV-` mais 8 caracteres hexadecimais de `uuid4` ([aurora/db.py#L94](aurora/db.py#L94)).

Por que não depende do modelo: a exclusividade é do SQLite no instante da gravação. A conferência de agenda feita antes (em `precisa_confirmar_reserva`) só evita pedir confirmação inútil; quem garante é o índice. O teste `test_reservas_simultaneas_so_uma_vence` dispara 8 threads ao mesmo tempo e só uma grava.

## Como rodar

Pré-requisitos: Python 3.12 ou superior (desenvolvido em 3.14), [uv](https://docs.astral.sh/uv/) e uma chave do Google AI Studio.

Variáveis do `.env`:

| Variável | Obrigatória | Padrão |
|---|---|---|
| `GOOGLE_API_KEY` | sim | — |
| `GOOGLE_GENAI_USE_VERTEXAI` | sim, `FALSE` | — |
| `GEMINI_MODEL` | não | `gemini-3.8-flash` |
| `GEMINI_MODEL_FALLBACK` | não | `gemini-3.5-flash` |

Instalar e configurar:

```bash
uv sync
cp .env.example .env   # preencha GOOGLE_API_KEY e GOOGLE_GENAI_USE_VERTEXAI=FALSE
```

Restaurar os dados iniciais (recria reservas e visitantes a partir de `dados/` e apaga as sessões):

```bash
uv run python -m aurora.db restaurar
```

Subir a API em `http://localhost:8000`:

```bash
uv run --env-file .env uvicorn --factory aurora.api:criar_api --port 8000
```

Testes:

```bash
uv run pytest                               # suíte sem LLM (modelo falso), não gasta cota
uv run --env-file .env pytest -m e2e -s     # fluxo do avaliador com o Gemini real; mostra chamadas e tokens
```

Os testes `e2e` ficam fora da execução padrão (`addopts = "-m 'not e2e'"` no `pyproject.toml`) e são pulados sem `GOOGLE_API_KEY`. No plano gratuito do Gemini o limite é de 20 requisições por dia por modelo, o que não basta para o fluxo completo.
