# Rotas da planilha de preço (Fase 4, Camada 1, decisão B -- 16/08/2026):
# salvar quantidade/preço/marca/fabricante/modelo de um item (chamada
# pelo JS a cada campo preenchido, mesmo padrão de PATCH /exigencias/{id})
# e gerar o XLSX pra download (mesmo padrão de POST
# /processos/{id}/gerar-declaracoes, em app/rotas/documentos.py). A
# validade da proposta (Fase 4, Camada 1 da minuta, 19/08/2026) mora
# nesta tela também -- mesmo lugar que o resto do dado comercial digitado
# por gente, mesmo padrão de salvar ao sair do campo.

import io

from fastapi import APIRouter, Response
from pydantic import BaseModel

from app.db.repositorio import (
    atualizar_validade_proposta,
    obter_catalogo_itens,
    obter_precos_item,
    obter_processo,
    salvar_preco_item,
)
from app.geracao.planilha_preco import gerar_planilha_preco
from app.pipeline import ProcessoNaoEncontradoError
from app.rotas.nomes_arquivo import cabecalho_content_disposition, nome_arquivo_seguro

router = APIRouter()

_TIPO_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class PrecoItemBody(BaseModel):
    quantidade: float | None = None
    preco_unitario: float | None = None
    marca: str | None = None
    fabricante: str | None = None
    modelo: str | None = None


class ValidadePropostaBody(BaseModel):
    validade_proposta: str | None = None


@router.patch("/processos/{id}/itens/{numero}/preco")
def salvar_preco_item_rota(id: int, numero: int, body: PrecoItemBody) -> dict:
    return salvar_preco_item(
        id, numero, body.quantidade, body.preco_unitario, body.marca, body.fabricante, body.modelo
    )


@router.patch("/processos/{id}/validade-proposta")
def atualizar_validade_proposta_rota(id: int, body: ValidadePropostaBody) -> dict:
    atualizar_validade_proposta(id, body.validade_proposta)
    return {"validade_proposta": body.validade_proposta}


@router.post("/processos/{id}/gerar-planilha-preco")
def gerar_planilha_preco_rota(id: int) -> Response:
    processo = obter_processo(id)
    if processo is None:
        raise ProcessoNaoEncontradoError(f"processo {id} não existe")

    catalogo = obter_catalogo_itens(id)
    precos = obter_precos_item(id)

    pasta = gerar_planilha_preco(processo, catalogo, precos)

    buffer = io.BytesIO()
    pasta.save(buffer)

    nome_arquivo = nome_arquivo_seguro(f"Planilha_de_Preco_{processo['nome']}.xlsx")

    return Response(
        content=buffer.getvalue(),
        media_type=_TIPO_XLSX,
        headers={"Content-Disposition": cabecalho_content_disposition(nome_arquivo)},
    )
