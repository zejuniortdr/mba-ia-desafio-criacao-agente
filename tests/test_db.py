import os
import subprocess
import sys
import threading

from aurora import db


def test_comando_restaurar_recria_dados_e_apaga_sessoes(tmp_path, monkeypatch):
    sessoes = tmp_path / "sessoes.db"
    sessoes.write_text("x")
    env = {**os.environ, "AURORA_DB": str(tmp_path / "a.db"), "AURORA_SESSOES_DB": str(sessoes)}
    subprocess.run([sys.executable, "-m", "aurora.db", "restaurar"], env=env, check=True, capture_output=True)
    assert not sessoes.exists()
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "a.db")
    assert [r["codigo"] for r in db.listar_reservas("302")] == ["RSV-4821"]


def test_restaurar_carrega_dados_iniciais():
    assert db.listar_reservas("101") == [
        {"codigo": "RSV-1377", "area": "quadra", "data": "2030-03-09"}
    ]
    assert db.listar_visitantes("302") == [{"nome": "Marina Duarte", "data": "2030-03-16"}]


def test_restaurar_desfaz_mudancas():
    db.cancelar_reserva("101", "quadra", "2030-03-09")
    db.autorizar_visitante("101", "Joana", "2030-04-21")
    db.restaurar()
    assert [r["codigo"] for r in db.listar_reservas("101")] == ["RSV-1377"]
    assert db.listar_visitantes("101") == []


def test_data_livre_reflete_agenda_de_qualquer_apartamento():
    assert db.data_livre("salao-de-festas", "2030-03-16") is False
    assert db.data_livre("salao-de-festas", "2030-03-17") is True


def test_criar_reserva_gera_codigo_novo():
    codigo = db.criar_reserva("101", "quadra", "2030-04-06")
    assert codigo not in {"RSV-1377", "RSV-4821", "RSV-2950"}
    assert {"codigo": codigo, "area": "quadra", "data": "2030-04-06"} in db.listar_reservas("101")


def test_criar_reserva_em_data_ocupada_retorna_none():
    assert db.criar_reserva("101", "salao-de-festas", "2030-03-16") is None
    assert [r["codigo"] for r in db.listar_reservas("302")] == ["RSV-4821"]


def test_cancelar_so_afeta_o_proprio_apartamento():
    assert db.cancelar_reserva("101", "salao-de-festas", "2030-03-16") is False
    assert [r["codigo"] for r in db.listar_reservas("302")] == ["RSV-4821"]
    assert db.cancelar_reserva("101", "quadra", "2030-03-09") is True
    assert db.listar_reservas("101") == []


def test_data_cancelada_volta_a_ficar_livre_e_codigo_nao_repete():
    db.cancelar_reserva("101", "quadra", "2030-03-09")
    codigo = db.criar_reserva("102", "quadra", "2030-03-09")
    assert codigo is not None and codigo != "RSV-1377"


def test_reservas_simultaneas_so_uma_vence():
    resultados = []
    barreira = threading.Barrier(8)

    def reservar(apto):
        barreira.wait()
        resultados.append(db.criar_reserva(apto, "salao-de-festas", "2030-05-11"))

    threads = [threading.Thread(target=reservar, args=(a,)) for a in ["101", "201"] * 4]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len([r for r in resultados if r]) == 1


def test_autorizar_visitante():
    db.autorizar_visitante("101", "Joana Ribeiro", "2030-04-21")
    assert db.listar_visitantes("101") == [{"nome": "Joana Ribeiro", "data": "2030-04-21"}]


def test_area_por_id():
    assert db.area("salao-de-festas")["taxa"] == 150.0
    assert db.area("inexistente") is None
