# Testes da limpeza automática de processos antigos (16/08/2026,
# app/limpeza.py): processo com "criado_em" há mais de 30 dias é removido
# do banco (processo + tudo que referencia ele) e da pasta de upload
# correspondente. Cobre: processo expirado é removido, processo recente é
# preservado, cascade não deixa linha órfã em nenhuma tabela filha, pasta
# de upload é removida com contagem de bytes, "empresa" nunca é tocada, e
# o log de auditoria grava nos dois casos (com e sem remoção).
#
# Mesmo padrão de isolamento de tests/test_db.py: caminho_db aponta pra um
# arquivo dentro do tmp_path do pytest, nunca o banco real.

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.db.conexao import obter_conexao
from app.db.repositorio import (
    criar_arquivo,
    criar_empresa,
    criar_processo,
    listar_empresas,
    obter_processo,
    salvar_catalogo_itens,
    salvar_exigencias,
    salvar_inconsistencias,
    salvar_preco_item,
    salvar_requisitos_item,
    salvar_texto_paginas,
)
from app.limpeza import executar_limpeza_com_auditoria, executar_limpeza_de_processos_antigos


@pytest.fixture
def caminho_db(tmp_path) -> str:
    return str(tmp_path / "teste.db")


@pytest.fixture
def pasta_uploads(tmp_path) -> Path:
    pasta = tmp_path / "uploads"
    pasta.mkdir()
    return pasta


def _definir_criado_em(processo_id: int, dias_atras: int, caminho_db: str) -> None:
    """Ajusta "criado_em" de um processo direto no banco -- criar_processo
    sempre usa a hora atual (repositorio._agora_iso), sem parâmetro pra
    sobrescrever. Pra testar idade sem esperar 30 dias de verdade, ajusta a
    linha depois de criada."""
    momento = datetime.now(timezone.utc) - timedelta(days=dias_atras)
    conexao = obter_conexao(caminho_db)
    try:
        conexao.execute(
            "UPDATE processo SET criado_em = ? WHERE id = ?", (momento.isoformat(), processo_id)
        )
        conexao.commit()
    finally:
        conexao.close()


def _processo_completo(
    caminho_db: str,
    pasta_uploads: Path,
    dias_atras: int,
    nome: str = "Processo de teste",
    orgao: str = "Órgão de teste",
) -> int:
    """Cria um processo com uma linha em CADA tabela filha (arquivo,
    texto_pagina, exigencia, requisito_item, inconsistencia, item_catalogo,
    preco_item) e um arquivo físico em uploads/{id}/ -- pra testar cascade
    e limpeza de disco de verdade, não só a tabela "processo" isolada."""
    processo_id = criar_processo({"nome": nome, "orgao": orgao}, caminho_banco=caminho_db)
    _definir_criado_em(processo_id, dias_atras, caminho_db)

    arquivo_id = criar_arquivo(
        processo_id, {"nome_arquivo": "edital.pdf", "tipo": "pdf"}, caminho_banco=caminho_db
    )
    salvar_texto_paginas(
        processo_id, arquivo_id,
        [{"numero_pagina": 1, "localizador": "página 1", "texto": "texto qualquer"}],
        caminho_banco=caminho_db,
    )
    salvar_exigencias(
        processo_id,
        [{
            "categoria": "habilitacao_juridica", "descricao": "Exigência", "trecho": "trecho",
            "arquivo_origem": "edital.pdf", "obrigatorio_para": "todos", "confianca": "localizado",
        }],
        caminho_banco=caminho_db,
    )
    salvar_requisitos_item(
        processo_id,
        [{
            "numero_item": 1, "categoria": "especificacao_tecnica", "gatilho": "gatilho",
            "trecho": "trecho do item", "arquivo_origem": "edital.pdf",
        }],
        caminho_banco=caminho_db,
    )
    salvar_inconsistencias(
        processo_id,
        [{
            "tipo": "quantidade", "descricao": "descrição", "trecho_edital": "trecho edital",
            "trecho_tr": "trecho tr",
        }],
        caminho_banco=caminho_db,
    )
    salvar_catalogo_itens(
        processo_id,
        [{"numero": 1, "texto_bruto": "1 Item de teste UND 10", "pagina": 1, "localizador": "página 1"}],
        caminho_banco=caminho_db,
    )
    salvar_preco_item(processo_id, 1, quantidade=10, preco_unitario=5.5, caminho_banco=caminho_db)

    pasta_processo = pasta_uploads / str(processo_id)
    pasta_processo.mkdir()
    (pasta_processo / "edital.pdf").write_bytes(b"conteudo de teste" * 100)

    return processo_id


def _contar_linhas(tabela: str, processo_id: int, caminho_db: str) -> int:
    conexao = obter_conexao(caminho_db)
    try:
        return conexao.execute(
            f"SELECT COUNT(*) FROM {tabela} WHERE processo_id = ?", (processo_id,)
        ).fetchone()[0]
    finally:
        conexao.close()


def test_processo_com_mais_de_30_dias_e_removido(caminho_db, pasta_uploads):
    processo_id = _processo_completo(caminho_db, pasta_uploads, dias_atras=31)

    resultado = executar_limpeza_de_processos_antigos(
        caminho_banco=caminho_db, upload_dir=str(pasta_uploads)
    )

    assert resultado["quantidade_removida"] == 1
    assert resultado["removidos"][0]["id"] == processo_id
    assert resultado["removidos"][0]["nome"] == "Processo de teste"
    assert obter_processo(processo_id, caminho_banco=caminho_db) is None


