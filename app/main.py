# Ponto de entrada da aplicação FastAPI. Registra os tratadores de erro
# (app/erros.py), os arquivos estáticos (app/static/) e as rotas
# (app/rotas/) — a lógica de verdade mora nos módulos que cada rota chama,
# não aqui.

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.erros import registrar_tratadores_de_erro
from app.limpeza import executar_limpeza_com_auditoria
from app.rotas import documentos, exigencias, inconsistencias, paginas, perguntas, processos

# Configuração simples do logging padrão do Python (nada de biblioteca
# nova): nível INFO (mostra erro e informação geral, sem o ruído de DEBUG de
# bibliotecas de terceiro) e um formato com data/hora, nível, nome do módulo
# que logou e a mensagem — dá pra saber "quando" e "onde" só olhando o
# terminal. basicConfig() sem "filename" manda tudo pro console (stderr),
# que é onde o uvicorn já roda — não precisa de configuração extra pra
# aparecer no terminal.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _ciclo_de_vida(_app: FastAPI):
    # Limpeza de processos antigos (16/08/2026): roda uma vez a cada
    # subida do servidor, não em cada requisição. Envolta em try/except
    # de propósito — um bug na rotina de limpeza não pode derrubar o
    # servidor inteiro (ela é manutenção de fundo, não uma dependência
    # crítica pra a aplicação funcionar), mas o erro NUNCA fica
    # silencioso: vai pro log padrão (console) sempre, e tenta ir pro log
    # de auditoria também, pra não sumir sem rastro nenhum.
    try:
        executar_limpeza_com_auditoria()
    except Exception:
        logger.exception("falha ao rodar a limpeza automática de processos antigos no startup")
    yield


app = FastAPI(title="NexLicit Engine", lifespan=_ciclo_de_vida)

registrar_tratadores_de_erro(app)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# paginas ANTES de processos de propósito: GET/POST /processos/novo (rota
# literal, paginas.py) precisa ser testada antes de GET /processos/{id}
# (rota com parâmetro, processos.py) — senão "novo" seria capturado como
# tentativa de id.
#
# Isso não é só cautela: testei invertendo a ordem e confirmei que o
# FastAPI/Starlette NÃO tenta a próxima rota quando a conversão de tipo do
# parâmetro falha — ele já se comprometeu com a primeira rota cujo padrão de
# caminho bateu, na ordem de registro. Com processos ANTES de paginas,
# GET /processos/novo vira 422 (int_parsing, "novo" não é int) em vez de
# cair na rota certa. Se um dia outra rota de path literal for adicionada
# sob /processos/, ela precisa ser registrada antes de processos.router
# também, pelo mesmo motivo.
app.include_router(paginas.router)
app.include_router(processos.router)
app.include_router(documentos.router)
app.include_router(exigencias.router)
app.include_router(perguntas.router)
app.include_router(inconsistencias.router)


@app.get("/health")
def health_check():
    # Retorna um status simples em JSON. É o jeito padrão de verificar
    # se uma API está no ar, sem depender de banco de dados ou de IA.
    return {"status": "ok"}
