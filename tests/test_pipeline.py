# Testes do módulo de orquestração (app/pipeline.py). Usa um FAKE de
# extrair_checklist (mesmo padrão do Passo 3: substitui a função, não chama
# a API real) e um PDF de exemplo pequeno gerado em tmp_path.

import fitz
import pytest

from app import pipeline
from app.db.conexao import obter_conexao
from app.db.repositorio import criar_processo, obter_processo
from app.inconsistencias.limite_tr import DeteccaoLimiteTR
from app.pipeline import (
    ProcessoJaAnalisadoError,
    ProcessoNaoEncontradoError,
    ProcessoSemTextoExtraidoError,
    _mesclar_inconsistencias,
    detectar_inconsistencias_processo,
    processar_processo,
    responder_pergunta_processo,
)


def _criar_pdf_exemplo(caminho) -> None:
    documento = fitz.open()
    pagina = documento.new_page()
    pagina.insert_text(
        (72, 72), "Clausula 5. O licitante deve apresentar Certidao Negativa de Debitos."
    )
    documento.save(str(caminho))
    documento.close()


def _checklist_falso(texto_completo, contexto_processo):
    # Confere que processar_processo repassa texto e contexto de verdade.
    assert "Certidao Negativa de Debitos" in texto_completo
    assert set(contexto_processo.keys()) == {"orgao", "modalidade", "objeto"}
    return [
        {
            "categoria": "habilitacao_fiscal_social_trabalhista",
            "descricao": "Certidão Negativa de Débitos",
            "base_legal": None,
            "trecho": "O licitante deve apresentar Certidao Negativa de Debitos.",
            "obrigatorio_para": "todos",
        },
        {
            "categoria": "declaracoes_exigidas",
            "descricao": "Exigência inventada só pro teste, não existe no texto de exemplo",
            "base_legal": None,
            "trecho": "isso não existe no texto de exemplo nenhum",
            "obrigatorio_para": "todos",
        },
    ]


@pytest.fixture
def caminho_db(tmp_path) -> str:
    return str(tmp_path / "teste.db")


def test_processar_processo_fluxo_completo(tmp_path, caminho_db, monkeypatch):
    caminho_pdf = tmp_path / "edital.pdf"
    _criar_pdf_exemplo(caminho_pdf)
    processo_id = criar_processo({"nome": "Processo de teste"}, caminho_banco=caminho_db)

    monkeypatch.setattr(pipeline, "extrair_checklist", _checklist_falso)

    resumo = processar_processo(processo_id, [str(caminho_pdf)], caminho_banco=caminho_db)

    assert resumo == {
        "processo_id": processo_id,
        "arquivos_processados": 1,
        "total_exigencias": 2,
        "localizadas": 1,
        "inferidas": 1,
        "requisitos_por_item": 0,
    }

    processo = obter_processo(processo_id, caminho_banco=caminho_db)
    assert processo is not None
    assert len(processo["arquivos"]) == 1
    assert processo["arquivos"][0]["nome_arquivo"] == "edital.pdf"
    assert len(processo["exigencias"]) == 2

    localizada = next(e for e in processo["exigencias"] if e["confianca"] == "localizado")
    assert localizada["arquivo_origem_id"] == processo["arquivos"][0]["id"]
    assert localizada["pagina"] == "1"

    inferida = next(e for e in processo["exigencias"] if e["confianca"] == "inferido")
    assert inferida["arquivo_origem_id"] is None

    # Fase 2, Camada 0: texto bruto por página populado junto, no mesmo
    # passo — o PDF de exemplo tem 1 página só, então espera 1 linha.
    conexao = obter_conexao(caminho_db)
    linhas_texto = conexao.execute(
        "SELECT * FROM texto_pagina WHERE processo_id = ?", (processo_id,)
    ).fetchall()
    conexao.close()
    assert len(linhas_texto) == 1
    assert linhas_texto[0]["arquivo_id"] == processo["arquivos"][0]["id"]
    assert linhas_texto[0]["numero_pagina"] == 1
    assert "Certidao Negativa de Debitos" in linhas_texto[0]["texto"]


