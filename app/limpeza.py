# Limpeza de processos antigos (expiração automática por idade,
# 16/08/2026). Decisão de produto: processo com mais de 30 dias desde
# "criado_em" é removido — do banco (processo + tudo que referencia ele)
# e da pasta de upload correspondente (uploads/{processo_id}/).
#
# Duas camadas separadas de propósito:
#   - executar_limpeza_de_processos_antigos(): a lógica pura (quem
#     expirou, apaga do banco, apaga a pasta de upload, soma o que foi
#     liberado). Devolve um resultado estruturado, não grava nada em
#     disco além do que foi pedido pra apagar — fácil de testar sozinha,
#     contra um banco de cópia/tmp_path.
#   - executar_limpeza_com_auditoria(): chama a de cima e grava o log de
#     auditoria (logs/limpeza_automatica.log) — usada pelo startup do
#     FastAPI (app/main.py). Log é OBRIGATÓRIO mesmo quando zero
#     processos são removidos: "rodou e não achou nada pra limpar" é uma
#     informação tão importante quanto "removeu X" pra confirmar que a
#     rotina está executando de verdade a cada subida do servidor.
#
# "empresa" (Fase 4, cadastro de fornecedores) NUNCA entra aqui — nem
# indiretamente: não existe FK de processo para empresa no schema (ver
# schema.sql), e esta função nunca referencia a tabela empresa em SQL
# nenhum. Só pode ser excluída manualmente pelo usuário.

from __future__ import annotations

import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.config import UPLOAD_DIR
from app.db.repositorio import excluir_processo, listar_processos

logger = logging.getLogger(__name__)

DIAS_LIMITE_PADRAO = 30

# Caminho relativo à raiz do projeto (mesma convenção de UPLOAD_DIR e
# DATABASE_PATH: nome de módulo, não valor fixo importado por cópia —
# assim monkeypatch.setattr("app.limpeza.CAMINHO_LOG_AUDITORIA", ...)
# funciona nos testes, isolando o log real igual já se isola banco/upload).
CAMINHO_LOG_AUDITORIA = "logs/limpeza_automatica.log"


def _processo_expirado(criado_em_iso: str, limite: datetime) -> bool:
    # "criado_em" é gravado sempre em UTC explícito (repositorio._agora_iso)
    # — comparar direto em UTC contra outro UTC não precisa de nenhuma
    # conversão de fuso. A conversão pra hora local (astimezone(), ver
    # app/rotas/paginas.py:_formatar_data_criacao) só existe pra EXIBIR
    # data pro usuário — não serve, e não deveria ser usada, pra comparar
    # dois instantes entre si.
    momento = datetime.fromisoformat(criado_em_iso)
    return momento < limite


def executar_limpeza_de_processos_antigos(
    dias_limite: int = DIAS_LIMITE_PADRAO,
    caminho_banco: str | None = None,
    upload_dir: str | None = None,
) -> dict[str, Any]:
    """Remove todo processo com "criado_em" mais antigo que `dias_limite`
    dias — do banco e da pasta de upload correspondente. Devolve um
    resultado estruturado (nunca levanta por processo individual: uma
    falha ao apagar a pasta de upload de um processo não impede os
    outros de serem processados, mas interrompe e propaga se a EXCLUSÃO
    NO BANCO falhar, porque aí o estado ficaria inconsistente demais pra
    seguir em frente silenciosamente).

    `upload_dir` opcional (senão usa UPLOAD_DIR do config) — mesmo
    padrão de `caminho_banco`, pra isolar em teste sem depender de
    variável de ambiente.
    """
    limite = datetime.now(timezone.utc) - timedelta(days=dias_limite)
    pasta_uploads = Path(upload_dir if upload_dir is not None else UPLOAD_DIR)

    processos = listar_processos(caminho_banco=caminho_banco)
    expirados = [p for p in processos if _processo_expirado(p["criado_em"], limite)]

    removidos: list[dict[str, Any]] = []
    quantidade_arquivos_removidos = 0
    bytes_liberados = 0

    for processo in expirados:
        pasta_processo = pasta_uploads / str(processo["id"])
        if pasta_processo.is_dir():
            arquivos = [caminho for caminho in pasta_processo.rglob("*") if caminho.is_file()]
            quantidade_arquivos_removidos += len(arquivos)
            bytes_liberados += sum(caminho.stat().st_size for caminho in arquivos)
            shutil.rmtree(pasta_processo)

        excluir_processo(processo["id"], caminho_banco=caminho_banco)

        removidos.append({
            "id": processo["id"],
            "nome": processo["nome"],
            "orgao": processo["orgao"],
            "criado_em": processo["criado_em"],
        })
        logger.info(
            "processo expirado removido: id=%s nome=%r orgao=%r (criado em %s)",
            processo["id"], processo["nome"], processo["orgao"], processo["criado_em"],
        )

    return {
        "dias_limite": dias_limite,
        "removidos": removidos,
        "quantidade_removida": len(removidos),
        "quantidade_arquivos_removidos": quantidade_arquivos_removidos,
        "bytes_liberados": bytes_liberados,
    }


def _formatar_linha_auditoria(resultado: dict[str, Any]) -> str:
    agora = datetime.now(timezone.utc).isoformat()
    linhas = [
        f"{agora} - limpeza automática (limite: {resultado['dias_limite']} dias) "
        f"- removidos: {resultado['quantidade_removida']} "
        f"- arquivos removidos: {resultado['quantidade_arquivos_removidos']} "
        f"({resultado['bytes_liberados']} bytes liberados)"
    ]
    if resultado["removidos"]:
        for processo in resultado["removidos"]:
            linhas.append(
                f"  - [{processo['id']}] {processo['nome']!r} "
                f"(órgão: {processo['orgao']!r}, criado em {processo['criado_em']})"
            )
    else:
        linhas.append("  (nenhum processo removido nesta execução)")
    return "\n".join(linhas) + "\n"


def registrar_auditoria(resultado: dict[str, Any], caminho_log: str | None = None) -> None:
    """Grava o resultado de uma execução no log de auditoria — sempre,
    mesmo com zero remoção (é o "rodou, nada pra limpar" que confirma que
    a rotina está executando a cada startup, não só quando remove algo).
    """
    caminho = Path(caminho_log if caminho_log is not None else CAMINHO_LOG_AUDITORIA)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with caminho.open("a", encoding="utf-8") as arquivo_log:
        arquivo_log.write(_formatar_linha_auditoria(resultado))


def executar_limpeza_com_auditoria(
    dias_limite: int = DIAS_LIMITE_PADRAO,
    caminho_banco: str | None = None,
    upload_dir: str | None = None,
    caminho_log: str | None = None,
) -> dict[str, Any]:
    """Roda a limpeza e grava o log de auditoria em seguida — é isto que
    o startup do FastAPI chama (app/main.py). Separado de
    executar_limpeza_de_processos_antigos() pra essa função de baixo
    poder ser testada isoladamente, sem precisar inspecionar arquivo de
    log."""
    resultado = executar_limpeza_de_processos_antigos(
        dias_limite=dias_limite, caminho_banco=caminho_banco, upload_dir=upload_dir,
    )
    registrar_auditoria(resultado, caminho_log=caminho_log)
    return resultado
