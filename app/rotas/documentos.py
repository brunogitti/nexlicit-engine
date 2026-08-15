# Rota de /processos/{id}/gerar-declaracoes: gera o DOCX de declaração
# unificada (Fase 4, Camada 1) a partir do checklist já extraído e da
# empresa selecionada. Sem chamada de IA -- montagem determinística
# (app/geracao/declaracoes.py), mesmo princípio do Passo 8.

import io
import re
from urllib.parse import quote

from fastapi import APIRouter, Response

from app.db.repositorio import RegistroNaoEncontradoError, obter_empresa, obter_processo
from app.geracao.declaracoes import gerar_declaracoes
from app.pipeline import ProcessoNaoEncontradoError

router = APIRouter()

_TIPO_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _nome_arquivo_seguro(base: str) -> str:
    """Troca por "_" qualquer caractere que o Windows não aceita em nome
    de arquivo (\\ / : * ? " < > |) -- razão social e nome de processo são
    texto livre digitado por gente, sem garantia nenhuma de já vir limpo."""
    limpo = re.sub(r'[\\/:*?"<>|]', "_", base).strip()
    return limpo or "documento"


def _cabecalho_content_disposition(nome_arquivo: str) -> str:
    """Nome de arquivo com acento (razão social quase sempre tem) precisa
    dos dois formatos no cabeçalho: "filename" simples só aceita ASCII de
    verdade pela RFC 6266 -- por isso um fallback sem acento pra navegador
    mais antigo, e "filename*" com percent-encoding UTF-8 (o formato que a
    RFC define pra isso) pro navegador moderno mostrar o nome certo."""
    ascii_fallback = nome_arquivo.encode("ascii", errors="replace").decode("ascii")
    codificado = quote(nome_arquivo)
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{codificado}"


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

    nome_arquivo = _nome_arquivo_seguro(f"Declaracao_{processo['nome']}_{empresa['razao_social']}.docx")

    return Response(
        content=buffer.getvalue(),
        media_type=_TIPO_DOCX,
        headers={"Content-Disposition": _cabecalho_content_disposition(nome_arquivo)},
    )