def test_processar_processo_inexistente_da_erro_claro(caminho_db):
    with pytest.raises(ProcessoNaoEncontradoError, match="999"):
        processar_processo(999, [], caminho_banco=caminho_db)


def test_processar_processo_ja_analisado_recusa_sem_forcar(tmp_path, caminho_db, monkeypatch):
    caminho_pdf = tmp_path / "edital.pdf"
    _criar_pdf_exemplo(caminho_pdf)
    processo_id = criar_processo({"nome": "Processo de teste"}, caminho_banco=caminho_db)
    monkeypatch.setattr(pipeline, "extrair_checklist", _checklist_falso)

    processar_processo(processo_id, [str(caminho_pdf)], caminho_banco=caminho_db)

    with pytest.raises(ProcessoJaAnalisadoError):
        processar_processo(processo_id, [str(caminho_pdf)], caminho_banco=caminho_db)


def test_processar_processo_com_forcar_reprocessa_sem_duplicar(
    tmp_path, caminho_db, monkeypatch
):
    caminho_pdf = tmp_path / "edital.pdf"
    _criar_pdf_exemplo(caminho_pdf)
    processo_id = criar_processo({"nome": "Processo de teste"}, caminho_banco=caminho_db)
    monkeypatch.setattr(pipeline, "extrair_checklist", _checklist_falso)

    processar_processo(processo_id, [str(caminho_pdf)], caminho_banco=caminho_db)
    resumo_2 = processar_processo(
        processo_id,
        [str(caminho_pdf)],
        forcar_reprocessamento=True,
        caminho_banco=caminho_db,
    )

    assert resumo_2["total_exigencias"] == 2

    processo = obter_processo(processo_id, caminho_banco=caminho_db)
    assert processo is not None
    # Não duplicou: continua 1 arquivo e 2 exigências, não 2 e 4.
    assert len(processo["arquivos"]) == 1
    assert len(processo["exigencias"]) == 2


def test_processar_processo_formato_nao_suportado_da_erro_claro(tmp_path, caminho_db):
    caminho_invalido = tmp_path / "arquivo.xyz"
    caminho_invalido.write_text("conteudo qualquer")
    processo_id = criar_processo({"nome": "Processo de teste"}, caminho_banco=caminho_db)

    with pytest.raises(ValueError, match="arquivo.xyz"):
        processar_processo(processo_id, [str(caminho_invalido)], caminho_banco=caminho_db)


# ---------- responder_pergunta_processo (Fase 2, Camada 1) ----------


def test_responder_pergunta_processo_monta_contexto_marcado_por_pagina(
    tmp_path, caminho_db, monkeypatch
):
    caminho_pdf = tmp_path / "edital.pdf"
    _criar_pdf_exemplo(caminho_pdf)
    processo_id = criar_processo({"nome": "Processo de teste"}, caminho_banco=caminho_db)
    monkeypatch.setattr(pipeline, "extrair_checklist", _checklist_falso)
    processar_processo(processo_id, [str(caminho_pdf)], caminho_banco=caminho_db)

    def ia_falsa(texto_completo_marcado, pergunta):
        # Confere que o texto chega marcado por página e que a pergunta
        # passa sem alteração — é só isso que responder_pergunta_processo
        # tem que fazer (montar o contexto, não interpretar a resposta).
        assert texto_completo_marcado.startswith("[PÁGINA 1]")
        assert "Certidao Negativa de Debitos" in texto_completo_marcado
        assert pergunta == "Qual certidão é exigida?"
        return {"encontrado": True, "resposta": "Certidão Negativa de Débitos.", "paginas": [1]}

    monkeypatch.setattr(pipeline, "_responder_pergunta_ia", ia_falsa)

    resultado = responder_pergunta_processo(
        processo_id, "Qual certidão é exigida?", caminho_banco=caminho_db
    )

    assert resultado == {
        "encontrado": True,
        "resposta": "Certidão Negativa de Débitos.",
        "paginas": [1],
    }


