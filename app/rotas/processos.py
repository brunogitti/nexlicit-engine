# Rotas de /processos: criar (upload), listar, obter um específico, e
# disparar a análise (pipeline). O SQL mora todo em app/db/repositorio.py —
# esta rota só converte HTTP <-> chamadas de função.
#
# Este módulo é só o contrato JSON do Passo 6, sem ramificação nenhuma para
# HTML — a página de histórico (Passo 7) vive em rota própria, GET / (ver
# app/rotas/paginas.py), não aqui.

import shutil
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, UploadFile

from app.config import UPLOAD_DIR
from app.db.repositorio import criar_processo, listar_processos, obter_processo
from app.pipeline import ProcessoNaoEncontradoError, processar_processo

router = APIRouter()


def criar_processo_e_salvar_arquivos(
    dados: dict[str, Any], arquivos: list[UploadFile]
) -> int:
    """Cria o processo e salva os arquivos enviados em
    UPLOAD_DIR/{processo_id}/. Devolve o id criado.

    Compartilhado entre a rota JSON (POST /processos) e o formulário HTML
    (POST /processos/novo, app/rotas/paginas.py) — as duas fazem exatamente
    a mesma coisa aqui, só o que devolvem depois é diferente.
    """
    processo_id = criar_processo(dados)

    pasta_processo = Path(UPLOAD_DIR) / str(processo_id)
    pasta_processo.mkdir(parents=True, exist_ok=True)

    for arquivo in arquivos:
        # Path(...).name descarta qualquer componente de diretório do nome
        # enviado pelo cliente — evita gravar fora de UPLOAD_DIR por acidente
        # ou má-fé (ex.: nome de arquivo "../../etc/algo").
        nome_seguro = Path(arquivo.filename or "arquivo_sem_nome").name
        destino = pasta_processo / nome_seguro
        with destino.open("wb") as saida:
            shutil.copyfileobj(arquivo.file, saida)

    return processo_id


@router.post("/processos", status_code=201)
async def criar_processo_rota(
    nome: Annotated[str, Form()],
    orgao: Annotated[str | None, Form()] = None,
    modalidade: Annotated[str | None, Form()] = None,
    objeto: Annotated[str | None, Form()] = None,
    valor_estimado: Annotated[float | None, Form()] = None,
    data_sessao: Annotated[str | None, Form()] = None,
    plataforma: Annotated[str | None, Form()] = None,
    arquivos: Annotated[list[UploadFile], File()] = [],
) -> dict:
    """Cria o registro do processo e salva os arquivos enviados. NÃO roda o
    pipeline aqui — só cria e guarda; quem dispara a análise é POST
    /processos/{id}/analisar, separado."""
    processo_id = criar_processo_e_salvar_arquivos(
        {
            "nome": nome,
            "orgao": orgao,
            "modalidade": modalidade,
            "objeto": objeto,
            "valor_estimado": valor_estimado,
            "data_sessao": data_sessao,
            "plataforma": plataforma,
        },
        arquivos,
    )
    return {"id": processo_id}


@router.post("/processos/{id}/analisar")
def analisar_processo_rota(id: int, forcar: bool = False) -> dict:
    """Roda o pipeline (extrai -> IA -> valida -> salva) para os arquivos já
    enviados desse processo. Síncrono: a requisição fica pendurada até
    terminar. Se já tiver sido analisado antes, recusa com 409 a menos que
    ?forcar=true seja passado (ver app/pipeline.py e app/erros.py).

    Não valida aqui se há arquivos: monta a lista (vazia se a pasta não
    existir) e deixa processar_processo decidir — assim o erro de "sem
    arquivos" passa pelos tratadores centralizados de app/erros.py (com o
    log), em vez de um HTTPException solto que os ignora.
    """
    pasta_processo = Path(UPLOAD_DIR) / str(id)
    caminhos = (
        sorted(str(caminho) for caminho in pasta_processo.iterdir() if caminho.is_file())
        if pasta_processo.is_dir()
        else []
    )

    return processar_processo(id, caminhos, forcar_reprocessamento=forcar)


@router.get("/processos")
def listar_processos_rota() -> list[dict]:
    return listar_processos()


@router.get("/processos/{id}")
def obter_processo_rota(id: int) -> dict:
    # Sempre JSON: a versão HTML interativa do checklist tem rota própria,
    # GET /processos/{id}/checklist (app/rotas/paginas.py, Passo 7).
    processo = obter_processo(id)
    if processo is None:
        raise ProcessoNaoEncontradoError(f"processo {id} não encontrado")
    return processo
