"""Dados do condomínio em SQLite. Toda regra de exclusividade vive no schema."""

import json
import os
import sqlite3
import sys
import uuid
from pathlib import Path

DADOS = Path(__file__).resolve().parent.parent / "dados"
DB_PATH = Path(os.environ.get("AURORA_DB", "var/aurora.db"))

SCHEMA = """
CREATE TABLE reservas (
    codigo TEXT PRIMARY KEY,
    apartamento TEXT NOT NULL,
    area TEXT NOT NULL,
    data TEXT NOT NULL,
    cancelada INTEGER NOT NULL DEFAULT 0
);
-- Garantia 5: no máximo uma reserva ativa por área e data, valendo no INSERT.
CREATE UNIQUE INDEX ux_reserva_ativa ON reservas(area, data) WHERE cancelada = 0;
CREATE TABLE visitantes (
    apartamento TEXT NOT NULL,
    nome TEXT NOT NULL,
    data TEXT NOT NULL
);
"""


def _conectar() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _ler(nome: str) -> list[dict]:
    return json.loads((DADOS / nome).read_text(encoding="utf-8"))


def restaurar() -> None:
    """Volta reservas e visitantes ao estado de dados/*.json."""
    with _conectar() as conn:
        conn.executescript("DROP TABLE IF EXISTS reservas; DROP TABLE IF EXISTS visitantes;" + SCHEMA)
        conn.executemany(
            "INSERT INTO reservas (codigo, apartamento, area, data) VALUES (:codigo, :apartamento, :area, :data)",
            _ler("reservas.json"),
        )
        conn.executemany(
            "INSERT INTO visitantes (apartamento, nome, data) VALUES (:apartamento, :nome, :data)",
            _ler("visitantes.json"),
        )
    conn.close()


def area(area_id: str) -> dict | None:
    return next((a for a in _ler("areas.json") if a["id"] == area_id), None)


def listar_reservas(apartamento: str) -> list[dict]:
    with _conectar() as conn:
        rows = conn.execute(
            "SELECT codigo, area, data FROM reservas WHERE apartamento = ? AND cancelada = 0 ORDER BY data",
            (apartamento,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def listar_visitantes(apartamento: str) -> list[dict]:
    with _conectar() as conn:
        rows = conn.execute(
            "SELECT nome, data FROM visitantes WHERE apartamento = ? ORDER BY data, rowid", (apartamento,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def data_livre(area_id: str, data: str) -> bool:
    with _conectar() as conn:
        row = conn.execute(
            "SELECT 1 FROM reservas WHERE area = ? AND data = ? AND cancelada = 0", (area_id, data)
        ).fetchone()
    conn.close()
    return row is None


def criar_reserva(apartamento: str, area_id: str, data: str) -> str | None:
    """Grava a reserva e devolve o código, ou None se a data já estiver ocupada."""
    # ponytail: 8 hex de uuid4; colisão vira IntegrityError na PK e é tratada como nova tentativa.
    for _ in range(5):
        codigo = f"RSV-{uuid.uuid4().hex[:8].upper()}"
        conn = _conectar()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO reservas (codigo, apartamento, area, data) VALUES (?, ?, ?, ?)",
                    (codigo, apartamento, area_id, data),
                )
            return codigo
        except sqlite3.IntegrityError as e:
            if "reservas.codigo" not in str(e):
                return None  # violou ux_reserva_ativa: data ocupada
        finally:
            conn.close()
    raise RuntimeError("não foi possível gerar código de reserva único")


def cancelar_reserva(apartamento: str, area_id: str, data: str) -> bool:
    with _conectar() as conn:
        cur = conn.execute(
            "UPDATE reservas SET cancelada = 1 WHERE apartamento = ? AND area = ? AND data = ? AND cancelada = 0",
            (apartamento, area_id, data),
        )
    conn.close()
    return cur.rowcount > 0


def autorizar_visitante(apartamento: str, nome: str, data: str) -> None:
    with _conectar() as conn:
        conn.execute("INSERT INTO visitantes (apartamento, nome, data) VALUES (?, ?, ?)", (apartamento, nome, data))
    conn.close()


if __name__ == "__main__" and sys.argv[1:] == ["restaurar"]:
    restaurar()
    print(f"dados restaurados em {DB_PATH}")
