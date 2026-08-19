# Rota de /processos/{id}/gerar-declaracoes: gera o DOCX de declaração
# unificada (Fase 4, Camada 1) a partir do checklist já extraído e da
# empresa selecionada. Sem chamada de IA -- montagem determinística
# (app/geracao/declaracoes.py), mesmo princípio do Passo 8.

import io

from fastapi import APIRouter, Response

from app.db.repositorio import RegistroNaoEncontradoError, obter_empresa, obter_processo
from app.geracao.declaracoes import gerar_declaracoes
from app.pipeline import ProcessoNaoEncontradoError
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
