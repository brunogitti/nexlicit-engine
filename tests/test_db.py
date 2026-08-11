# Testes do módulo de persistência (app/db/). Cada teste recebe a fixture
# caminho_db, que aponta pra um arquivo SQLite novo dentro do diretório
# temporário do pytest (tmp_path) — isolado por teste, nada de :memory:
# (motivo: cada função do repositório abre sua própria conexão; com
# :memory: cada conexão veria um banco vazio diferente).

import json
import sqlite3
from pathlib import Path

import pytest

from app.db.conexao import obter_conexao
from app.db.repositorio import (
    atualizar_status_exigencia,
    criar_arquivo,
    criar_processo,
    listar_processos,
    obter_processo,
    salvar_exigencias,
    salvar_texto_paginas,
)
from app.extracao.extrator import Bloco, DocumentoExtraido
from app.ia.llm_client import _parsear_resposta
from app.validacao.validador import validar_exigencias

CAMINHO_FIXTURE_OURO = (
    Path(__file__).resolve().parent.parent / "editais-reais" / "teste_ouro_camara_lins.json"
)


@pytest.fixture
def caminho_db(tmp_path) -> str:
    return str(tmp_path / "teste.db")


def _exigencia_sintetica(**overrides) -> dict:
    base = {
        "categoria": "habilitacao_fiscal_social_trabalhista",
        "descricao": "descrição de teste",
        "base_legal": None,
        "trecho": "texto qualquer o suficiente para passar do limite mínimo",
        "obrigatorio_para": "todos",
        "confianca": "inferido",
        "pagina": None,
        "localizador": None,
        "arquivo_origem": None,
        "cruzou_pagina": False,
        "ocorrencias_encontradas": 0,
    }
    base.update(overrides)
    return base


def test_fluxo_completo_cria_processo_arquivo_exigencias_e_recupera(caminho_db):
    processo_id = criar_processo(
        {"nome": "Dispensa 001/2026", "orgao": "Prefeitura Teste", "modalidade": "Dispensa"},
        caminho_banco=caminho_db,
    )
    arquivo_id = criar_arquivo(
        processo_id,
        {"nome_arquivo": "edital.pdf", "tipo": "pdf", "num_paginas": 3, "texto_extraido": "..."},
        caminho_banco=caminho_db,
    )

    exigencias = [
        _exigencia_sintetica(
            categoria="habilitacao_juridica",
            descricao="Contrato social",
            confianca="localizado",
            pagina="1",
            localizador="página 1",
            arquivo_origem="edital.pdf",
            ocorrencias_encontradas=1,
        ),
        _exigencia_sintetica(
            categoria="declaracoes_exigidas",
            descricao="Declaração de ME/EPP",
            confianca="inferido",
        ),
    ]
    ids_exigencias = salvar_exigencias(processo_id, exigencias, caminho_banco=caminho_db)
    assert len(ids_exigencias) == 2

    processo = obter_processo(processo_id, caminho_banco=caminho_db)

    assert processo is not None
    assert processo["nome"] == "Dispensa 001/2026"
    assert processo["orgao"] == "Prefeitura Teste"
    assert len(processo["arquivos"]) == 1
    assert processo["arquivos"][0]["id"] == arquivo_id
    assert processo["arquivos"][0]["nome_arquivo"] == "edital.pdf"

    assert len(processo["exigencias"]) == 2
    localizada = next(e for e in processo["exigencias"] if e["descricao"] == "Contrato social")
    assert localizada["confianca"] == "localizado"
    assert localizada["pagina"] == "1"
    assert localizada["arquivo_origem_id"] == arquivo_id
    assert localizada["cruzou_pagina"] is False
    assert localizada["status_check"] == "pendente"  # default da tabela

    inferida = next(e for e in processo["exigencias"] if e["descricao"] == "Declaração de ME/EPP")
    assert inferida["confianca"] == "inferido"
    assert inferida["arquivo_origem_id"] is None


