"""API HTTP do assistente. Rotas e formatos seguem o contrato do enunciado.

Subir: uv run --env-file .env uvicorn --factory aurora.api:criar_api --port 8000
"""

import os

from fastapi import FastAPI, HTTPException
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


class NovaSessao(BaseModel):
    apartamento: str = Field(min_length=1)


class Mensagem(BaseModel):
    texto: str = Field(min_length=1)


class RespostaConfirmacao(BaseModel):
    id: str
    confirmado: bool


def confirmacoes_pendentes(events: list[Event]) -> list[dict]:
    """Garantia 1: pendência = pedido de confirmação do ADK ainda sem FunctionResponse na sessão."""
    pedidos, respondidos = {}, set()
    for ev in events:
        for fc in ev.get_function_calls():
            if fc.name == CONFIRMACAO:
                pedidos[fc.id] = fc.args["originalFunctionCall"]
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
    api = FastAPI(title="Residencial Aurora")

    async def sessao_ou_404(session_id: str):
        session = await sessoes.get_session(app_name=app_adk.name, user_id=USER_ID, session_id=session_id)
        if session is None:
            raise HTTPException(404, "sessão não encontrada")
        return session

    async def executar(session_id: str, content: types.Content) -> dict:
        textos = []
        async for ev in runner.run_async(user_id=USER_ID, session_id=session_id, new_message=content):
            if ev.author != "user" and ev.is_final_response() and ev.content:
                textos += [p.text for p in ev.content.parts or [] if p.text and not p.thought]
        session = await sessao_ou_404(session_id)
        return {"resposta": "\n".join(textos), "confirmacoes_pendentes": confirmacoes_pendentes(session.events)}

    @api.post("/sessoes", status_code=201)
    async def criar_sessao(body: NovaSessao):
        # Garantia 2: o apartamento entra no state uma única vez, aqui; nenhuma rota o altera depois.
        session = await sessoes.create_session(
            app_name=app_adk.name, user_id=USER_ID, state={"apartamento": body.apartamento}
        )
        return {"session_id": session.id}

    @api.post("/sessoes/{session_id}/mensagens")
    async def enviar_mensagem(session_id: str, body: Mensagem):
        await sessao_ou_404(session_id)
        return await executar(session_id, types.Content(role="user", parts=[types.Part(text=body.texto)]))

    @api.post("/sessoes/{session_id}/confirmacoes")
    async def responder_confirmacao(session_id: str, body: RespostaConfirmacao):
        session = await sessao_ou_404(session_id)
        if body.id not in {p["id"] for p in confirmacoes_pendentes(session.events)}:
            raise HTTPException(409, "não existe confirmação pendente com esse id nesta sessão")
        resposta = types.FunctionResponse(id=body.id, name=CONFIRMACAO, response={"confirmed": body.confirmado})
        return await executar(session_id, types.Content(role="user", parts=[types.Part(function_response=resposta)]))

    @api.get("/sessoes/{session_id}/eventos")
    async def listar_eventos(session_id: str):
        session = await sessao_ou_404(session_id)
        return [ev.model_dump(mode="json", exclude_none=True, by_alias=True) for ev in session.events]

    @api.get("/apartamentos/{apartamento}/reservas")
    def reservas(apartamento: str):
        return db.listar_reservas(apartamento)

    @api.get("/apartamentos/{apartamento}/visitantes")
    def visitantes(apartamento: str):
        return db.listar_visitantes(apartamento)

    return api
