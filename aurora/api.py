"""API HTTP do assistente. Rotas e formatos seguem o contrato do enunciado.

Subir: uv run --env-file .env uvicorn --factory aurora.api:criar_api --port 8000
"""

import os

from fastapi import FastAPI, HTTPException
from google.adk.agents.run_config import RunConfig, ToolThreadPoolConfig
from google.adk.events import Event
from google.adk.models.base_llm import BaseLlm
from google.adk.runners import Runner
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from google.genai import types
from pydantic import BaseModel, Field

from aurora import db
from aurora.agents import criar_app

USER_ID = "morador"  # ponytail: o morador é identificado pelo apartamento no state; user_id é fixo.
CONFIRMACAO = "adk_request_confirmation"
# Tools síncronas (SQLite) rodam num thread pool, sem bloquear o event loop.
RUN_CONFIG = RunConfig(tool_thread_pool_config=ToolThreadPoolConfig())


class NovaSessao(BaseModel):
    apartamento: str = Field(min_length=1, examples=["101"])


class Mensagem(BaseModel):
    texto: str = Field(min_length=1, examples=["Reserve o salão de festas para 2030-04-20."])


class RespostaConfirmacao(BaseModel):
    id: str = Field(examples=["adk-b10f2f6e-5459-4aec-8377-802c599febdf"])
    confirmado: bool


class SessaoCriada(BaseModel):
    session_id: str = Field(examples=["55fc10b4-70fa-4d9c-bec7-afe6bbd0f382"])


class ConfirmacaoPendente(BaseModel):
    id: str = Field(examples=["adk-b10f2f6e-5459-4aec-8377-802c599febdf"])
    acao: str = Field(description="Tool que roda se aprovada.", examples=["reservar_area"])
    detalhes: dict = Field(
        description="Argumentos da tool.", examples=[{"area": "salao-de-festas", "data": "2030-04-20"}]
    )


class RespostaConversa(BaseModel):
    resposta: str = Field(description="Texto do assistente; vazio quando parou esperando confirmação.")
    confirmacoes_pendentes: list[ConfirmacaoPendente] = Field(description="Todas as pendências da sessão.")


class Reserva(BaseModel):
    codigo: str = Field(examples=["RSV-1377"])
    area: str = Field(examples=["quadra"])
    data: str = Field(examples=["2030-03-09"])


class Visitante(BaseModel):
    nome: str = Field(examples=["Marina Duarte"])
    data: str = Field(examples=["2030-03-16"])


class Erro(BaseModel):
    detail: str


SESSAO_404: dict[int | str, dict] = {404: {"model": Erro, "description": "Sessão não encontrada."}}
CONFIRMACAO_409: dict[int | str, dict] = {
    409: {"model": Erro, "description": "Não existe confirmação pendente com esse id nesta sessão; nada é executado."}
}


def confirmacoes_pendentes(events: list[Event]) -> list[dict]:
    """Garantia 1: pendência = pedido de confirmação do ADK ainda sem FunctionResponse na sessão."""
    pedidos, respondidos = {}, set()
    for ev in events:
        for fc in ev.get_function_calls():
            if fc.name == CONFIRMACAO:
                pedidos[fc.id] = (fc.args or {})["originalFunctionCall"]
        for fr in ev.get_function_responses():
            if fr.name == CONFIRMACAO:
                respondidos.add(fr.id)
    return [
        {"id": cid, "acao": original["name"], "detalhes": original.get("args", {})}
        for cid, original in pedidos.items()
        if cid not in respondidos
    ]


