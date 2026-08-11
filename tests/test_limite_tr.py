# Testes do motor de inconsistências edital-vs-TR, Camada 0
# (app/inconsistencias/limite_tr.py) — sem cobertura nenhuma até agora (só
# foi verificado com scripts avulsos contra os 7 editais reais). Trava aqui
# o comportamento calibrado nessa rodada, pra não regredir silenciosamente
# — se esta camada errar o limite, a comparação da Camada 1 fica sem valor.
#
# Duas camadas de teste:
# - _parece_titulo direto: rápido, preciso, cobre as 3 regras de rejeição +
#   aceitação, sem precisar de banco.
# - identificar_blocos_edital_tr ponta a ponta: grava texto_pagina sintético
#   num banco de teste (mesmo padrão de tmp_path/caminho_banco do resto da
#   suíte) e confere o resultado via a função pública de verdade.

import pytest

from app.db.repositorio import criar_arquivo, criar_processo, salvar_texto_paginas
from app.inconsistencias.limite_tr import _parece_titulo, identificar_blocos_edital_tr


# ---------- _parece_titulo (unitário, sem banco) ----------


def test_parece_titulo_aceita_linha_isolada_com_acento():
    # Regressão do bug de acento: a checagem de resíduo precisa normalizar
    # antes de comparar, senão "REFERÊNCIA" (acentuado) nunca bate contra o
    # padrão de remoção (escrito sem acento) e a linha mais limpa possível
    # era rejeitada por engano.
    assert _parece_titulo("TERMO DE REFERÊNCIA", "") is True


def test_parece_titulo_aceita_anexo_mais_titulo_na_mesma_linha():
    assert _parece_titulo("ANEXO I – TERMO DE REFERÊNCIA.", "Página 63/86") is True


def test_parece_titulo_aceita_quando_anexo_esta_na_linha_anterior():
    assert _parece_titulo("TERMO DE REFERÊNCIA", "ANEXO I") is True


def test_parece_titulo_rejeita_citacao_dentro_de_frase():
    linha = "1.2. A licitação será dividida em itens, conforme tabela constante do Termo de Referência,"
    assert _parece_titulo(linha, "quantidades e exigências estabelecidas neste Edital e seus anexos.") is False


def test_parece_titulo_rejeita_linha_comecando_com_numeracao_de_clausula():
    # "14.16.1. ANEXO I - Termo de Referência;" — item de lista de anexos do
    # próprio edital, não título da seção.
    assert _parece_titulo("14.16.1. ANEXO I - Termo de Referência;", "") is False


def test_parece_titulo_rejeita_quando_linha_anterior_e_numero_de_lista_solto():
    # Tabela de anexos "achatada" pela extração: número do item vira linha
    # própria antes do título ("12.11.1" sozinho) — mesmo a linha do
    # marcador ficando limpa, isso é item de lista, não título de seção.
    assert _parece_titulo("ANEXO I - Termo de Referência", "12.11.1") is False


def test_parece_titulo_rejeita_frase_cortada_terminando_no_marcador():
    # Regressão do bug do resíduo só-pontuação: "Termo de Referência."
    # sozinho passaria no filtro de letra (o ponto final não é letra), mas
    # a linha anterior é uma frase de cláusula de verdade, comprida — não é
    # título.
    linha_antes = (
        "com os requisitos estabelecidos neste Edital, contenham vícios "
        "insanáveis ou não apresentem as especificações técnicas exigidas no"
    )
    assert _parece_titulo("Termo de Referência.", linha_antes) is False


def test_parece_titulo_aceita_linha_anterior_curta_de_titulo_composto():
    # Paulínia real: "ANEXO I – ESPECIFICAÇÕES DO OBJETO / TERMO DE
    # REFERÊNCIA" (56 caracteres) — mais comprida que um rótulo solto, mas
    # ainda é outra linha de título, não uma frase de cláusula.
    linha_antes = "ANEXO I – ESPECIFICAÇÕES DO OBJETO / TERMO DE REFERÊNCIA"
    assert len(linha_antes) <= 60
    assert _parece_titulo("ANEXO I -TERMO DE REFERÊNCIA", linha_antes) is True


# ---------- identificar_blocos_edital_tr (ponta a ponta, com banco) ----------


@pytest.fixture
def caminho_db(tmp_path) -> str:
    return str(tmp_path / "teste.db")


def _paginas_pdf(textos: list[str]) -> list[dict]:
    """Uma página por item de `textos`, numeradas a partir de 1 — formato
    que salvar_texto_paginas espera."""
    return [
        {"numero_pagina": indice + 1, "localizador": f"página {indice + 1}", "texto": texto}
        for indice, texto in enumerate(textos)
    ]


def test_caso_b_identifica_titulo_isolado_no_meio_do_arquivo(caminho_db):
    processo_id = criar_processo({"nome": "Teste"}, caminho_banco=caminho_db)
    arquivo_id = criar_arquivo(
        processo_id, {"nome_arquivo": "edital.pdf", "tipo": "pdf"}, caminho_banco=caminho_db
    )
    salvar_texto_paginas(
        processo_id,
        arquivo_id,
        _paginas_pdf(
            [
                "1. DO OBJETO\nConforme disposto no Termo de Referência, anexo a este edital.",
                "ANEXO I\nTERMO DE REFERÊNCIA\n1. OBJETO\nFornecimento de bens diversos.",
            ]
        ),
        caminho_banco=caminho_db,
    )

    resultado = identificar_blocos_edital_tr(processo_id, caminho_banco=caminho_db)

    assert resultado.identificado is True
    assert resultado.metodo == "marcador_no_texto"
    assert resultado.marcador_encontrado == "TERMO DE REFERÊNCIA"
    assert resultado.inicio_pagina == 2
    assert len(resultado.paginas_edital) == 1
    assert len(resultado.paginas_tr) == 1
    assert resultado.paginas_edital[0]["numero_pagina"] == 1
    assert resultado.paginas_tr[0]["numero_pagina"] == 2