def test_responder_pergunta_processo_inexistente_da_erro_claro(caminho_db):
    with pytest.raises(ProcessoNaoEncontradoError, match="999"):
        responder_pergunta_processo(999, "pergunta qualquer", caminho_banco=caminho_db)


def test_responder_pergunta_processo_sem_analise_da_erro_claro(caminho_db):
    # Processo existe, mas nunca foi analisado — sem texto_pagina nenhum.
    processo_id = criar_processo({"nome": "Processo não analisado"}, caminho_banco=caminho_db)

    with pytest.raises(ProcessoSemTextoExtraidoError, match=str(processo_id)):
        responder_pergunta_processo(processo_id, "pergunta qualquer", caminho_banco=caminho_db)


# ---------- detectar_inconsistencias_processo (motor de inconsistências, Camada 1) ----------

_PAGINA_EDITAL_FALSA = {
    "numero_pagina": 5,
    "localizador": "página 5",
    "texto": "O prazo de entrega será de 30 dias.",
}
_PAGINA_TR_FALSA = {
    "numero_pagina": 20,
    "localizador": "página 20",
    "texto": "O prazo de entrega será de 45 dias.",
}


def _ia_falha_se_chamada(texto_edital_marcado, texto_tr_marcado):
    raise AssertionError("a IA não deveria ser chamada quando a Camada 0 não identificou o TR")


def test_detectar_inconsistencias_processo_tr_nao_identificado_nao_chama_ia(
    caminho_db, monkeypatch
):
    processo_id = criar_processo({"nome": "Processo teste"}, caminho_banco=caminho_db)

    deteccao_falsa = DeteccaoLimiteTR(
        identificado=False,
        metodo=None,
        marcador_encontrado=None,
        arquivo_tr_id=None,
        inicio_pagina=None,
        inicio_localizador=None,
        motivo_nao_identificado="motivo de teste: marcador não encontrado",
    )
    monkeypatch.setattr(pipeline, "identificar_blocos_edital_tr", lambda pid, caminho_banco=None: deteccao_falsa)
    monkeypatch.setattr(pipeline, "_detectar_inconsistencias_ia", _ia_falha_se_chamada)

    resultado = detectar_inconsistencias_processo(processo_id, caminho_banco=caminho_db)

    assert resultado == {
        "comparacao_possivel": False,
        "motivo_impossibilidade": "motivo de teste: marcador não encontrado",
        "inconsistencias": [],
    }

    # Fase 2, Camada 2: status persistido em "processo", pra UI diferenciar
    # "nunca verificado" de "verificado, não possível".
    processo = obter_processo(processo_id, caminho_banco=caminho_db)
    assert processo["inconsistencias_verificado_em"] is not None
    assert processo["inconsistencias_comparacao_possivel"] == 0
    assert processo["inconsistencias_motivo_impossibilidade"] == "motivo de teste: marcador não encontrado"


def test_detectar_inconsistencias_processo_bloco_edital_vazio_nao_chama_ia(
    caminho_db, monkeypatch
):
    processo_id = criar_processo({"nome": "Processo teste"}, caminho_banco=caminho_db)

    deteccao_falsa = DeteccaoLimiteTR(
        identificado=True,
        metodo="marcador_no_texto",
        marcador_encontrado="TERMO DE REFERÊNCIA",
        arquivo_tr_id=None,
        inicio_pagina=1,
        inicio_localizador="página 1",
        paginas_edital=[],
        paginas_tr=[_PAGINA_TR_FALSA],
    )
    monkeypatch.setattr(pipeline, "identificar_blocos_edital_tr", lambda pid, caminho_banco=None: deteccao_falsa)
    monkeypatch.setattr(pipeline, "_detectar_inconsistencias_ia", _ia_falha_se_chamada)

    resultado = detectar_inconsistencias_processo(processo_id, caminho_banco=caminho_db)

    assert resultado["comparacao_possivel"] is False
    assert "vazio" in resultado["motivo_impossibilidade"]
    assert resultado["inconsistencias"] == []


