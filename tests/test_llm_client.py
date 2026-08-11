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
from types import SimpleNamespace

import pytest

from app.ia import llm_client
from app.ia.llm_client import (
    ConfiguracaoAusenteError,
    ContextoGrandeDemaisError,
    ProvedorNaoSuportadoError,
    RespostaIAError,
    detectar_inconsistencias,
    extrair_checklist,
    responder_pergunta,
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

    with pytest.raises(ConfiguracaoAusenteError, match="GEMINI_API_KEY"):
        llm_client._chamar_gemini("texto qualquer", {})


# ---------- responder_pergunta (Fase 2, Camada 1) ----------

RESPOSTA_PERGUNTA_JSON_ENCONTRADA = json.dumps(
    {
        "encontrado": True,
        "resposta": "O prazo de entrega é de 10 dias úteis, contados do recebimento da ordem de compra.",
        "paginas": [7],
    }
)

RESPOSTA_PERGUNTA_JSON_NAO_ENCONTRADA = json.dumps(
    {"encontrado": False, "resposta": "Não localizei essa informação no texto fornecido.", "paginas": []}
)


def test_responder_pergunta_processa_resposta_encontrada(monkeypatch):
    def gemini_falso(texto_completo_marcado, pergunta):
        assert "[PÁGINA 7]" in texto_completo_marcado
        assert pergunta == "Qual o prazo de entrega?"
        return RESPOSTA_PERGUNTA_JSON_ENCONTRADA

    monkeypatch.setattr(llm_client, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(llm_client, "_chamar_gemini_pergunta", gemini_falso)

    resultado = responder_pergunta("[PÁGINA 7]\ntexto do edital aqui", "Qual o prazo de entrega?")

    assert resultado["encontrado"] is True
    assert resultado["paginas"] == [7]
    assert "10 dias úteis" in resultado["resposta"]


def test_responder_pergunta_processa_resposta_nao_encontrada(monkeypatch):
    def gemini_falso(texto_completo_marcado, pergunta):
        return RESPOSTA_PERGUNTA_JSON_NAO_ENCONTRADA

    monkeypatch.setattr(llm_client, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(llm_client, "_chamar_gemini_pergunta", gemini_falso)

    resultado = responder_pergunta("[PÁGINA 1]\ntexto", "Pergunta sem resposta no texto")

    assert resultado["encontrado"] is False
    assert resultado["paginas"] == []


def test_responder_pergunta_rejeita_provedor_nao_suportado(monkeypatch):
    monkeypatch.setattr(llm_client, "LLM_PROVIDER", "claude")

    with pytest.raises(ProvedorNaoSuportadoError, match="claude"):
        responder_pergunta("[PÁGINA 1]\ntexto", "pergunta qualquer")


def test_responder_pergunta_nao_quebra_com_json_malformado(monkeypatch):
    monkeypatch.setattr(llm_client, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(llm_client, "_chamar_gemini_pergunta", lambda t, p: "isso não é um JSON {")

    with pytest.raises(RespostaIAError):
        responder_pergunta("[PÁGINA 1]\ntexto", "pergunta qualquer")


def _cliente_falso_count_tokens(total_tokens):
    """Um "cliente Gemini" mínimo, só com o pedaço que
    _verificar_tamanho_do_contexto usa (cliente.models.count_tokens) —
    dá pra testar a lógica de comparação com o limite sem precisar de rede
    nem de um genai.Client de verdade."""
    return SimpleNamespace(
        models=SimpleNamespace(
            count_tokens=lambda model, contents: SimpleNamespace(total_tokens=total_tokens)
        )
    )


def test_verificar_tamanho_do_contexto_aceita_dentro_do_limite():
    cliente_falso = _cliente_falso_count_tokens(total_tokens=1000)
    # Não levanta nada — é o próprio teste (se levantasse, o pytest já
    # reportaria a falha sozinho).
    llm_client._verificar_tamanho_do_contexto(cliente_falso, "modelo-fake", "texto pequeno")


def test_verificar_tamanho_do_contexto_recusa_acima_do_limite():
    limite_efetivo = llm_client.LIMITE_TOKENS_ENTRADA - llm_client.MARGEM_SEGURANCA_TOKENS_CONTEXTO
    cliente_falso = _cliente_falso_count_tokens(total_tokens=limite_efetivo + 1)

    with pytest.raises(ContextoGrandeDemaisError, match="grande demais"):
        llm_client._verificar_tamanho_do_contexto(cliente_falso, "modelo-fake", "texto grande")


def test_chamar_gemini_pergunta_recusa_sem_api_key(monkeypatch):
    monkeypatch.setattr(llm_client, "GEMINI_API_KEY", "")

    with pytest.raises(ConfiguracaoAusenteError, match="GEMINI_API_KEY"):
        llm_client._chamar_gemini_pergunta("[PÁGINA 1]\ntexto", "pergunta qualquer")


# ---------- detectar_inconsistencias (motor de inconsistências, Camada 1) ----------

RESPOSTA_INCONSISTENCIAS_JSON_COM_ACHADO = json.dumps(
    {
        "inconsistencias": [
            {
                "tipo": "prazo",
                "descricao": "prazo de entrega diverge entre edital e TR",
                "trecho_edital": "O prazo de entrega será de 30 dias.",
                "pagina_edital": 5,
                "trecho_tr": "O prazo de entrega será de 45 dias.",
                "pagina_tr": 20,
            }
        ]
    }
)

RESPOSTA_INCONSISTENCIAS_JSON_VAZIA = json.dumps({"inconsistencias": []})


def test_detectar_inconsistencias_processa_resposta_com_achado(monkeypatch):
    def gemini_falso(texto_edital_marcado, texto_tr_marcado):
        assert "[PÁGINA 5]" in texto_edital_marcado
        assert "[PÁGINA 20]" in texto_tr_marcado
        return RESPOSTA_INCONSISTENCIAS_JSON_COM_ACHADO

    monkeypatch.setattr(llm_client, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(llm_client, "_chamar_gemini_inconsistencias", gemini_falso)

    resultado = detectar_inconsistencias("[PÁGINA 5]\ntexto do edital", "[PÁGINA 20]\ntexto do TR")

    assert len(resultado) == 1
    assert resultado[0]["tipo"] == "prazo"
    assert resultado[0]["pagina_edital"] == 5
    assert resultado[0]["pagina_tr"] == 20


def test_detectar_inconsistencias_processa_lista_vazia(monkeypatch):
    monkeypatch.setattr(llm_client, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(
        llm_client, "_chamar_gemini_inconsistencias", lambda e, t: RESPOSTA_INCONSISTENCIAS_JSON_VAZIA
    )

    resultado = detectar_inconsistencias("[PÁGINA 1]\nedital", "[PÁGINA 2]\ntr")

    assert resultado == []


def test_detectar_inconsistencias_rejeita_provedor_nao_suportado(monkeypatch):
    monkeypatch.setattr(llm_client, "LLM_PROVIDER", "claude")

    with pytest.raises(ProvedorNaoSuportadoError, match="claude"):
        detectar_inconsistencias("[PÁGINA 1]\nedital", "[PÁGINA 2]\ntr")


def test_detectar_inconsistencias_nao_quebra_com_json_malformado(monkeypatch):
    monkeypatch.setattr(llm_client, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(
        llm_client, "_chamar_gemini_inconsistencias", lambda e, t: "isso não é um JSON {"
    )

    with pytest.raises(RespostaIAError):
        detectar_inconsistencias("[PÁGINA 1]\nedital", "[PÁGINA 2]\ntr")


def test_detectar_inconsistencias_rejeita_json_sem_chave_esperada(monkeypatch):
    monkeypatch.setattr(llm_client, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(
        llm_client, "_chamar_gemini_inconsistencias", lambda e, t: json.dumps({"outra_coisa": []})
    )

    with pytest.raises(RespostaIAError):
        detectar_inconsistencias("[PÁGINA 1]\nedital", "[PÁGINA 2]\ntr")


def test_chamar_gemini_inconsistencias_recusa_sem_api_key(monkeypatch):
    monkeypatch.setattr(llm_client, "GEMINI_API_KEY", "")

    with pytest.raises(ConfiguracaoAusenteError, match="GEMINI_API_KEY"):
        llm_client._chamar_gemini_inconsistencias("[PÁGINA 1]\nedital", "[PÁGINA 2]\ntr")


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