def criar_api(model: str | BaseLlm | None = None, sessoes_db: str | None = None) -> FastAPI:
    sessoes_db = sessoes_db or str(db.SESSOES_DB)
    os.makedirs(os.path.dirname(sessoes_db) or ".", exist_ok=True)
    # Garantia 3: eventos e state das sessões persistidos em SQLite, sobrevivem ao reinício.
    sessoes = SqliteSessionService(db_path=sessoes_db)
    app_adk = criar_app(model)
    runner = Runner(app=app_adk, session_service=sessoes)
    api = FastAPI(
        title="Residencial Aurora",
        description="Assistente dos moradores. Guia com exemplos e fluxo de confirmação: docs/API.md.",
    )

    async def sessao_ou_404(session_id: str):
        session = await sessoes.get_session(app_name=app_adk.name, user_id=USER_ID, session_id=session_id)
        if session is None:
            raise HTTPException(404, "sessão não encontrada")
        return session

    async def executar(session_id: str, content: types.Content) -> dict:
        textos = []
        async for ev in runner.run_async(
            user_id=USER_ID, session_id=session_id, new_message=content, run_config=RUN_CONFIG
        ):
            if ev.author != "user" and ev.is_final_response() and ev.content:
                textos += [p.text for p in ev.content.parts or [] if p.text and not p.thought]
        session = await sessao_ou_404(session_id)
        return {"resposta": "\n".join(textos), "confirmacoes_pendentes": confirmacoes_pendentes(session.events)}

    @api.post(
        "/sessoes",
        status_code=201,
        response_model=SessaoCriada,
        tags=["conversa"],
        summary="Cria uma sessão para um apartamento",
        description="O apartamento fica fixo na sessão e é o único que as tools usam.",
    )
    async def criar_sessao(body: NovaSessao):
        # Garantia 2: o apartamento entra no state uma única vez, aqui; nenhuma rota o altera depois.
        session = await sessoes.create_session(
            app_name=app_adk.name, user_id=USER_ID, state={"apartamento": body.apartamento}
        )
        return {"session_id": session.id}

    @api.post(
        "/sessoes/{session_id}/mensagens",
        response_model=RespostaConversa,
        responses=SESSAO_404,
        tags=["conversa"],
        summary="Envia uma mensagem ao assistente",
        description="Ações com taxa ou que liberam acesso voltam em `confirmacoes_pendentes` sem executar.",
    )
    async def enviar_mensagem(session_id: str, body: Mensagem):
        await sessao_ou_404(session_id)
        return await executar(session_id, types.Content(role="user", parts=[types.Part(text=body.texto)]))

    @api.post(
        "/sessoes/{session_id}/confirmacoes",
        response_model=RespostaConversa,
        responses=SESSAO_404 | CONFIRMACAO_409,
        tags=["conversa"],
        summary="Aprova ou nega uma confirmação pendente",
        description="`true` executa a ação uma vez; `false` não grava nada. Só esta rota aprova.",
    )
    async def responder_confirmacao(session_id: str, body: RespostaConfirmacao):
        session = await sessao_ou_404(session_id)
        if body.id not in {p["id"] for p in confirmacoes_pendentes(session.events)}:
            raise HTTPException(409, "não existe confirmação pendente com esse id nesta sessão")
        resposta = types.FunctionResponse(id=body.id, name=CONFIRMACAO, response={"confirmed": body.confirmado})
        return await executar(session_id, types.Content(role="user", parts=[types.Part(function_response=resposta)]))

    # ponytail: eventos sem modelo próprio; o formato é o Event do ADK (camelCase).
    @api.get(
        "/sessoes/{session_id}/eventos",
        response_model=list[dict],
        responses=SESSAO_404,
        tags=["conversa"],
        summary="Lista todos os eventos gravados na sessão, em ordem",
    )
    async def listar_eventos(session_id: str):
        session = await sessao_ou_404(session_id)
        return [ev.model_dump(mode="json", exclude_none=True, by_alias=True) for ev in session.events]

    @api.get(
        "/apartamentos/{apartamento}/reservas",
        response_model=list[Reserva],
        tags=["verificação"],
        summary="Reservas ativas do apartamento, direto do banco",
    )
    def reservas(apartamento: str):
        return db.listar_reservas(apartamento)

    @api.get(
        "/apartamentos/{apartamento}/visitantes",
        response_model=list[Visitante],
        tags=["verificação"],
        summary="Visitantes autorizados do apartamento, direto do banco",
    )
    def visitantes(apartamento: str):
        return db.listar_visitantes(apartamento)

    return api