def test_detectar_inconsistencias_processo_encontra_e_salva(caminho_db, monkeypatch):
    processo_id = criar_processo({"nome": "Processo teste"}, caminho_banco=caminho_db)

    deteccao_falsa = DeteccaoLimiteTR(
        identificado=True,
        metodo="marcador_no_texto",
        marcador_encontrado="TERMO DE REFERÊNCIA",
        arquivo_tr_id=None,
        inicio_pagina=20,
        inicio_localizador="página 20",
        paginas_edital=[_PAGINA_EDITAL_FALSA],
        paginas_tr=[_PAGINA_TR_FALSA],
    )
    achado = {
        "tipo": "prazo",
        "descricao": "prazo de entrega diverge entre edital e TR",
        "trecho_edital": "O prazo de entrega será de 30 dias.",
        "pagina_edital": 5,
        "trecho_tr": "O prazo de entrega será de 45 dias.",
        "pagina_tr": 20,
    }

    def ia_falsa(texto_edital_marcado, texto_tr_marcado):
        assert "[PÁGINA 5]" in texto_edital_marcado
        assert "30 dias" in texto_edital_marcado
        assert "[PÁGINA 20]" in texto_tr_marcado
        assert "45 dias" in texto_tr_marcado
        return [achado]

    monkeypatch.setattr(pipeline, "identificar_blocos_edital_tr", lambda pid, caminho_banco=None: deteccao_falsa)
    monkeypatch.setattr(pipeline, "_detectar_inconsistencias_ia", ia_falsa)
    # Stub da medição de tokens (Camada 3, espaçamento) — valor pequeno, não
    # é o foco deste teste, só evita chamar a API de verdade.
    monkeypatch.setattr(pipeline, "_contar_tokens_comparacao_ia", lambda e, t: 1000)

    resultado = detectar_inconsistencias_processo(processo_id, caminho_banco=caminho_db)

    assert resultado == {
        "comparacao_possivel": True,
        "motivo_impossibilidade": None,
        "inconsistencias": [achado],
    }

    conexao = obter_conexao(caminho_db)
    linhas = conexao.execute(
        "SELECT * FROM inconsistencia WHERE processo_id = ?", (processo_id,)
    ).fetchall()
    conexao.close()
    assert len(linhas) == 1
    assert linhas[0]["tipo"] == "prazo"
    assert linhas[0]["pagina_edital"] == 5
    assert linhas[0]["pagina_tr"] == 20

    processo = obter_processo(processo_id, caminho_banco=caminho_db)
    assert processo["inconsistencias_verificado_em"] is not None
    assert processo["inconsistencias_comparacao_possivel"] == 1
    assert processo["inconsistencias_motivo_impossibilidade"] is None


def test_detectar_inconsistencias_processo_sem_achado_nao_salva_nada(caminho_db, monkeypatch):
    processo_id = criar_processo({"nome": "Processo teste"}, caminho_banco=caminho_db)

    deteccao_falsa = DeteccaoLimiteTR(
        identificado=True,
        metodo="marcador_no_texto",
        marcador_encontrado="TERMO DE REFERÊNCIA",
        arquivo_tr_id=None,
        inicio_pagina=20,
        inicio_localizador="página 20",
        paginas_edital=[_PAGINA_EDITAL_FALSA],
        paginas_tr=[_PAGINA_TR_FALSA],
    )
    monkeypatch.setattr(pipeline, "identificar_blocos_edital_tr", lambda pid, caminho_banco=None: deteccao_falsa)
    monkeypatch.setattr(pipeline, "_detectar_inconsistencias_ia", lambda e, t: [])
    monkeypatch.setattr(pipeline, "_contar_tokens_comparacao_ia", lambda e, t: 1000)

    resultado = detectar_inconsistencias_processo(processo_id, caminho_banco=caminho_db)

    assert resultado == {"comparacao_possivel": True, "motivo_impossibilidade": None, "inconsistencias": []}

    conexao = obter_conexao(caminho_db)
    total = conexao.execute(
        "SELECT COUNT(*) FROM inconsistencia WHERE processo_id = ?", (processo_id,)
    ).fetchone()[0]
    conexao.close()
    assert total == 0