def test_processo_com_menos_de_30_dias_nao_e_tocado(caminho_db, pasta_uploads):
    processo_id = _processo_completo(caminho_db, pasta_uploads, dias_atras=10)

    resultado = executar_limpeza_de_processos_antigos(
        caminho_banco=caminho_db, upload_dir=str(pasta_uploads)
    )

    assert resultado["quantidade_removida"] == 0
    assert resultado["removidos"] == []
    processo = obter_processo(processo_id, caminho_banco=caminho_db)
    assert processo is not None
    assert processo["nome"] == "Processo de teste"


def test_cascade_nao_deixa_linha_orfa_em_nenhuma_tabela_filha(caminho_db, pasta_uploads):
    processo_id = _processo_completo(caminho_db, pasta_uploads, dias_atras=45)

    executar_limpeza_de_processos_antigos(caminho_banco=caminho_db, upload_dir=str(pasta_uploads))

    for tabela in (
        "arquivo", "texto_pagina", "exigencia", "requisito_item", "inconsistencia",
        "item_catalogo", "preco_item",
    ):
        assert _contar_linhas(tabela, processo_id, caminho_db) == 0, (
            f"tabela {tabela} ficou com linha órfã do processo {processo_id} excluído"
        )


def test_pasta_de_upload_e_removida_com_contagem_de_bytes(caminho_db, pasta_uploads):
    processo_id = _processo_completo(caminho_db, pasta_uploads, dias_atras=40)
    pasta_processo = pasta_uploads / str(processo_id)
    tamanho_esperado = (pasta_processo / "edital.pdf").stat().st_size
    assert pasta_processo.is_dir()

    resultado = executar_limpeza_de_processos_antigos(
        caminho_banco=caminho_db, upload_dir=str(pasta_uploads)
    )

    assert not pasta_processo.exists()
    assert resultado["quantidade_arquivos_removidos"] == 1
    assert resultado["bytes_liberados"] == tamanho_esperado


def test_processo_expirado_sem_arquivos_nao_gera_erro(caminho_db, pasta_uploads):
    # Processo sem nenhum arquivo físico (pasta uploads/{id}/ nunca chegou
    # a ser criada) -- não pode quebrar a limpeza, só não soma nada de
    # espaço liberado pra ele.
    processo_id = criar_processo({"nome": "Sem arquivo"}, caminho_banco=caminho_db)
    _definir_criado_em(processo_id, 40, caminho_db)

    resultado = executar_limpeza_de_processos_antigos(
        caminho_banco=caminho_db, upload_dir=str(pasta_uploads)
    )

    assert resultado["quantidade_removida"] == 1
    assert resultado["quantidade_arquivos_removidos"] == 0
    assert resultado["bytes_liberados"] == 0


def test_empresa_nunca_e_tocada_pela_limpeza(caminho_db, pasta_uploads):
    _processo_completo(caminho_db, pasta_uploads, dias_atras=60)
    criar_empresa(
        {"razao_social": "Empresa Teste LTDA", "cnpj": "00.000.000/0001-00"},
        caminho_banco=caminho_db,
    )

    executar_limpeza_de_processos_antigos(caminho_banco=caminho_db, upload_dir=str(pasta_uploads))

    empresas = listar_empresas(caminho_banco=caminho_db)
    assert len(empresas) == 1
    assert empresas[0]["razao_social"] == "Empresa Teste LTDA"


def test_log_de_auditoria_grava_quando_remove_processo(caminho_db, pasta_uploads, tmp_path):
    caminho_log = str(tmp_path / "limpeza.log")
    _processo_completo(caminho_db, pasta_uploads, dias_atras=35, nome="Edital Antigo", orgao="Prefeitura X")

    executar_limpeza_com_auditoria(
        caminho_banco=caminho_db, upload_dir=str(pasta_uploads), caminho_log=caminho_log
    )

    conteudo = Path(caminho_log).read_text(encoding="utf-8")
    assert "removidos: 1" in conteudo
    assert "Edital Antigo" in conteudo
    assert "Prefeitura X" in conteudo


def test_log_de_auditoria_grava_mesmo_sem_remover_nada(caminho_db, pasta_uploads, tmp_path):
    caminho_log = str(tmp_path / "limpeza.log")
    _processo_completo(caminho_db, pasta_uploads, dias_atras=5)

    executar_limpeza_com_auditoria(
        caminho_banco=caminho_db, upload_dir=str(pasta_uploads), caminho_log=caminho_log
    )

    conteudo = Path(caminho_log).read_text(encoding="utf-8")
    assert "removidos: 0" in conteudo
    assert "nenhum processo removido" in conteudo


def test_log_de_auditoria_acumula_varias_execucoes(caminho_db, pasta_uploads, tmp_path):
    # A rotina roda a cada subida do servidor -- o arquivo de log precisa
    # ACUMULAR entradas (modo "append"), não sobrescrever a execução
    # anterior a cada startup.
    caminho_log = str(tmp_path / "limpeza.log")

    executar_limpeza_com_auditoria(
        caminho_banco=caminho_db, upload_dir=str(pasta_uploads), caminho_log=caminho_log
    )
    executar_limpeza_com_auditoria(
        caminho_banco=caminho_db, upload_dir=str(pasta_uploads), caminho_log=caminho_log
    )

    conteudo = Path(caminho_log).read_text(encoding="utf-8")
    assert conteudo.count("limpeza automática") == 2
