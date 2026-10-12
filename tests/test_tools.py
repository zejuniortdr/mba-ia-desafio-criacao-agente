from types import SimpleNamespace

from aurora import db, tools


def ctx(apartamento="101", confirmado=None):
    # ponytail: as tools só usam state e tool_confirmation; o fluxo real com ToolContext está em test_api.py.
    confirmacao = None if confirmado is None else SimpleNamespace(confirmed=confirmado)
    return SimpleNamespace(state={"apartamento": apartamento}, tool_confirmation=confirmacao)


def test_listar_minhas_reservas_usa_apartamento_da_sessao():
    assert tools.listar_minhas_reservas(ctx("101")) == {
        "reservas": [{"codigo": "RSV-1377", "area": "quadra", "data": "2030-03-09"}]
    }


def test_listar_meus_visitantes_usa_apartamento_da_sessao():
    assert tools.listar_meus_visitantes(ctx("101")) == {"visitantes": []}


def test_disponibilidade_nao_revela_dono():
    r = tools.verificar_disponibilidade("salao-de-festas", "2030-03-16", ctx())
    assert r == {"area": "salao-de-festas", "data": "2030-03-16", "disponivel": False}


def test_area_invalida_e_data_invalida_viram_erro():
    assert "erro" in tools.verificar_disponibilidade("piscina", "2030-03-16", ctx())
    assert "erro" in tools.reservar_area("quadra", "16/03/2030", ctx())
    assert "erro" in tools.autorizar_visitante("Joana", "amanhã", ctx())
    assert "erro" in tools.autorizar_visitante("  ", "2030-04-21", ctx())


def test_reservar_area_grava_para_o_apartamento_da_sessao():
    r = tools.reservar_area("quadra", "2030-04-06", ctx("101"))
    assert r["status"] == "reservada"
    assert {"codigo": r["codigo"], "area": "quadra", "data": "2030-04-06"} in db.listar_reservas("101")


def test_reservar_data_ocupada_responde_sem_codigo_nem_apartamento_do_dono():
    r = tools.reservar_area("salao-de-festas", "2030-03-16", ctx("101"))
    assert r["status"] == "ocupada"
    assert "RSV-4821" not in str(r) and "302" not in str(r)


def test_cancelar_reserva_de_outro_apartamento_nao_altera_nem_vaza():
    r = tools.cancelar_reserva("salao-de-festas", "2030-03-16", ctx("101"))
    assert r["status"] == "nao_encontrada"
    assert "RSV-4821" not in str(r) and "302" not in str(r)
    assert [x["codigo"] for x in db.listar_reservas("302")] == ["RSV-4821"]


def test_cancelar_propria_reserva():
    assert tools.cancelar_reserva("quadra", "2030-03-09", ctx("101"))["status"] == "cancelada"
    assert db.listar_reservas("101") == []


def test_reserva_so_pede_confirmacao_com_taxa_e_data_livre():
    assert tools.precisa_confirmar_reserva("salao-de-festas", "2030-04-20", ctx()) is True
    assert tools.precisa_confirmar_reserva("quadra", "2030-04-06", ctx()) is False
    assert tools.precisa_confirmar_reserva("salao-de-festas", "2030-03-16", ctx()) is False
    assert tools.precisa_confirmar_reserva("piscina", "2030-04-20", ctx()) is False


def test_autorizar_visitante_grava_para_o_apartamento_da_sessao():
    assert tools.autorizar_visitante("Joana Ribeiro", "2030-04-21", ctx("101"))["status"] == "autorizado"
    assert db.listar_visitantes("101") == [{"nome": "Joana Ribeiro", "data": "2030-04-21"}]


def test_consultar_regulamento_devolve_so_o_capitulo_pedido():
    texto = tools.consultar_regulamento("IV")
    assert "Capítulo IV: Piscina" in texto
    assert "das 9h às 20h" in texto
    assert "Capítulo III" not in texto and "Capítulo V" not in texto
    assert "erro" in tools.consultar_regulamento("XCIX")


def test_indice_do_regulamento_tem_so_titulos():
    indice = tools.indice_regulamento()
    assert "IV: Piscina" in indice
    assert "9h" not in indice


def test_data_fora_do_formato_estrito_vira_erro():
    for data in ("20300420", "2030-W16-6", "2030-04-20T10:00"):
        assert "erro" in tools.reservar_area("quadra", data, ctx())
        assert "erro" in tools.autorizar_visitante("Joana", data, ctx())
        assert tools.precisa_confirmar_reserva("salao-de-festas", data, ctx()) is False
    assert db.listar_reservas("101") == [{"codigo": "RSV-1377", "area": "quadra", "data": "2030-03-09"}]


def test_area_com_taxa_nao_grava_sem_confirmacao_aprovada():
    r = tools.reservar_area("salao-de-festas", "2030-04-20", ctx())
    assert r["status"] == "nao_confirmada"
    assert db.listar_reservas("101") == [{"codigo": "RSV-1377", "area": "quadra", "data": "2030-03-09"}]
    assert tools.reservar_area("salao-de-festas", "2030-04-20", ctx(confirmado=True))["status"] == "reservada"