def test_detectar_inconsistencias_processo_inexistente_da_erro_claro(caminho_db):
    with pytest.raises(ProcessoNaoEncontradoError, match="999"):
        detectar_inconsistencias_processo(999, caminho_banco=caminho_db)


def test_detectar_inconsistencias_processo_falha_da_ia_nao_atualiza_status(caminho_db, monkeypatch):
    # Se a chamada à IA falhar (rede, JSON malformado...), a rodada não
    # "completou" de verdade — o status de processo não deve ser
    # sobrescrito com um resultado que não aconteceu (ver docstring de
    # detectar_inconsistencias_processo).
    processo_id = criar_processo({"nome": "Processo teste"}, caminho_banco=caminho_db)

    deteccao_falsa = DeteccaoLimiteTR(
        identificado=True,
        metodo="marcador_no_texto",
        marcador_encontrado="TERMO DE REFERÊNCIA",
        arquivo_tr_id=None,
        inicio_pagina=20,
        inicio_localizador="página 20",
        paginas_edital=[_PAGINA_EDITAL_FALSA],
        paginas_tr=[_PAGINA_TR_FALSA],
    )

    def ia_que_falha(texto_edital_marcado, texto_tr_marcado):
        raise RuntimeError("falha simulada de rede")

    monkeypatch.setattr(pipeline, "identificar_blocos_edital_tr", lambda pid, caminho_banco=None: deteccao_falsa)
    monkeypatch.setattr(pipeline, "_detectar_inconsistencias_ia", ia_que_falha)
    monkeypatch.setattr(pipeline, "_contar_tokens_comparacao_ia", lambda e, t: 1000)

    with pytest.raises(RuntimeError, match="falha simulada de rede"):
        detectar_inconsistencias_processo(processo_id, caminho_banco=caminho_db)

    processo = obter_processo(processo_id, caminho_banco=caminho_db)
    assert processo["inconsistencias_verificado_em"] is None
    assert processo["inconsistencias_comparacao_possivel"] is None


def test_detectar_inconsistencias_processo_limpa_rodada_anterior_antes_de_salvar(
    caminho_db, monkeypatch
):
    from app.db.repositorio import salvar_inconsistencias

    processo_id = criar_processo({"nome": "Processo teste"}, caminho_banco=caminho_db)
    salvar_inconsistencias(
        processo_id,
        [
            {
                "tipo": "valor",
                "descricao": "achado de uma rodada antiga, deve sumir",
                "trecho_edital": "texto antigo edital",
                "pagina_edital": 1,
                "trecho_tr": "texto antigo tr",
                "pagina_tr": 2,
            }
        ],
        caminho_banco=caminho_db,
    )

    deteccao_falsa = DeteccaoLimiteTR(
        identificado=True,
        metodo="marcador_no_texto",
        marcador_encontrado="TERMO DE REFERÊNCIA",
        arquivo_tr_id=None,
        inicio_pagina=20,
        inicio_localizador="página 20",
        paginas_edital=[_PAGINA_EDITAL_FALSA],
        paginas_tr=[_PAGINA_TR_FALSA],
    )
    monkeypatch.setattr(pipeline, "identificar_blocos_edital_tr", lambda pid, caminho_banco=None: deteccao_falsa)
    monkeypatch.setattr(pipeline, "_detectar_inconsistencias_ia", lambda e, t: [])
    monkeypatch.setattr(pipeline, "_contar_tokens_comparacao_ia", lambda e, t: 1000)

    detectar_inconsistencias_processo(processo_id, caminho_banco=caminho_db)

    conexao = obter_conexao(caminho_db)
    total = conexao.execute(
        "SELECT COUNT(*) FROM inconsistencia WHERE processo_id = ?", (processo_id,)
    ).fetchone()[0]
    conexao.close()
    assert total == 0  # a antiga foi limpa, e a rodada nova não achou nada


# ---------- _mesclar_inconsistencias (motor de inconsistências, Camada 3) ----------


