# Traduz as exceções que os módulos internos levantam (extrator, llm_client,
# validador, repositorio, pipeline) em respostas HTTP com status e mensagem
# claros. Centralizado aqui em vez de try/except espalhado em cada rota —
# quem escreve uma rota nova só deixa a exceção subir, este módulo cuida do
# resto.
#
# Mapeamento (motivo de cada status na tabela abaixo):
#   ValueError                  -> 400  erro do cliente (ex.: arquivo de
#                                        formato não suportado)
#   sqlite3.IntegrityError      -> 400  dado inválido (ex.: status_check
#                                        fora dos valores aceitos)
#   ProcessoSemArquivosError    -> 400  cliente pediu análise sem ter
#                                        enviado nenhum arquivo antes
#   ProcessoSemTextoExtraidoError -> 400 cliente perguntou sem o processo
#                                        ter sido analisado antes (Fase 2)
#   ProcessoNaoEncontradoError  -> 404  recurso não existe
#   RegistroNaoEncontradoError  -> 404  idem, para exigência
#   ProcessoJaAnalisadoError    -> 409  conflito com o estado atual;
#                                        resolvível com ?forcar=true
#   ContextoGrandeDemaisError   -> 413  o texto do processo excede o limite
#                                        de entrada do modelo (Fase 2)
#   ProvedorNaoSuportadoError   -> 501  funcionalidade não implementada
#   RespostaIAError             -> 502  a API do Gemini foi chamada e falhou
#                                        (ou devolveu algo inaproveitável)
#   ConfiguracaoAusenteError    -> 500  servidor mal configurado (.env sem
#                                        GEMINI_API_KEY/MODEL) — a chamada
#                                        nem chega a ser tentada; não é culpa
#                                        do arquivo enviado nem do Gemini
#   ModoDemoEstaticoError       -> 403  rota de escrita bloqueada porque o
#                                        modo estático do demo público está
#                                        ativo (Fase 3, app/demo_estatico.py)
#   SemDeclaracoesError         -> 400  processo não tem exigência de
#                                        "declaracoes_exigidas" no checklist
#                                        (Fase 4, geração de documento)
#   EmpresaSemRepresentanteError -> 400 empresa selecionada não tem
#                                        representante legal cadastrado
#                                        (Fase 4, geração de documento)
#   SemCatalogoError             -> 400  processo não tem catálogo de itens
#                                        salvo (nunca reprocessado depois da
#                                        funcionalidade existir, ou edital
#                                        sem tabela de itens reconhecível)
#                                        (Fase 4, planilha de preço)
#
# Todo tratador loga a exceção com traceback (logging, nível ERROR) antes de
# montar a resposta — mesmo os 400/404/409 "esperados". Assim o terminal do
# uvicorn sempre mostra o erro de verdade por trás, independente do que o
# cliente recebeu. Configuração do logging fica em app/main.py.

import logging
import sqlite3

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.db.repositorio import RegistroNaoEncontradoError
from app.demo_estatico import ModoDemoEstaticoError
from app.geracao.declaracoes import EmpresaSemRepresentanteError, SemDeclaracoesError
from app.geracao.planilha_preco import SemCatalogoError
from app.ia.llm_client import (
    ConfiguracaoAusenteError,
    ContextoGrandeDemaisError,
    ProvedorNaoSuportadoError,
    RespostaIAError,
)
from app.pipeline import (
    ProcessoJaAnalisadoError,
    ProcessoNaoEncontradoError,
    ProcessoSemArquivosError,
    ProcessoSemTextoExtraidoError,
)

logger = logging.getLogger(__name__)


def _resposta_erro(
    status_code: int, mensagem: str, requisicao: Request, erro: Exception
) -> JSONResponse:
    # Log completo (com traceback) SEMPRE, antes de montar a resposta —
    # inclusive pros 400/404/409 esperados, não só pros 500 inesperados.
    # exc_info=erro (em vez de True) porque não dá pra confiar que ainda
    # estamos dentro do "except" original quando o handler roda; passar a
    # exceção direto funciona não importa onde formos chamados.
    logger.error(
        "%s %s -> HTTP %d: %s",
        requisicao.method,
        requisicao.url.path,
        status_code,
        mensagem,
        exc_info=erro,
    )
    return JSONResponse(
        status_code=status_code,
        content={"detail": mensagem, "path": requisicao.url.path},
    )


