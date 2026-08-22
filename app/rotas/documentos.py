# Rotas de geração de documento (DOCX) da Fase 4: declaração unificada e
# minuta de proposta (Camada 1, montagem 100% determinística a partir do
# checklist/catálogo já extraídos) e recurso administrativo (Camada 1
# seguinte, 19/08/2026 -- a única que chama IA de verdade pra escrever
# argumentação, não só montar; ver app.pipeline.gerar_recurso_processo).

import io
from typing import Annotated

from fastapi import APIRouter, Form, Response

from app.db.repositorio import (
    RegistroNaoEncontradoError,
    obter_catalogo_itens,
    obter_empresa,
    obter_precos_item,
    obter_processo,
)
from app.geracao.declaracoes import gerar_declaracoes
from app.geracao.minuta import gerar_minuta
from app.geracao.recurso import gerar_recurso
from app.pipeline import ProcessoNaoEncontradoError, gerar_recurso_processo
from app.rotas.nomes_arquivo import cabecalho_content_disposition, nome_arquivo_seguro

router = APIRouter()

_TIPO_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@router.post("/processos/{id}/gerar-declaracoes")
def gerar_declaracoes_rota(id: int, empresa_id: int) -> Response:
    processo = obter_processo(id)
    if processo is None:
        raise ProcessoNaoEncontradoError(f"processo {id} não existe")

    empresa = obter_empresa(empresa_id)
    if empresa is None:
        raise RegistroNaoEncontradoError(f"empresa {empresa_id} não existe")

    documento = gerar_declaracoes(processo, empresa)

    buffer = io.BytesIO()
    documento.save(buffer)

    nome_arquivo = nome_arquivo_seguro(f"Declaracao_{processo['nome']}_{empresa['razao_social']}.docx")

    return Response(
        content=buffer.getvalue(),
        media_type=_TIPO_DOCX,
        headers={"Content-Disposition": cabecalho_content_disposition(nome_arquivo)},
    )


@router.post("/processos/{id}/gerar-recurso")
def gerar_recurso_rota(
    id: int,
    exigencia_id: Annotated[int, Form()],
    narrativa: Annotated[str, Form()],
    empresa_id: Annotated[int, Form()],
) -> Response:
    """Formulário de verdade (não JSON) porque a tela de origem
    (GET /processos/{id}/recurso) é um <form> HTML de verdade — mesmo
    padrão do botão "Baixar planilha" da planilha de preço. Todas as
    validações de negócio (processo existe, exigência pertence a este
    processo, exigência é de categoria de habilitação, narrativa tem
    detalhe suficiente) já acontecem dentro de gerar_recurso_processo —
    esta rota só resolve HTTP <-> chamada de função e busca a empresa."""
    resultado = gerar_recurso_processo(id, exigencia_id, narrativa)

    empresa = obter_empresa(empresa_id)
    if empresa is None:
        raise RegistroNaoEncontradoError(f"empresa {empresa_id} não existe")

    documento = gerar_recurso(
        resultado["processo"], empresa, resultado["exigencia"], narrativa,
        resultado["fundamentacao"], resultado["pedido"],
    )

    buffer = io.BytesIO()
    documento.save(buffer)

    nome_arquivo = nome_arquivo_seguro(
        f"Recurso_Administrativo_{resultado['processo']['nome']}_{empresa['razao_social']}.docx"
    )

    return Response(
        content=buffer.getvalue(),
        media_type=_TIPO_DOCX,
        headers={"Content-Disposition": cabecalho_content_disposition(nome_arquivo)},
    )


@router.post("/processos/{id}/gerar-minuta")
def gerar_minuta_rota(id: int, empresa_id: int) -> Response:
    processo = obter_processo(id)
    if processo is None:
        raise ProcessoNaoEncontradoError(f"processo {id} não existe")

    empresa = obter_empresa(empresa_id)
    if empresa is None:
        raise RegistroNaoEncontradoError(f"empresa {empresa_id} não existe")

    catalogo = obter_catalogo_itens(id)
    precos = obter_precos_item(id)

    documento = gerar_minuta(processo, empresa, catalogo, precos)

    buffer = io.BytesIO()
    documento.save(buffer)

    nome_arquivo = nome_arquivo_seguro(f"Minuta_de_Proposta_{processo['nome']}_{empresa['razao_social']}.docx")

    return Response(
        content=buffer.getvalue(),
        media_type=_TIPO_DOCX,
        headers={"Content-Disposition": cabecalho_content_disposition(nome_arquivo)},
    )