def _achado(**overrides) -> dict:
    base = {
        "tipo": "prazo",
        "descricao": "prazo diverge",
        "trecho_edital": "O prazo de entrega será de 30 dias.",
        "pagina_edital": 5,
        "trecho_tr": "O prazo de entrega será de 45 dias.",
        "pagina_tr": 20,
    }
    base.update(overrides)
    return base


def test_mesclar_inconsistencias_deduplica_achado_identico_nas_duas_execucoes():
    achado_1 = _achado()
    achado_2 = _achado()  # dict diferente, mas mesmo conteúdo

    mesclado = _mesclar_inconsistencias([achado_1], [achado_2])

    assert len(mesclado) == 1


def test_mesclar_inconsistencias_deduplica_ignorando_acento_caixa_e_pontuacao():
    achado_1 = _achado(trecho_edital="O prazo de entrega será de 30 dias.")
    achado_2 = _achado(trecho_edital="  o PRAZO DE ENTREGA sera de 30 dias  ")

    mesclado = _mesclar_inconsistencias([achado_1], [achado_2])

    assert len(mesclado) == 1


def test_mesclar_inconsistencias_mantem_achado_que_aparece_em_uma_so_execucao():
    # União, não interseção: achado só na execução 1 e achado só na
    # execução 2 entram os dois — não é descartado por "só apareceu uma
    # vez" (isso reintroduziria o risco que a execução dupla mitiga).
    achado_so_na_1 = _achado(tipo="prazo", descricao="só apareceu na primeira rodada")
    achado_so_na_2 = _achado(tipo="valor", descricao="só apareceu na segunda rodada")

    mesclado = _mesclar_inconsistencias([achado_so_na_1], [achado_so_na_2])

    assert len(mesclado) == 2
    assert achado_so_na_1 in mesclado
    assert achado_so_na_2 in mesclado


def test_mesclar_inconsistencias_nao_confunde_tipos_diferentes_com_mesmo_trecho():
    achado_prazo = _achado(tipo="prazo")
    achado_valor = _achado(tipo="valor")  # mesmos trechos, tipo diferente

    mesclado = _mesclar_inconsistencias([achado_prazo], [achado_valor])

    assert len(mesclado) == 2


def test_mesclar_inconsistencias_duas_listas_vazias_devolve_lista_vazia():
    assert _mesclar_inconsistencias([], []) == []


# ---------- Camada 3: execução dupla real dentro de detectar_inconsistencias_processo ----------


def test_detectar_inconsistencias_processo_chama_ia_duas_vezes(caminho_db, monkeypatch):
    processo_id = criar_processo({"nome": "Processo teste"}, caminho_banco=caminho_db)

    deteccao_falsa = DeteccaoLimiteTR(
        identificado=True,
        metodo="marcador_no_texto",
        marcador_encontrado="TERMO DE REFERÊNCIA",
        arquivo_tr_id=None,
        inicio_pagina=20,
        inicio_localizador="página 20",
        paginas_edital=[_PAGINA_EDITAL_FALSA],
        paginas_tr=[_PAGINA_TR_FALSA],
    )
    chamadas = []

    def ia_falsa(texto_edital_marcado, texto_tr_marcado):
        # Cada chamada "acha" um trecho DIFERENTE (não só descrição
        # diferente) — simula o não-determinismo real (Frutal: rodadas
        # diferentes citaram trechos diferentes sobre o mesmo tema).
        chamadas.append(1)
        return [_achado(trecho_edital=f"Trecho do edital, achado {len(chamadas)}.")]

    monkeypatch.setattr(pipeline, "identificar_blocos_edital_tr", lambda pid, caminho_banco=None: deteccao_falsa)
    monkeypatch.setattr(pipeline, "_detectar_inconsistencias_ia", ia_falsa)
    monkeypatch.setattr(pipeline, "_contar_tokens_comparacao_ia", lambda e, t: 1000)

    resultado = detectar_inconsistencias_processo(processo_id, caminho_banco=caminho_db)

    assert len(chamadas) == 2
    # As duas chamadas devolveram achados com trechos DIFERENTES — o merge
    # não descarta nenhum por ter aparecido numa chamada só (união, não
    # interseção).
    assert len(resultado["inconsistencias"]) == 2