def test_grupo_hipoteses_persiste_e_volta_no_obter_processo(caminho_db):
    # Passo 9 (Mudança 3): quando a IA marca duas exigências como
    # alternativas entre si (mesmo grupo_hipoteses), isso precisa sobreviver
    # ao salvar/reler — é o que app/rotas/paginas.py usa pra montar o card
    # agrupado com um checkbox só.
    processo_id = criar_processo({"nome": "Processo teste"}, caminho_banco=caminho_db)
    exigencias = [
        _exigencia_sintetica(
            categoria="habilitacao_juridica",
            descricao="Registro comercial",
            grupo_hipoteses="Documento constitutivo da empresa",
        ),
        _exigencia_sintetica(
            categoria="habilitacao_juridica",
            descricao="Contrato social",
            grupo_hipoteses="Documento constitutivo da empresa",
        ),
        _exigencia_sintetica(categoria="habilitacao_juridica", descricao="CNPJ"),
    ]
    salvar_exigencias(processo_id, exigencias, caminho_banco=caminho_db)

    processo = obter_processo(processo_id, caminho_banco=caminho_db)
    assert processo is not None

    cnpj = next(e for e in processo["exigencias"] if e["descricao"] == "CNPJ")
    assert cnpj["grupo_hipoteses"] is None

    alternativas = [
        e["grupo_hipoteses"]
        for e in processo["exigencias"]
        if e["descricao"] in ("Registro comercial", "Contrato social")
    ]
    assert alternativas == ["Documento constitutivo da empresa"] * 2


def test_obter_processo_inexistente_devolve_none(caminho_db):
    assert obter_processo(999, caminho_banco=caminho_db) is None


def test_foreign_key_impede_exigencia_com_processo_inexistente(caminho_db):
    exigencia = _exigencia_sintetica()

    with pytest.raises(sqlite3.IntegrityError):
        salvar_exigencias(999999, [exigencia], caminho_banco=caminho_db)


def test_salvar_exigencias_com_arquivo_origem_nao_registrado_da_erro_claro(caminho_db):
    processo_id = criar_processo({"nome": "Processo teste"}, caminho_banco=caminho_db)
    exigencia = _exigencia_sintetica(arquivo_origem="arquivo_que_nao_existe.pdf")

    with pytest.raises(ValueError, match="arquivo_que_nao_existe.pdf"):
        salvar_exigencias(processo_id, [exigencia], caminho_banco=caminho_db)


def test_atualizar_status_exigencia_persiste(caminho_db):
    processo_id = criar_processo({"nome": "Processo teste"}, caminho_banco=caminho_db)
    ids = salvar_exigencias(processo_id, [_exigencia_sintetica()], caminho_banco=caminho_db)
    id_exigencia = ids[0]

    atualizar_status_exigencia(id_exigencia, "ok", "conferido manualmente", caminho_banco=caminho_db)

    processo = obter_processo(processo_id, caminho_banco=caminho_db)
    assert processo is not None
    exigencia_atualizada = processo["exigencias"][0]
    assert exigencia_atualizada["status_check"] == "ok"
    assert exigencia_atualizada["observacao_usuario"] == "conferido manualmente"


def test_atualizar_status_sem_observacao_preserva_a_anterior(caminho_db):
    processo_id = criar_processo({"nome": "Processo teste"}, caminho_banco=caminho_db)
    ids = salvar_exigencias(processo_id, [_exigencia_sintetica()], caminho_banco=caminho_db)
    id_exigencia = ids[0]

    atualizar_status_exigencia(id_exigencia, "ok", "nota original", caminho_banco=caminho_db)
    atualizar_status_exigencia(id_exigencia, "nao_aplica", caminho_banco=caminho_db)

    processo = obter_processo(processo_id, caminho_banco=caminho_db)
    assert processo is not None
    exigencia_atualizada = processo["exigencias"][0]
    assert exigencia_atualizada["status_check"] == "nao_aplica"
    assert exigencia_atualizada["observacao_usuario"] == "nota original"


def test_listar_processos_devolve_varios_na_ordem_esperada(caminho_db):
    id_1 = criar_processo({"nome": "Primeiro processo"}, caminho_banco=caminho_db)
    id_2 = criar_processo({"nome": "Segundo processo"}, caminho_banco=caminho_db)
    id_3 = criar_processo({"nome": "Terceiro processo"}, caminho_banco=caminho_db)

    processos = listar_processos(caminho_banco=caminho_db)

    assert len(processos) == 3
    # Mais recente primeiro (ORDER BY criado_em DESC, id DESC).
    assert [p["id"] for p in processos] == [id_3, id_2, id_1]


