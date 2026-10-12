"""LLM determinístico para testes: interpreta comandos curtos em vez de linguagem natural.

  reservar <area> <data> | cancelar <area> <data> | minhas-reservas
  autorizar <data> <nome...> | regulamento <capitulo>

Usa só as tools que o agente atual tem; sem a tool, transfere para o especialista.
"""

import json
from collections.abc import AsyncGenerator

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

COMANDOS = {
    "reservar": ("reservas", "reservar_area", lambda a: {"area": a[0], "data": a[1]}),
    "cancelar": ("reservas", "cancelar_reserva", lambda a: {"area": a[0], "data": a[1]}),
    "minhas-reservas": ("reservas", "listar_minhas_reservas", lambda a: {}),
    "autorizar": ("visitantes", "autorizar_visitante", lambda a: {"data": a[0], "nome": " ".join(a[1:])}),
    "regulamento": ("aurora", "consultar_regulamento", lambda a: {"capitulo": a[0]}),
}


def _parte(**kw) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(**kw)]))


def _comando(llm_request: LlmRequest) -> list[str]:
    for content in reversed(llm_request.contents):
        for part in content.parts or []:
            if content.role == "user" and part.text and part.text.split()[0] in COMANDOS:
                return part.text.split()
    raise AssertionError("nenhum comando na conversa")


class FakeLlm(BaseLlm):
    model: str = "fake"

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        ultima = llm_request.contents[-1].parts[-1]
        if ultima.function_response and ultima.function_response.name != "transfer_to_agent":
            yield _parte(text="resultado: " + json.dumps(ultima.function_response.response, ensure_ascii=False))
            return
        cmd, *args = _comando(llm_request)
        agente, tool, montar = COMANDOS[cmd]
        tools = llm_request.tools_dict
        if tool in tools:
            yield _parte(function_call=types.FunctionCall(name=tool, args=montar(args)))
        elif cmd == "regulamento" and "regulamento" in tools:
            yield _parte(function_call=types.FunctionCall(name="regulamento", args={"request": " ".join([cmd, *args])}))
        else:
            yield _parte(function_call=types.FunctionCall(name="transfer_to_agent", args={"agent_name": agente}))