# ---------- Camada 3 (espaçamento): _tempo_de_espera_entre_execucoes ----------


def test_tempo_de_espera_zero_para_contexto_pequeno():
    # Paulínia real: 54_278 tokens por chamada (x2 = 108_556) — bem abaixo
    # do limite seguro (220_000), não deve esperar nada.
    assert pipeline._tempo_de_espera_entre_execucoes(54_278) == 0


def test_tempo_de_espera_zero_para_contexto_medio_que_ja_passou_sem_espera():
    # Ouroeste real: 103_820 tokens por chamada (x2 = 207_640) — este é o
    # caso que JÁ completou sem esperar no golden test da Camada 3; o
    # limiar não pode regredir esse caso pra "espera desnecessária".
    assert pipeline._tempo_de_espera_entre_execucoes(103_820) == 0


def test_tempo_de_espera_60s_para_contexto_grande_que_bateu_429():
    # Frutal real: 216_689 tokens por chamada (x2 = 433_378) — este é o
    # caso que bateu 429 de verdade no golden test (ver
    # camada3_golden_log.txt); o limiar precisa cobrir exatamente este caso.
    assert pipeline._tempo_de_espera_entre_execucoes(216_689) == pipeline._ESPERA_ENTRE_EXECUCOES_SEGUNDOS


def test_tempo_de_espera_respeita_a_margem_de_seguranca_no_limiar_exato():
    # No limiar exato (soma == limite seguro), ainda não espera — só espera
    # quando a soma ULTRAPASSA o limite seguro (> , não >=).
    limite_seguro = pipeline.LIMITE_TOKENS_POR_MINUTO - pipeline._MARGEM_SEGURANCA_TOKENS_POR_MINUTO
    tokens_no_limiar = limite_seguro // 2
    assert pipeline._tempo_de_espera_entre_execucoes(tokens_no_limiar) == 0
    assert pipeline._tempo_de_espera_entre_execucoes(tokens_no_limiar + 1) == pipeline._ESPERA_ENTRE_EXECUCOES_SEGUNDOS


# ---------- Camada 3 (espaçamento): integração com detectar_inconsistencias_processo ----------


def test_detectar_inconsistencias_processo_nao_espera_para_contexto_pequeno(
    caminho_db, monkeypatch
):
    processo_id = criar_processo({"nome": "Processo teste"}, caminho_banco=caminho_db)

    deteccao_falsa = DeteccaoLimiteTR(
        identificado=True,
        metodo="marcador_no_texto",
        marcador_encontrado="TERMO DE REFERÊNCIA",
        arquivo_tr_id=None,
        inicio_pagina=20,
        inicio_localizador="página 20",
        paginas_edital=[_PAGINA_EDITAL_FALSA],
        paginas_tr=[_PAGINA_TR_FALSA],
    )

    def sleep_que_falha_se_chamado(segundos):
        raise AssertionError("não deveria esperar entre as chamadas pra um contexto pequeno")

    monkeypatch.setattr(pipeline, "identificar_blocos_edital_tr", lambda pid, caminho_banco=None: deteccao_falsa)
    monkeypatch.setattr(pipeline, "_detectar_inconsistencias_ia", lambda e, t: [])
    monkeypatch.setattr(pipeline, "_contar_tokens_comparacao_ia", lambda e, t: 54_278)  # Paulínia real
    monkeypatch.setattr(pipeline.time, "sleep", sleep_que_falha_se_chamado)

    # Não pode levantar AssertionError — se levantar, quer dizer que esperou
    # à toa pra um edital pequeno.
    detectar_inconsistencias_processo(processo_id, caminho_banco=caminho_db)