def test_caso_b_nao_identifica_quando_so_ha_citacao_de_passagem(caminho_db):
    processo_id = criar_processo({"nome": "Teste"}, caminho_banco=caminho_db)
    arquivo_id = criar_arquivo(
        processo_id, {"nome_arquivo": "edital.pdf", "tipo": "pdf"}, caminho_banco=caminho_db
    )
    salvar_texto_paginas(
        processo_id,
        arquivo_id,
        _paginas_pdf(
            [
                "1. DO OBJETO\nConforme disposto no Termo de Referência, anexo a este edital.",
                "2. DAS CONDIÇÕES\nO licitante deverá atender ao Termo de Referência integralmente.",
            ]
        ),
        caminho_banco=caminho_db,
    )

    resultado = identificar_blocos_edital_tr(processo_id, caminho_banco=caminho_db)

    assert resultado.identificado is False
    assert resultado.paginas_edital == []
    assert resultado.paginas_tr == []
    assert "não achei" in (resultado.motivo_nao_identificado or "")


def test_caso_b_usa_anexo_i_como_fallback_quando_termo_de_referencia_nao_aparece(caminho_db):
    processo_id = criar_processo({"nome": "Teste"}, caminho_banco=caminho_db)
    arquivo_id = criar_arquivo(
        processo_id, {"nome_arquivo": "edital.pdf", "tipo": "pdf"}, caminho_banco=caminho_db
    )
    salvar_texto_paginas(
        processo_id,
        arquivo_id,
        _paginas_pdf(
            [
                "1. DO OBJETO\nAquisição de bens diversos, conforme condições deste edital.",
                "ANEXO I\n1. ESPECIFICAÇÕES\nDescrição detalhada dos itens.",
            ]
        ),
        caminho_banco=caminho_db,
    )

    resultado = identificar_blocos_edital_tr(processo_id, caminho_banco=caminho_db)

    assert resultado.identificado is True
    assert resultado.marcador_encontrado == "ANEXO I"
    assert resultado.inicio_pagina == 2


def test_caso_a_identifica_arquivo_separado_como_tr(caminho_db):
    processo_id = criar_processo({"nome": "Teste"}, caminho_banco=caminho_db)
    arquivo_edital_id = criar_arquivo(
        processo_id, {"nome_arquivo": "edital.pdf", "tipo": "pdf"}, caminho_banco=caminho_db
    )
    arquivo_tr_id = criar_arquivo(
        processo_id, {"nome_arquivo": "termo_referencia.pdf", "tipo": "pdf"}, caminho_banco=caminho_db
    )
    salvar_texto_paginas(
        processo_id,
        arquivo_edital_id,
        _paginas_pdf(["1. DO OBJETO\nConforme condições deste edital e seus anexos."]),
        caminho_banco=caminho_db,
    )
    salvar_texto_paginas(
        processo_id,
        arquivo_tr_id,
        _paginas_pdf(["TERMO DE REFERÊNCIA\n1. OBJETO\nFornecimento de bens diversos."]),
        caminho_banco=caminho_db,
    )

    resultado = identificar_blocos_edital_tr(processo_id, caminho_banco=caminho_db)

    assert resultado.identificado is True
    assert resultado.metodo == "arquivo_separado"
    assert resultado.arquivo_tr_id == arquivo_tr_id
    assert len(resultado.paginas_edital) == 1
    assert len(resultado.paginas_tr) == 1


def test_caso_a_nao_identifica_quando_nenhum_arquivo_tem_marcador(caminho_db):
    processo_id = criar_processo({"nome": "Teste"}, caminho_banco=caminho_db)
    arquivo_1_id = criar_arquivo(
        processo_id, {"nome_arquivo": "edital.pdf", "tipo": "pdf"}, caminho_banco=caminho_db
    )
    arquivo_2_id = criar_arquivo(
        processo_id, {"nome_arquivo": "declaracoes.pdf", "tipo": "pdf"}, caminho_banco=caminho_db
    )
    salvar_texto_paginas(
        processo_id, arquivo_1_id,
        _paginas_pdf(["1. DO OBJETO\nConforme condições deste edital."]),
        caminho_banco=caminho_db,
    )
    salvar_texto_paginas(
        processo_id, arquivo_2_id,
        _paginas_pdf(["MODELO DE DECLARAÇÃO\nDeclaro para os devidos fins..."]),
        caminho_banco=caminho_db,
    )

    resultado = identificar_blocos_edital_tr(processo_id, caminho_banco=caminho_db)

    assert resultado.identificado is False
    assert "nenhum dos arquivos" in (resultado.motivo_nao_identificado or "")


def test_identificar_blocos_devolve_motivo_quando_processo_sem_texto(caminho_db):
    processo_id = criar_processo({"nome": "Teste"}, caminho_banco=caminho_db)

    resultado = identificar_blocos_edital_tr(processo_id, caminho_banco=caminho_db)

    assert resultado.identificado is False
    assert "sem texto extraído" in (resultado.motivo_nao_identificado or "")
