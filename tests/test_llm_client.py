# Testes do módulo app/ia/llm_client.py.
#
# A maioria dos testes usa um "fake" da chamada ao Gemini (substitui
# _chamar_gemini) para não gastar quota nem depender de internet — são os que
# rodam no CI e no dia a dia com "pytest".
#
# Um teste é marcado como manual e chama a API de verdade: só roda se a
# variável de ambiente RODAR_TESTE_GEMINI_REAL estiver definida, e precisa da
# GEMINI_API_KEY real no .env. Para rodar:
#   RODAR_TESTE_GEMINI_REAL=1 pytest tests/test_llm_client.py -k manual -s

import json
import os

import pytest

from app.ia import llm_client
from app.ia.llm_client import (
    ProvedorNaoSuportadoError,
    RespostaIAError,
    extrair_checklist,
)

RESPOSTA_JSON_VALIDA = json.dumps(
    {
        "habilitacao_juridica": [
            {
                "descricao": "Apresentar contrato social e alterações",
                "base_legal": "art. 66",
                "trecho": "O licitante deve apresentar contrato social e suas alterações.",
                "obrigatorio_para": "todos",
            }
        ],
        "habilitacao_fiscal_social_trabalhista": [
            {
                "descricao": "Certidão Negativa de Débitos Federais",
                "base_legal": None,
                "trecho": "Certidão Negativa de Débitos Federais (CND) em nome do licitante.",
                "obrigatorio_para": "todos",
            }
        ],
        "qualificacao_economico_financeira": [],
        "qualificacao_tecnica": [],
        "declaracoes_exigidas": [
            {
                "descricao": "Declaração de inexistência de fato impeditivo",
                "base_legal": None,
                "trecho": "Declara, para fins do disposto no edital, a inexistência de fato impeditivo.",
                "obrigatorio_para": "todos",
            }
        ],
        "requisitos_proposta": [
            {
                "descricao": "Prazo de validade da proposta de 60 dias",
                "base_legal": None,
                "trecho": "O licitante vencedor deverá manter a proposta válida por 60 dias.",
                "obrigatorio_para": "vencedor",
            }
        ],
    }
)


def test_extrair_checklist_processa_resposta_valida_da_ia(monkeypatch):
    def gemini_falso(texto_completo, contexto_processo):
        # Confere que extrair_checklist repassa os argumentos sem alterar.
        assert texto_completo == "texto qualquer do edital"
        assert contexto_processo == {}
        return RESPOSTA_JSON_VALIDA

    monkeypatch.setattr(llm_client, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(llm_client, "_chamar_gemini", gemini_falso)

    resultado = extrair_checklist("texto qualquer do edital", {})

    # A resposta tinha 4 exigências no total, espalhadas em 3 categorias.
    assert len(resultado) == 4

    categorias_encontradas = {item["categoria"] for item in resultado}
    assert categorias_encontradas == {
        "habilitacao_juridica",
        "habilitacao_fiscal_social_trabalhista",
        "declaracoes_exigidas",
        "requisitos_proposta",
    }

    exigencia_fiscal = next(
        item for item in resultado if item["categoria"] == "habilitacao_fiscal_social_trabalhista"
    )
    assert exigencia_fiscal["descricao"] == "Certidão Negativa de Débitos Federais"
    assert "CND" in exigencia_fiscal["trecho"]
    assert exigencia_fiscal["obrigatorio_para"] == "todos"


def test_extrair_checklist_rejeita_provedor_nao_suportado(monkeypatch):
    monkeypatch.setattr(llm_client, "LLM_PROVIDER", "claude")

    with pytest.raises(ProvedorNaoSuportadoError, match="claude"):
        extrair_checklist("texto qualquer", {})


def test_extrair_checklist_nao_quebra_com_json_malformado(monkeypatch):
    def gemini_falso(texto_completo, contexto_processo):
        assert texto_completo == "texto qualquer"
        assert contexto_processo == {}
        return "isso não é um JSON {"

    monkeypatch.setattr(llm_client, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(llm_client, "_chamar_gemini", gemini_falso)

    with pytest.raises(RespostaIAError):
        extrair_checklist("texto qualquer", {})


def test_extrair_checklist_rejeita_json_que_nao_e_objeto(monkeypatch):
    def gemini_falso(texto_completo, contexto_processo):
        assert texto_completo == "texto qualquer"
        assert contexto_processo == {}
        return json.dumps(["lista", "solta"])

    monkeypatch.setattr(llm_client, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(llm_client, "_chamar_gemini", gemini_falso)

    with pytest.raises(RespostaIAError):
        extrair_checklist("texto qualquer", {})


def test_chamar_gemini_recusa_sem_api_key(monkeypatch):
    # Confere que falta de chave dá um erro claro ANTES de tentar rede —
    # e que a mensagem de erro não expõe a chave (aqui ela nem existe).
    monkeypatch.setattr(llm_client, "GEMINI_API_KEY", "")

    with pytest.raises(RespostaIAError, match="GEMINI_API_KEY"):
        llm_client._chamar_gemini("texto qualquer", {})


@pytest.mark.skipif(
    not os.getenv("RODAR_TESTE_GEMINI_REAL"),
    reason=(
        "teste manual: chama a API do Gemini de verdade e gasta quota. Só "
        "roda com RODAR_TESTE_GEMINI_REAL=1 definido e GEMINI_API_KEY real "
        "no .env."
    ),
)
def test_extrair_checklist_chama_api_real_manualmente():
    texto_exemplo = (
        "Cláusula 7ª. Para fins de habilitação, o licitante deverá apresentar "
        "Certidão Negativa de Débitos Federais (CND) e declaração de "
        "inexistência de fato impeditivo, sob pena de inabilitação."
    )

    resultado = extrair_checklist(texto_exemplo, {"objeto": "aquisição de bens de exemplo"})

    print("\nResultado da chamada real ao Gemini:")
    print(json.dumps(resultado, indent=2, ensure_ascii=False))

    assert isinstance(resultado, list)
