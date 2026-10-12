import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from aurora import api, db, tools
from tests.fake_llm import FakeLlm


@pytest.fixture
def sessoes_db(tmp_path):
    return str(tmp_path / "sessoes.db")


@pytest.fixture
def client(sessoes_db):
    return TestClient(api.criar_api(model=FakeLlm(), sessoes_db=sessoes_db))


def sessao(client, apto="101"):
    r = client.post("/sessoes", json={"apartamento": apto})
    assert r.status_code == 201
    return r.json()["session_id"]


def msg(client, sid, texto):
    r = client.post(f"/sessoes/{sid}/mensagens", json={"texto": texto})
    assert r.status_code == 200, r.text
    return r.json()


def confirmar(client, sid, cid, ok=True):
    return client.post(f"/sessoes/{sid}/confirmacoes", json={"id": cid, "confirmado": ok})


def reservas(client, apto, area=None, data=None):
    return [
        r
        for r in client.get(f"/apartamentos/{apto}/reservas").json()
        if (area is None or r["area"] == area) and (data is None or r["data"] == data)
    ]


def eventos(client, sid):
    r = client.get(f"/sessoes/{sid}/eventos")
    assert r.status_code == 200
    return r.json()


def test_tools_rodam_fora_do_event_loop(client, monkeypatch):
    original, loops = db.listar_reservas, []

    def espiao(apto):
        try:
            loops.append(asyncio.get_running_loop())
        except RuntimeError:
            loops.append(None)
        return original(apto)

    monkeypatch.setattr(db, "listar_reservas", espiao)
    msg(client, sessao(client), "minhas-reservas")
    assert loops == [None]


def test_rotas_de_verificacao(client):
    assert client.get("/apartamentos/101/reservas").json() == [
        {"codigo": "RSV-1377", "area": "quadra", "data": "2030-03-09"}
    ]
    assert client.get("/apartamentos/302/visitantes").json() == [{"nome": "Marina Duarte", "data": "2030-03-16"}]


def test_sessao_inexistente_404(client):
    assert client.get("/sessoes/sessao-inexistente/eventos").status_code == 404
    assert client.post("/sessoes/x/mensagens", json={"texto": "oi"}).status_code == 404
    assert confirmar(client, "x", "y").status_code == 404


def test_reserva_sem_taxa_e_cancelamento_nao_pedem_confirmacao(client):
    sid = sessao(client)
    r = msg(client, sid, "reservar quadra 2030-04-06")
    assert r["confirmacoes_pendentes"] == [] and r["resposta"]
    assert len(reservas(client, "101", "quadra", "2030-04-06")) == 1
    assert msg(client, sid, "cancelar quadra 2030-03-09")["confirmacoes_pendentes"] == []
    assert reservas(client, "101", "quadra", "2030-03-09") == []


def test_cancelar_reserva_alheia_nao_altera_nem_vaza(client):
    sid = sessao(client)
    r = msg(client, sid, "cancelar salao-de-festas 2030-03-16")
    assert "RSV-4821" not in json.dumps(r) and "RSV-4821" not in json.dumps(eventos(client, sid))
    assert [x["codigo"] for x in reservas(client, "302")] == ["RSV-4821"]


def test_reserva_com_taxa_negar_aprovar_e_reenviar(client):
    sid = sessao(client)
    r = msg(client, sid, "reservar salao-de-festas 2030-04-20")
    [pend] = r["confirmacoes_pendentes"]
    assert pend["detalhes"] == {"area": "salao-de-festas", "data": "2030-04-20"}
    assert reservas(client, "101", "salao-de-festas", "2030-04-20") == []

    negado = confirmar(client, sid, pend["id"], ok=False)
    assert negado.status_code == 200 and negado.json()["confirmacoes_pendentes"] == []
    assert reservas(client, "101", "salao-de-festas", "2030-04-20") == []

    [pend] = msg(client, sid, "reservar salao-de-festas 2030-04-20")["confirmacoes_pendentes"]
    assert confirmar(client, sid, pend["id"]).status_code == 200
    assert len(reservas(client, "101", "salao-de-festas", "2030-04-20")) == 1

    assert confirmar(client, sid, pend["id"]).status_code == 409
    assert confirmar(client, sid, "id-inexistente").status_code == 409
    assert len(reservas(client, "101", "salao-de-festas", "2030-04-20")) == 1


