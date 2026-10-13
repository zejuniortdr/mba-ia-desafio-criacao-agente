# Como usar a API

A API recebe e devolve JSON em `http://localhost:8000`. Para subir, restaurar os dados e configurar o `.env`, veja a seção Como rodar do [README](../README.md) ou rode `make help`.

Com a API no ar, o FastAPI também serve a especificação interativa em `http://localhost:8000/docs`.

## Fluxo básico

1. Crie uma sessão para um apartamento.
2. Envie mensagens em linguagem natural.
3. Se a resposta trouxer `confirmacoes_pendentes`, aprove ou negue cada uma pela rota de confirmações.
4. Confira o efeito nas rotas de verificação.

O apartamento é fixado na criação da sessão e vale para todas as mensagens dela. Para agir por outro apartamento, crie outra sessão.

## Rotas

| Método | Caminho | O que faz |
|---|---|---|
| `POST` | `/sessoes` | Cria uma sessão para um apartamento |
| `POST` | `/sessoes/{session_id}/mensagens` | Envia uma mensagem ao assistente |
| `POST` | `/sessoes/{session_id}/confirmacoes` | Aprova ou nega uma confirmação pendente |
| `GET` | `/sessoes/{session_id}/eventos` | Lista todos os eventos gravados na sessão |
| `GET` | `/apartamentos/{apartamento}/reservas` | Reservas ativas do apartamento (lê o banco, sem passar pelo modelo) |
| `GET` | `/apartamentos/{apartamento}/visitantes` | Visitantes autorizados do apartamento (lê o banco, sem passar pelo modelo) |

### Criar sessão

```bash
curl -s -X POST localhost:8000/sessoes \
  -H 'content-type: application/json' \
  -d '{"apartamento": "101"}'
```

`201`:

```json
{"session_id": "55fc10b4-70fa-4d9c-bec7-afe6bbd0f382"}
```

Apartamentos dos dados iniciais: `101`, `102`, `201`, `202`, `301`, `302`.

### Enviar mensagem

```bash
curl -s -X POST localhost:8000/sessoes/$SID/mensagens \
  -H 'content-type: application/json' \
  -d '{"texto": "Reserve o salão de festas para 2030-04-20."}'
```

`200`:

```json
{
  "resposta": "",
  "confirmacoes_pendentes": [
    {
      "id": "adk-b10f2f6e-5459-4aec-8377-802c599febdf",
      "acao": "reservar_area",
      "detalhes": {"area": "salao-de-festas", "data": "2030-04-20"}
    }
  ]
}
```

- `resposta` é o texto do assistente. Vem vazio quando a execução parou esperando confirmação.
- `confirmacoes_pendentes` lista todas as pendências da sessão naquele momento; é `[]` quando não há nenhuma.
- `acao` é o nome da tool que vai rodar e `detalhes` são os argumentos dela.

O que o assistente faz:

| Pedido | Ação | Pede confirmação? |
|---|---|---|
| Ver se uma área está livre numa data | `verificar_disponibilidade` | Não |
| Listar as próprias reservas | `listar_minhas_reservas` | Não |
| Reservar salão de festas (R$ 150) ou churrasqueira (R$ 80) | `reservar_area` | Sim, se a data estiver livre |
| Reservar a quadra (sem taxa) | `reservar_area` | Não |
| Cancelar uma reserva do próprio apartamento | `cancelar_reserva` | Não |
| Listar os próprios visitantes | `listar_meus_visitantes` | Não |
| Autorizar a entrada de um visitante | `autorizar_visitante` | Sempre |
| Dúvida sobre o regulamento | especialista `regulamento` | Não |

Datas vão no formato `AAAA-MM-DD`. Ids das áreas: `salao-de-festas`, `churrasqueira`, `quadra`.

### Responder confirmação

Use o `id` que veio em `confirmacoes_pendentes`.

```bash
curl -s -X POST localhost:8000/sessoes/$SID/confirmacoes \
  -H 'content-type: application/json' \
  -d '{"id": "adk-b10f2f6e-5459-4aec-8377-802c599febdf", "confirmado": true}'
```

`200`, no mesmo formato da rota de mensagens. A `resposta` traz o resultado (por exemplo, o código da reserva criada) e a pendência sai da lista:

```json
{"resposta": "...", "confirmacoes_pendentes": []}
```

- `"confirmado": true` executa a ação uma vez.
- `"confirmado": false` não grava nada.
- Um `id` que não está pendente nesta sessão recebe `409`, inclusive um já respondido. Nada é executado:

```json
{"detail": "não existe confirmação pendente com esse id nesta sessão"}
```

Escrever "já confirmei" na conversa não aprova nada. Só esta rota aprova.

### Ver eventos da sessão

```bash
curl -s localhost:8000/sessoes/$SID/eventos
```

`200` com a lista de eventos do ADK, em ordem, como foram gravados: mensagens do morador, respostas dos agentes, transferências entre agentes, chamadas e respostas de tools e pedidos de confirmação. Os campos usam camelCase. Exemplo de um evento de transferência:

```json
{
  "content": {
    "parts": [{"functionCall": {"id": "adk-066f...", "args": {"agent_name": "reservas"}, "name": "transfer_to_agent"}}],
    "role": "model"
  },
  "invocationId": "e-ffb1c102-...",
  "author": "aurora",
  "actions": {"stateDelta": {}, "artifactDelta": {}, "requestedAuthConfigs": {}, "requestedToolConfirmations": {}},
  "id": "8fec19c9-...",
  "timestamp": 1791851121.0252051
}
```

As sessões ficam em `var/sessoes.db` e sobrevivem ao reinício da API.

### Rotas de verificação

```bash
curl -s localhost:8000/apartamentos/101/reservas
curl -s localhost:8000/apartamentos/302/visitantes
```

`200`:

```json
[{"codigo": "RSV-1377", "area": "quadra", "data": "2030-03-09"}]
```

```json
[{"nome": "Marina Duarte", "data": "2030-03-16"}]
```

As reservas listadas são só as ativas; reservas canceladas não aparecem. Um apartamento sem registros devolve `[]`.

## Códigos de status

| Código | Quando |
|---|---|
| `200` | Mensagem processada, confirmação respondida ou consulta feita |
| `201` | Sessão criada |
| `404` | `session_id` inexistente em qualquer rota `/sessoes/{session_id}/...` |
| `409` | Confirmação com `id` que não está pendente nesta sessão |
| `422` | Corpo inválido, como `apartamento` ou `texto` vazios, ou `confirmado` ausente |

Uma reserva recusada porque a data já está ocupada volta como `200`, com a recusa explicada em `resposta`.

## Exemplo completo

```bash
SID=$(curl -s -X POST localhost:8000/sessoes -H 'content-type: application/json' \
  -d '{"apartamento": "101"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["session_id"])')

curl -s -X POST localhost:8000/sessoes/$SID/mensagens -H 'content-type: application/json' \
  -d '{"texto": "Libera a entrada da Joana Ribeiro no dia 2030-04-21."}' > resposta.json

CID=$(python3 -c 'import json; print(json.load(open("resposta.json"))["confirmacoes_pendentes"][0]["id"])')

curl -s -X POST localhost:8000/sessoes/$SID/confirmacoes -H 'content-type: application/json' \
  -d "{\"id\": \"$CID\", \"confirmado\": true}"

curl -s localhost:8000/apartamentos/101/visitantes
```