@pytest.mark.skipif(
    not CAMINHO_FIXTURE_OURO.exists(),
    reason=(
        "fixture do teste de ouro (dado real, fora do git) não está "
        "presente nesta máquina — ver editais-reais/ no .gitignore"
    ),
)
def test_fluxo_completo_extrai_valida_salva_recupera_teste_de_ouro(caminho_db):
    with open(CAMINHO_FIXTURE_OURO, encoding="utf-8") as arquivo:
        dados_fixture = json.load(arquivo)

    # "Extrai" (já temos o resultado do Passo 2 congelado na fixture).
    bloco = Bloco(
        pagina=dados_fixture["documento"]["pagina"],
        localizador=dados_fixture["documento"]["localizador"],
        texto=dados_fixture["documento"]["texto"],
    )
    documento = DocumentoExtraido(
        nome_arquivo=dados_fixture["documento"]["nome_arquivo"],
        tipo="pdf",
        num_paginas=dados_fixture["documento"]["pagina"],
        blocos=[bloco],
        alertas=[],
    )

    # "Achata" (Passo 3, de verdade — a fixture guarda o JSON aninhado por
    # categoria como o Gemini realmente devolve, não um resultado já
    # achatado à mão).
    resposta_bruta = json.dumps(dados_fixture["resposta_ia_bruta_por_categoria"])
    exigencias_extraidas = _parsear_resposta(resposta_bruta)

    # "Valida" (Passo 4, de verdade, sem mock).
    exigencias_validadas = validar_exigencias(exigencias_extraidas, [documento])
    assert all(e["confianca"] == "localizado" for e in exigencias_validadas)

    # "Salva" (Passo 5, este módulo).
    processo_id = criar_processo(
        {
            "nome": "Dispensa 056/2026",
            "orgao": "Câmara Municipal de Lins",
            "modalidade": "Dispensa de Licitação",
        },
        caminho_banco=caminho_db,
    )
    arquivo_id = criar_arquivo(
        processo_id,
        {
            "nome_arquivo": documento.nome_arquivo,
            "tipo": "pdf",
            "num_paginas": documento.num_paginas,
            "texto_extraido": bloco.texto,
        },
        caminho_banco=caminho_db,
    )
    salvar_exigencias(processo_id, exigencias_validadas, caminho_banco=caminho_db)

    # "Recupera".
    processo = obter_processo(processo_id, caminho_banco=caminho_db)

    assert processo is not None
    assert len(processo["exigencias"]) == 5
    for exigencia in processo["exigencias"]:
        assert exigencia["confianca"] == "localizado"
        assert exigencia["arquivo_origem_id"] == arquivo_id
        assert exigencia["pagina"] == str(dados_fixture["documento"]["pagina"])


# ---------- Texto por página (Fase 2, Camada 0) ----------


def test_salvar_texto_paginas_persiste_e_permite_reler(caminho_db):
    processo_id = criar_processo({"nome": "Processo teste"}, caminho_banco=caminho_db)
    arquivo_id = criar_arquivo(
        processo_id,
        {"nome_arquivo": "edital.pdf", "tipo": "pdf", "num_paginas": 2},
        caminho_banco=caminho_db,
    )

    ids = salvar_texto_paginas(
        processo_id,
        arquivo_id,
        [
            {"numero_pagina": 1, "localizador": "página 1", "texto": "Texto da página um."},
            {"numero_pagina": 2, "localizador": "página 2", "texto": "Texto da página dois."},
        ],
        caminho_banco=caminho_db,
    )
    assert len(ids) == 2

    # obter_processo não expõe texto_pagina de propósito (a tabela pode
    # ter centenas de linhas, e a maioria de quem chama obter_processo não
    # precisa do texto bruto inteiro) — lê direto pra conferir persistência.
    conexao = obter_conexao(caminho_db)
    linhas = conexao.execute(
        "SELECT * FROM texto_pagina WHERE processo_id = ? ORDER BY numero_pagina", (processo_id,)
    ).fetchall()
    conexao.close()

    assert len(linhas) == 2
    assert linhas[0]["arquivo_id"] == arquivo_id
    assert linhas[0]["numero_pagina"] == 1
    assert linhas[0]["localizador"] == "página 1"
    assert linhas[0]["texto"] == "Texto da página um."
    assert linhas[1]["numero_pagina"] == 2