def test_reservar_data_de_outro_apartamento_nao_vaza(client):
    sid = sessao(client)
    r = msg(client, sid, "reservar salao-de-festas 2030-03-16")
    assert r["confirmacoes_pendentes"] == []
    assert reservas(client, "101", "salao-de-festas", "2030-03-16") == []
    assert "RSV-4821" not in json.dumps(r) and "302" not in r["resposta"]
    assert "RSV-4821" not in json.dumps(eventos(client, sid))


def test_visitante_so_com_confirmacao(client):
    sid = sessao(client)
    [pend] = msg(client, sid, "autorizar 2030-04-21 Joana Ribeiro")["confirmacoes_pendentes"]
    assert pend["detalhes"] == {"nome": "Joana Ribeiro", "data": "2030-04-21"}
    assert client.get("/apartamentos/101/visitantes").json() == []
    assert confirmar(client, sid, pend["id"]).status_code == 200
    assert client.get("/apartamentos/101/visitantes").json() == [{"nome": "Joana Ribeiro", "data": "2030-04-21"}]


def test_regulamento_so_traz_o_capitulo_consultado(client):
    sid = sessao(client)
    msg(client, sid, "reservar quadra 2030-04-06")
    r = msg(client, sid, "regulamento IV")
    assert "20h" in r["resposta"]
    dump = json.dumps(eventos(client, sid), ensure_ascii=False)
    assert "reservar_area" in dump
    for cap, texto in tools._CAPITULOS.items():
        if cap != "IV":
            assert texto.splitlines()[0] not in dump
            assert texto.splitlines()[2][:60] not in dump


def test_reinicio_preserva_eventos_e_retoma_confirmacao(sessoes_db):
    antes = TestClient(api.criar_api(model=FakeLlm(), sessoes_db=sessoes_db))
    sid = sessao(antes)
    msg(antes, sid, "reservar quadra 2030-04-06")
    [pend] = msg(antes, sid, "reservar salao-de-festas 2030-04-20")["confirmacoes_pendentes"]
    n = len(eventos(antes, sid))

    depois = TestClient(api.criar_api(model=FakeLlm(), sessoes_db=sessoes_db))
    assert len(eventos(depois, sid)) == n
    assert confirmar(depois, sid, pend["id"]).status_code == 200
    assert len(reservas(depois, "101", "salao-de-festas", "2030-04-20")) == 1
    assert len(eventos(depois, sid)) > n
    assert msg(depois, sid, "minhas-reservas")["confirmacoes_pendentes"] == []


async def test_aprovacoes_simultaneas_so_uma_reserva(sessoes_db):
    app = api.criar_api(model=FakeLlm(), sessoes_db=sessoes_db)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        pendentes = []
        for apto in ("101", "201"):
            sid = (await c.post("/sessoes", json={"apartamento": apto})).json()["session_id"]
            r = await c.post(f"/sessoes/{sid}/mensagens", json={"texto": "reservar salao-de-festas 2030-05-11"})
            [pend] = r.json()["confirmacoes_pendentes"]
            pendentes.append((sid, pend["id"]))
        respostas = await asyncio.gather(
            *[c.post(f"/sessoes/{sid}/confirmacoes", json={"id": cid, "confirmado": True}) for sid, cid in pendentes]
        )
        assert [r.status_code for r in respostas] == [200, 200]
        total = 0
        for apto in ("101", "201"):
            total += len(
                [x for x in (await c.get(f"/apartamentos/{apto}/reservas")).json() if x["data"] == "2030-05-11"]
            )
        assert total == 1