def test_detectar_inconsistencias_processo_espera_para_contexto_grande(
    caminho_db, monkeypatch
):
    processo_id = criar_processo({"nome": "Processo teste"}, caminho_banco=caminho_db)

    deteccao_falsa = DeteccaoLimiteTR(
        identificado=True,
        metodo="marcador_no_texto",
        marcador_encontrado="TERMO DE REFERÊNCIA",
        arquivo_tr_id=None,
        inicio_pagina=20,
        inicio_localizador="página 20",
        paginas_edital=[_PAGINA_EDITAL_FALSA],
        paginas_tr=[_PAGINA_TR_FALSA],
    )
    esperas_registradas = []

    monkeypatch.setattr(pipeline, "identificar_blocos_edital_tr", lambda pid, caminho_banco=None: deteccao_falsa)
    monkeypatch.setattr(pipeline, "_detectar_inconsistencias_ia", lambda e, t: [])
    monkeypatch.setattr(pipeline, "_contar_tokens_comparacao_ia", lambda e, t: 216_689)  # Frutal real
    monkeypatch.setattr(pipeline.time, "sleep", lambda segundos: esperas_registradas.append(segundos))

    detectar_inconsistencias_processo(processo_id, caminho_banco=caminho_db)

    assert esperas_registradas == [pipeline._ESPERA_ENTRE_EXECUCOES_SEGUNDOS]


# ---------- Camada 3: wire na análise automática (processar_processo) ----------


def test_processar_processo_roda_deteccao_de_inconsistencias_automaticamente(
    tmp_path, caminho_db, monkeypatch
):
    caminho_pdf = tmp_path / "edital.pdf"
    _criar_pdf_exemplo(caminho_pdf)
    processo_id = criar_processo({"nome": "Processo de teste"}, caminho_banco=caminho_db)
    monkeypatch.setattr(pipeline, "extrair_checklist", _checklist_falso)

    deteccao_falsa = DeteccaoLimiteTR(
        identificado=True,
        metodo="marcador_no_texto",
        marcador_encontrado="TERMO DE REFERÊNCIA",
        arquivo_tr_id=None,
        inicio_pagina=20,
        inicio_localizador="página 20",
        paginas_edital=[_PAGINA_EDITAL_FALSA],
        paginas_tr=[_PAGINA_TR_FALSA],
    )
    monkeypatch.setattr(pipeline, "identificar_blocos_edital_tr", lambda pid, caminho_banco=None: deteccao_falsa)
    monkeypatch.setattr(pipeline, "_detectar_inconsistencias_ia", lambda e, t: [_achado()])
    monkeypatch.setattr(pipeline, "_contar_tokens_comparacao_ia", lambda e, t: 1000)

    processar_processo(processo_id, [str(caminho_pdf)], caminho_banco=caminho_db)

    from app.db.repositorio import obter_inconsistencias

    processo = obter_processo(processo_id, caminho_banco=caminho_db)
    assert processo["inconsistencias_verificado_em"] is not None
    assert processo["inconsistencias_comparacao_possivel"] == 1
    assert len(obter_inconsistencias(processo_id, caminho_banco=caminho_db)) == 1


def test_processar_processo_nao_falha_se_deteccao_de_inconsistencias_falhar(
    tmp_path, caminho_db, monkeypatch
):
    # A entrega principal (checklist) não pode ser derrubada por uma falha
    # numa camada secundária (rede, IA fora do ar...) — o processo fica
    # "ainda não verificado" e dá pra rodar à mão depois pela tela.
    caminho_pdf = tmp_path / "edital.pdf"
    _criar_pdf_exemplo(caminho_pdf)
    processo_id = criar_processo({"nome": "Processo de teste"}, caminho_banco=caminho_db)
    monkeypatch.setattr(pipeline, "extrair_checklist", _checklist_falso)

    def identificar_que_falha(pid, caminho_banco=None):
        raise RuntimeError("falha simulada na Camada 0 do motor de inconsistências")

    monkeypatch.setattr(pipeline, "identificar_blocos_edital_tr", identificar_que_falha)

    resumo = processar_processo(processo_id, [str(caminho_pdf)], caminho_banco=caminho_db)

    # A análise principal completou normalmente, sem levantar exceção.
    assert resumo["total_exigencias"] == 2

    processo = obter_processo(processo_id, caminho_banco=caminho_db)
    assert len(processo["exigencias"]) == 2  # checklist foi salvo normalmente
    assert processo["inconsistencias_verificado_em"] is None  # detecção não completou