def test_salvar_texto_paginas_aceita_numero_pagina_nulo_para_docx(caminho_db):
    processo_id = criar_processo({"nome": "Processo teste"}, caminho_banco=caminho_db)
    arquivo_id = criar_arquivo(
        processo_id,
        {"nome_arquivo": "termo.docx", "tipo": "docx"},
        caminho_banco=caminho_db,
    )

    salvar_texto_paginas(
        processo_id,
        arquivo_id,
        [{"numero_pagina": None, "localizador": "parágrafo 1", "texto": "Texto do parágrafo."}],
        caminho_banco=caminho_db,
    )

    conexao = obter_conexao(caminho_db)
    linha = conexao.execute("SELECT * FROM texto_pagina WHERE processo_id = ?", (processo_id,)).fetchone()
    conexao.close()

    assert linha["numero_pagina"] is None
    assert linha["localizador"] == "parágrafo 1"


def test_limpar_analise_do_processo_remove_texto_pagina_tambem(caminho_db):
    # Regressão: limpar_analise_do_processo apagava "arquivo" sem apagar
    # "texto_pagina" antes — violava a FK ao reprocessar (forcar_reprocessamento).
    processo_id = criar_processo({"nome": "Processo teste"}, caminho_banco=caminho_db)
    arquivo_id = criar_arquivo(
        processo_id,
        {"nome_arquivo": "edital.pdf", "tipo": "pdf"},
        caminho_banco=caminho_db,
    )
    salvar_texto_paginas(
        processo_id,
        arquivo_id,
        [{"numero_pagina": 1, "localizador": "página 1", "texto": "Texto."}],
        caminho_banco=caminho_db,
    )

    from app.db.repositorio import limpar_analise_do_processo

    limpar_analise_do_processo(processo_id, caminho_banco=caminho_db)

    conexao = obter_conexao(caminho_db)
    total = conexao.execute(
        "SELECT COUNT(*) FROM texto_pagina WHERE processo_id = ?", (processo_id,)
    ).fetchone()[0]
    conexao.close()
    assert total == 0


# ---------- inconsistencia (motor de inconsistências edital-vs-TR, Camada 1) ----------


def _inconsistencia_sintetica(**overrides) -> dict:
    base = {
        "tipo": "prazo",
        "descricao": "prazo de entrega diverge entre edital e TR",
        "trecho_edital": "O prazo de entrega será de 30 dias.",
        "pagina_edital": 5,
        "trecho_tr": "O prazo de entrega será de 45 dias.",
        "pagina_tr": 20,
    }
    base.update(overrides)
    return base


def test_salvar_inconsistencias_persiste_e_permite_reler(caminho_db):
    from app.db.repositorio import obter_inconsistencias, salvar_inconsistencias

    processo_id = criar_processo({"nome": "Processo teste"}, caminho_banco=caminho_db)

    ids = salvar_inconsistencias(
        processo_id,
        [
            _inconsistencia_sintetica(),
            _inconsistencia_sintetica(tipo="valor", descricao="valor diverge", pagina_edital=6, pagina_tr=21),
        ],
        caminho_banco=caminho_db,
    )

    assert len(ids) == 2

    salvas = obter_inconsistencias(processo_id, caminho_banco=caminho_db)
    assert len(salvas) == 2
    assert salvas[0]["tipo"] == "prazo"
    assert salvas[0]["trecho_edital"] == "O prazo de entrega será de 30 dias."
    assert salvas[0]["pagina_edital"] == 5
    assert salvas[0]["trecho_tr"] == "O prazo de entrega será de 45 dias."
    assert salvas[0]["pagina_tr"] == 20
    assert salvas[1]["tipo"] == "valor"


