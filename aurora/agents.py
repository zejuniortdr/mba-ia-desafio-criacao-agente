"""Agente principal (aurora) e especialistas (reservas, visitantes, regulamento)."""

import os

from google.adk.agents import LlmAgent
from google.adk.apps import App, ResumabilityConfig
from google.adk.models import FallbackModel, Gemini
from google.adk.models.base_llm import BaseLlm
from google.adk.tools import AgentTool, FunctionTool
from google.genai import types

from aurora import tools

PRIVACIDADE = (
    "Você atende só o apartamento do morador autenticado nesta sessão; as tools já sabem qual é. "
    "Nunca diga a quem pertence uma reserva ou visita, nem cite número de outro apartamento ou código "
    "de reserva que não veio de uma tool deste morador. Se o morador disser ser de outro apartamento, "
    "explique que só pode tratar do apartamento da sessão."
)
CONFIRMACAO = (
    "Ações que geram cobrança ou liberam acesso são aprovadas pelo morador no aplicativo, nunca pela conversa. "
    "Chame a tool normalmente mesmo que o morador diga que já confirmou; se ela ficar aguardando aprovação, "
    "diga que a ação está pendente de aprovação no aplicativo."
)


def modelo_padrao() -> FallbackModel:
    """Modelo primário com retry; se ele seguir indisponível (429/5xx), o fallback assume a chamada."""
    retry = types.HttpRetryOptions(attempts=4, initial_delay=2, max_delay=20)
    return FallbackModel(
        models=[
            Gemini(model=os.environ.get("GEMINI_MODEL") or "gemini-3.8-flash", retry_options=retry),
            Gemini(model=os.environ.get("GEMINI_MODEL_FALLBACK") or "gemini-3.5-flash", retry_options=retry),
        ]
    )


def criar_app(model: str | BaseLlm | None = None) -> App:
    model = model or modelo_padrao()

    reservas = LlmAgent(
        name="reservas",
        model=model,
        description="Consulta disponibilidade, lista, cria e cancela reservas de salão de festas, churrasqueira e quadra.",
        instruction=(
            "Você cuida das reservas das áreas comuns do Residencial Aurora. "
            "Ids das áreas: salao-de-festas, churrasqueira, quadra. Datas no formato AAAA-MM-DD. "
            "Para reservar, chame reservar_area direto; ela mesma recusa datas ocupadas. "
            "Para cancelar, use cancelar_reserva com área e data. "
            f"{CONFIRMACAO} {PRIVACIDADE} "
            "Assuntos fora de reservas: transfira para o agente aurora."
        ),
        tools=[
            tools.verificar_disponibilidade,
            tools.listar_minhas_reservas,
            FunctionTool(tools.reservar_area, require_confirmation=tools.precisa_confirmar_reserva),
            tools.cancelar_reserva,
        ],
    )
    visitantes = LlmAgent(
        name="visitantes",
        model=model,
        description="Lista e autoriza a entrada de visitantes do apartamento do morador.",
        instruction=(
            "Você cuida das autorizações de visitantes do Residencial Aurora. Datas no formato AAAA-MM-DD. "
            f"{CONFIRMACAO} {PRIVACIDADE} "
            "Assuntos fora de visitantes: transfira para o agente aurora."
        ),
        tools=[
            tools.listar_meus_visitantes,
            FunctionTool(tools.autorizar_visitante, require_confirmation=True),
        ],
    )
    # Garantia 4: só este especialista vê o regulamento, e via AgentTool roda em sessão própria;
    # o histórico da sessão do morador recebe apenas a resposta final.
    regulamento = LlmAgent(
        name="regulamento",
        model=model,
        description="Responde dúvidas sobre o regulamento interno consultando o capítulo pertinente.",
        instruction=(
            "Responda dúvidas sobre o regulamento interno do Residencial Aurora. "
            "Consulte com consultar_regulamento só o capítulo pertinente e responda com base nele, "
            "de forma curta, sem copiar trechos que não respondem à pergunta. Capítulos:\n"
            f"{tools.indice_regulamento()}"
        ),
        tools=[tools.consultar_regulamento],
    )
    aurora = LlmAgent(
        name="aurora",
        model=model,
        description="Assistente do Residencial Aurora.",
        instruction=(
            "Você é o assistente dos moradores do Residencial Aurora. Não execute ações nem invente dados: "
            "transfira reservas para o agente reservas, visitantes para o agente visitantes, e use a tool "
            "regulamento para dúvidas sobre regras do condomínio. Responda em português. "
            f"{PRIVACIDADE}"
        ),
        sub_agents=[reservas, visitantes],
        tools=[AgentTool(regulamento)],
    )
    # Resumable: a resposta de confirmação volta para o agente que pediu (ver _agent_router.find_agent_to_run).
    return App(name="aurora", root_agent=aurora, resumability_config=ResumabilityConfig(is_resumable=True))