def registrar_tratadores_de_erro(app: FastAPI) -> None:
    @app.exception_handler(ValueError)
    async def _tratar_value_error(requisicao: Request, erro: ValueError) -> JSONResponse:
        return _resposta_erro(400, str(erro), requisicao, erro)

    @app.exception_handler(sqlite3.IntegrityError)
    async def _tratar_integrity_error(
        requisicao: Request, erro: sqlite3.IntegrityError
    ) -> JSONResponse:
        return _resposta_erro(400, f"dado inválido: {erro}", requisicao, erro)

    @app.exception_handler(ProcessoSemArquivosError)
    async def _tratar_processo_sem_arquivos(
        requisicao: Request, erro: ProcessoSemArquivosError
    ) -> JSONResponse:
        return _resposta_erro(400, str(erro), requisicao, erro)

    @app.exception_handler(ProcessoSemTextoExtraidoError)
    async def _tratar_processo_sem_texto_extraido(
        requisicao: Request, erro: ProcessoSemTextoExtraidoError
    ) -> JSONResponse:
        return _resposta_erro(400, str(erro), requisicao, erro)

    @app.exception_handler(ProcessoNaoEncontradoError)
    async def _tratar_processo_nao_encontrado(
        requisicao: Request, erro: ProcessoNaoEncontradoError
    ) -> JSONResponse:
        return _resposta_erro(404, str(erro), requisicao, erro)

    @app.exception_handler(RegistroNaoEncontradoError)
    async def _tratar_registro_nao_encontrado(
        requisicao: Request, erro: RegistroNaoEncontradoError
    ) -> JSONResponse:
        return _resposta_erro(404, str(erro), requisicao, erro)

    @app.exception_handler(ProcessoJaAnalisadoError)
    async def _tratar_processo_ja_analisado(
        requisicao: Request, erro: ProcessoJaAnalisadoError
    ) -> JSONResponse:
        return _resposta_erro(409, str(erro), requisicao, erro)

    @app.exception_handler(ProvedorNaoSuportadoError)
    async def _tratar_provedor_nao_suportado(
        requisicao: Request, erro: ProvedorNaoSuportadoError
    ) -> JSONResponse:
        return _resposta_erro(501, str(erro), requisicao, erro)

    @app.exception_handler(RespostaIAError)
    async def _tratar_resposta_ia_error(
        requisicao: Request, erro: RespostaIAError
    ) -> JSONResponse:
        return _resposta_erro(502, str(erro), requisicao, erro)

    @app.exception_handler(ContextoGrandeDemaisError)
    async def _tratar_contexto_grande_demais(
        requisicao: Request, erro: ContextoGrandeDemaisError
    ) -> JSONResponse:
        return _resposta_erro(413, str(erro), requisicao, erro)

    @app.exception_handler(ConfiguracaoAusenteError)
    async def _tratar_configuracao_ausente(
        requisicao: Request, erro: ConfiguracaoAusenteError
    ) -> JSONResponse:
        return _resposta_erro(
            500,
            f"problema de configuração do servidor, não do arquivo enviado: {erro}",
            requisicao,
            erro,
        )

    @app.exception_handler(ModoDemoEstaticoError)
    async def _tratar_modo_demo_estatico(
        requisicao: Request, erro: ModoDemoEstaticoError
    ) -> JSONResponse:
        return _resposta_erro(403, str(erro), requisicao, erro)

    @app.exception_handler(SemDeclaracoesError)
    async def _tratar_sem_declaracoes(
        requisicao: Request, erro: SemDeclaracoesError
    ) -> JSONResponse:
        return _resposta_erro(400, str(erro), requisicao, erro)

    @app.exception_handler(EmpresaSemRepresentanteError)
    async def _tratar_empresa_sem_representante(
        requisicao: Request, erro: EmpresaSemRepresentanteError
    ) -> JSONResponse:
        return _resposta_erro(400, str(erro), requisicao, erro)

    @app.exception_handler(SemCatalogoError)
    async def _tratar_sem_catalogo(
        requisicao: Request, erro: SemCatalogoError
    ) -> JSONResponse:
        return _resposta_erro(400, str(erro), requisicao, erro)