def test_obter_inconsistencias_devolve_lista_vazia_quando_nunca_rodou(caminho_db):
    from app.db.repositorio import obter_inconsistencias

    processo_id = criar_processo({"nome": "Processo teste"}, caminho_banco=caminho_db)

    assert obter_inconsistencias(processo_id, caminho_banco=caminho_db) == []


def test_limpar_inconsistencias_do_processo_remove_so_as_daquele_processo(caminho_db):
    from app.db.repositorio import (
        limpar_inconsistencias_do_processo,
        obter_inconsistencias,
        salvar_inconsistencias,
    )

    processo_1 = criar_processo({"nome": "Processo 1"}, caminho_banco=caminho_db)
    processo_2 = criar_processo({"nome": "Processo 2"}, caminho_banco=caminho_db)
    salvar_inconsistencias(processo_1, [_inconsistencia_sintetica()], caminho_banco=caminho_db)
    salvar_inconsistencias(processo_2, [_inconsistencia_sintetica()], caminho_banco=caminho_db)

    limpar_inconsistencias_do_processo(processo_1, caminho_banco=caminho_db)

    assert obter_inconsistencias(processo_1, caminho_banco=caminho_db) == []
    assert len(obter_inconsistencias(processo_2, caminho_banco=caminho_db)) == 1


# ---------- status de detecção (motor de inconsistências, Camada 2 — UI) ----------


def test_processo_novo_nao_tem_status_de_deteccao(caminho_db):
    processo_id = criar_processo({"nome": "Processo teste"}, caminho_banco=caminho_db)

    processo = obter_processo(processo_id, caminho_banco=caminho_db)

    assert processo["inconsistencias_verificado_em"] is None
    assert processo["inconsistencias_comparacao_possivel"] is None
    assert processo["inconsistencias_motivo_impossibilidade"] is None


def test_atualizar_status_deteccao_inconsistencias_persiste_possivel(caminho_db):
    from app.db.repositorio import atualizar_status_deteccao_inconsistencias

    processo_id = criar_processo({"nome": "Processo teste"}, caminho_banco=caminho_db)

    atualizar_status_deteccao_inconsistencias(
        processo_id, comparacao_possivel=True, motivo_impossibilidade=None, caminho_banco=caminho_db
    )

    processo = obter_processo(processo_id, caminho_banco=caminho_db)
    assert processo["inconsistencias_verificado_em"] is not None
    assert processo["inconsistencias_comparacao_possivel"] == 1
    assert processo["inconsistencias_motivo_impossibilidade"] is None


def test_atualizar_status_deteccao_inconsistencias_persiste_nao_possivel(caminho_db):
    from app.db.repositorio import atualizar_status_deteccao_inconsistencias

    processo_id = criar_processo({"nome": "Processo teste"}, caminho_banco=caminho_db)

    atualizar_status_deteccao_inconsistencias(
        processo_id,
        comparacao_possivel=False,
        motivo_impossibilidade="motivo de teste",
        caminho_banco=caminho_db,
    )

    processo = obter_processo(processo_id, caminho_banco=caminho_db)
    assert processo["inconsistencias_verificado_em"] is not None
    assert processo["inconsistencias_comparacao_possivel"] == 0
    assert processo["inconsistencias_motivo_impossibilidade"] == "motivo de teste"


def test_atualizar_status_deteccao_inconsistencias_sobrescreve_rodada_anterior(caminho_db):
    from app.db.repositorio import atualizar_status_deteccao_inconsistencias

    processo_id = criar_processo({"nome": "Processo teste"}, caminho_banco=caminho_db)

    atualizar_status_deteccao_inconsistencias(
        processo_id, comparacao_possivel=False, motivo_impossibilidade="motivo antigo",
        caminho_banco=caminho_db,
    )
    atualizar_status_deteccao_inconsistencias(
        processo_id, comparacao_possivel=True, motivo_impossibilidade=None, caminho_banco=caminho_db
    )

    processo = obter_processo(processo_id, caminho_banco=caminho_db)
    assert processo["inconsistencias_comparacao_possivel"] == 1
    assert processo["inconsistencias_motivo_impossibilidade"] is None
