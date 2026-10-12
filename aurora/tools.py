"""Tools dos especialistas.

Garantia 2: nenhuma tool recebe apartamento como argumento. O apartamento vem
sempre de tool_context.state["apartamento"], gravado só na criação da sessão.
"""

import re
from datetime import date

from google.adk.tools import ToolContext

from aurora import db

REGULAMENTO = (db.DADOS / "regulamento.md").read_text(encoding="utf-8")
_CAPITULOS = {
    m.group(1): m.group(0).strip()
    for m in re.finditer(r"^## Capítulo ([IVXLC]+):.*?(?=^## |\Z)", REGULAMENTO, re.MULTILINE | re.DOTALL)
}


def _apartamento(tool_context: ToolContext) -> str:
    return tool_context.state["apartamento"]


def _validar(area: str | None, data: str) -> str | None:
    if area is not None and db.area(area) is None:
        return "Área inválida. Use salao-de-festas, churrasqueira ou quadra."
    # Formato estrito: fromisoformat sozinho aceita "20300420", que não colidiria com "2030-04-20" no índice único.
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", data):
            raise ValueError(data)
        date.fromisoformat(data)
    except ValueError:
        return "Data inválida. Use o formato AAAA-MM-DD."
    return None


def verificar_disponibilidade(area: str, data: str, tool_context: ToolContext) -> dict:
    """Informa se uma área comum está livre numa data (AAAA-MM-DD).

    Args:
        area: id da área: salao-de-festas, churrasqueira ou quadra.
        data: data no formato AAAA-MM-DD.
    """
    if erro := _validar(area, data):
        return {"erro": erro}
    return {"area": area, "data": data, "disponivel": db.data_livre(area, data)}


def listar_minhas_reservas(tool_context: ToolContext) -> dict:
    """Lista as reservas ativas do apartamento do morador."""
    return {"reservas": db.listar_reservas(_apartamento(tool_context))}


def _taxa(area: str) -> float:
    dados = db.area(area)
    if dados is None:
        raise ValueError(f"área inválida: {area}")
    return dados["taxa"]


def precisa_confirmar_reserva(area: str, data: str, tool_context: ToolContext) -> bool:
    """Garantia 1: só reserva com taxa e data livre gera cobrança, logo só ela pede confirmação."""
    if _validar(area, data):
        return False
    return _taxa(area) > 0 and db.data_livre(area, data)


def reservar_area(area: str, data: str, tool_context: ToolContext) -> dict:
    """Reserva uma área comum para o apartamento do morador. Áreas com taxa exigem aprovação do morador.

    Args:
        area: id da área: salao-de-festas, churrasqueira ou quadra.
        data: data no formato AAAA-MM-DD.
    """
    if erro := _validar(area, data):
        return {"erro": erro}
    # Garantia 1: cobrança nunca grava sem aprovação, mesmo se a agenda mudou depois de precisa_confirmar_reserva.
    confirmacao = tool_context.tool_confirmation
    if _taxa(area) > 0 and not (confirmacao and confirmacao.confirmed):
        if not db.data_livre(area, data):
            return {"status": "ocupada", "mensagem": "A área já está reservada nessa data."}
        return {"status": "nao_confirmada", "mensagem": "A agenda mudou. Peça a reserva de novo para aprovar a taxa."}
    codigo = db.criar_reserva(_apartamento(tool_context), area, data)
    if codigo is None:
        return {"status": "ocupada", "mensagem": "A área já está reservada nessa data."}
    return {"status": "reservada", "codigo": codigo, "area": area, "data": data, "taxa": _taxa(area)}


def cancelar_reserva(area: str, data: str, tool_context: ToolContext) -> dict:
    """Cancela uma reserva do próprio apartamento do morador.

    Args:
        area: id da área: salao-de-festas, churrasqueira ou quadra.
        data: data no formato AAAA-MM-DD.
    """
    if erro := _validar(area, data):
        return {"erro": erro}
    if db.cancelar_reserva(_apartamento(tool_context), area, data):
        return {"status": "cancelada", "area": area, "data": data}
    return {"status": "nao_encontrada", "mensagem": "Você não tem reserva dessa área nessa data."}


def listar_meus_visitantes(tool_context: ToolContext) -> dict:
    """Lista as autorizações de visita do apartamento do morador."""
    return {"visitantes": db.listar_visitantes(_apartamento(tool_context))}


def autorizar_visitante(nome: str, data: str, tool_context: ToolContext) -> dict:
    """Autoriza a entrada de um visitante no prédio. Sempre exige aprovação do morador.

    Args:
        nome: nome completo do visitante.
        data: data da visita no formato AAAA-MM-DD.
    """
    if erro := _validar(None, data):
        return {"erro": erro}
    if not nome.strip():
        return {"erro": "Informe o nome do visitante."}
    db.autorizar_visitante(_apartamento(tool_context), nome.strip(), data)
    return {"status": "autorizado", "nome": nome.strip(), "data": data}


def indice_regulamento() -> str:
    """Só os títulos dos capítulos; o texto fica fora das instruções (Garantia 4)."""
    return "\n".join(texto.splitlines()[0].removeprefix("## Capítulo ") for texto in _CAPITULOS.values())


def consultar_regulamento(capitulo: str) -> str:
    """Devolve o texto de um único capítulo do regulamento interno.

    Args:
        capitulo: número romano do capítulo, por exemplo IV.
    """
    return _CAPITULOS.get(capitulo.strip().upper(), f"erro: capítulo inexistente. Capítulos:\n{indice_regulamento()}")
