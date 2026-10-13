import inspect

from google.adk.models import FallbackModel, Gemini
from google.adk.tools import AgentTool, FunctionTool

from aurora import tools
from aurora.agents import criar_app, modelo_padrao
from tests.fake_llm import FakeLlm

APP = criar_app(FakeLlm())
RAIZ = APP.root_agent


def _agentes(agente):
    yield agente
    for sub in agente.sub_agents:
        yield from _agentes(sub)
    for t in agente.tools:
        if isinstance(t, AgentTool):
            yield from _agentes(t.agent)


def _funcoes(agente):
    for t in agente.tools:
        if isinstance(t, FunctionTool):
            yield t.func
        elif callable(t) and not isinstance(t, AgentTool):
            yield t


def test_principal_com_pelo_menos_dois_especialistas():
    assert len(list(_agentes(RAIZ))) - 1 >= 2


def test_nenhuma_tool_aceita_apartamento():
    for agente in _agentes(RAIZ):
        for func in _funcoes(agente):
            assert "apartamento" not in inspect.signature(func).parameters, func.__name__


def test_principal_nao_recebe_regulamento_nas_instrucoes():
    for texto in tools._CAPITULOS.values():
        corpo = texto.splitlines()[2]
        assert corpo[:60] not in RAIZ.instruction
    assert list(_funcoes(RAIZ)) == []


def test_modelo_padrao_tem_retry_e_fallback(monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_MODEL_FALLBACK", raising=False)
    modelo = modelo_padrao()
    assert isinstance(modelo, FallbackModel)
    assert [m.model for m in modelo.models] == ["gemini-3.8-flash", "gemini-3.5-flash"]
    assert all(isinstance(m, Gemini) and m.retry_options.attempts > 1 for m in modelo.models)


def test_modelos_vem_do_ambiente(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "primario")
    monkeypatch.setenv("GEMINI_MODEL_FALLBACK", "reserva")
    assert [m.model for m in modelo_padrao().models] == ["primario", "reserva"]


def test_tools_que_cobram_ou_liberam_acesso_pedem_confirmacao():
    por_nome = {t.name: t for a in _agentes(RAIZ) for t in a.tools if isinstance(t, FunctionTool)}
    assert por_nome["reservar_area"]._require_confirmation is tools.precisa_confirmar_reserva
    assert por_nome["autorizar_visitante"]._require_confirmation is True
