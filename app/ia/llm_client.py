# Camada de IA trocável: o resto do sistema (rotas, validador, etc.) chama só
# as funções deste módulo, nunca fala direto com o SDK de um provedor. Hoje só
# o Gemini está implementado; trocar de provedor no futuro é acrescentar outro
# "if" abaixo, sem mexer em quem chama.

from __future__ import annotations

import json
import logging
from typing import Any

from google import genai
from google.genai import types

from app.config import GEMINI_API_KEY, GEMINI_MODEL, LLM_PROVIDER

logger = logging.getLogger(__name__)


# Categorias fixas da Lei 14.133/2021 (planejamento-nexlicit-engine.md, seção
# 3). A IA só encaixa exigências dentro delas, nunca cria uma categoria nova
# — isso é o que trava a alucinação de estrutura. Estas chaves também são o
# valor que vai para a coluna "categoria" de cada exigência no banco (Passo 5).
CATEGORIAS_CHECKLIST = (
    "habilitacao_juridica",
    "habilitacao_fiscal_social_trabalhista",
    "qualificacao_economico_financeira",
    "qualificacao_tecnica",
    "declaracoes_exigidas",
    "requisitos_proposta",
)

# Schema de cada exigência individual. Usa o dialeto JSON Schema padrão
# (response_json_schema), não o subconjunto OpenAPI (response_schema) — o
# padrão aceita "enum" e "required", que travam obrigatorio_para nos dois
# valores válidos e exigem os quatro campos sempre presentes.
_SCHEMA_EXIGENCIA = {
    "type": "object",
    "properties": {
        "descricao": {"type": "string"},
        # base_legal pode não ser identificável no texto; anyOf com "null" é
        # a forma de nulidade suportada por este dialeto (não "nullable").
        "base_legal": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "trecho": {"type": "string"},
        "obrigatorio_para": {"type": "string", "enum": ["todos", "vencedor"]},
    },
    "required": ["descricao", "base_legal", "trecho", "obrigatorio_para"],
    "additionalProperties": False,
}

# Schema do JSON completo: um objeto com uma chave por categoria fixa, cada
# uma um array de exigências. Gerado a partir de CATEGORIAS_CHECKLIST pra não
# ter a lista de categorias duplicada (e desalinhável) em dois lugares.
SCHEMA_RESPOSTA_CHECKLIST = {
    "type": "object",
    "properties": {
        categoria: {"type": "array", "items": _SCHEMA_EXIGENCIA}
        for categoria in CATEGORIAS_CHECKLIST
    },
    "required": list(CATEGORIAS_CHECKLIST),
    "additionalProperties": False,
}

# Confirmado ao vivo em 2026-07 via cliente.models.get(model=GEMINI_MODEL)
# .output_token_limit: o teto de saída do gemini-3.6-flash é 65536 tokens.
# Usamos o teto inteiro como max_output_tokens — um edital de 50-60 páginas
# pode gerar uma lista longa de exigências, e a maior folga possível é usar
# o próprio limite do modelo.
MAX_OUTPUT_TOKENS = 65536

# Temperatura baixa porque esta tarefa é extração fiel (copiar trecho
# literal), não geração criativa. 0.1 em vez de 0.0 exato: alguns modelos
# entram em loop de repetição em temperatura estritamente zero.
TEMPERATURA_EXTRACAO = 0.1

# Nível de "thinking" baixo, não desligado: o gemini-3.6-flash tem thinking
# ligado por padrão em "medium", e os tokens de raciocínio saem do mesmo
# orçamento de max_output_tokens (confirmado: não são um orçamento à parte,
# ver discussão registrada na conversa deste passo). "low" reduz esse consumo
# em vez de "minimal", porque este sistema depende de achar exigência
# escondida no meio do texto — desligar thinking de vez arrisca perder
# justamente esse tipo de achado.
THINKING_LEVEL_EXTRACAO = types.ThinkingLevel.LOW


class ProvedorNaoSuportadoError(Exception):
    """LLM_PROVIDER no .env aponta para um provedor que ainda não existe aqui."""


class RespostaIAError(Exception):
    """A IA não devolveu algo que dá para transformar no checklist esperado."""


def _montar_prompt_sistema() -> str:
    categorias_formatadas = "\n".join(f"- {c}" for c in CATEGORIAS_CHECKLIST)
    return f"""Você lê editais de licitação brasileiros regidos pela Lei \
14.133/2021 e extrai um checklist de exigências de habilitação, declarações \
e requisitos da proposta.

Categorias fixas (use exatamente estas chaves, uma por categoria, mesmo que \
fique com lista vazia):
{categorias_formatadas}

Cuidado especial entre "habilitacao_juridica" e \
"habilitacao_fiscal_social_trabalhista" — são as duas categorias mais fáceis \
de confundir, principalmente porque muitos editais colocam os dois tipos de \
exigência juntos, sob um único título (às vezes até rotulado errado como \
"habilitação jurídica" quando na verdade é fiscal). Classifique pelo que o \
documento exigido PROVA, não pelo título da seção do edital:
- "habilitacao_juridica": prova de que a empresa EXISTE e está regularmente \
constituída — contrato social, estatuto, ato constitutivo e suas alterações, \
documento do representante legal, e também a prova de inscrição no CNPJ em \
si (o cadastro que dá existência formal à empresa perante a Receita).
- "habilitacao_fiscal_social_trabalhista": CERTIDÕES DE REGULARIDADE ou de \
AUSÊNCIA DE DÉBITO junto a um órgão — Receita/Fazenda Federal, INSS, FGTS, \
Justiça do Trabalho (CNDT), e certidões correlatas de mesma natureza (como a \
de improbidade do CNJ). Se o documento existe para provar que a empresa está \
"em dia" com alguma obrigação perante um órgão, é fiscal/social/trabalhista, \
mesmo que apareça na mesma lista ou sob o mesmo título que a prova de CNPJ.

Para cada exigência encontrada, preencha:
- "descricao": o que é exigido, em uma frase objetiva.
- "base_legal": artigo ou dispositivo legal, se for possível identificar. Se \
não der para identificar, use null — não invente um artigo.
- "trecho": cópia LITERAL do trecho do texto fornecido que fundamenta essa \
exigência. Copie exatamente como está escrito, sem parafrasear, sem corrigir \
ortografia ou pontuação. Isso é obrigatório: um validador automático vai \
conferir depois se esse trecho existe, palavra por palavra, no documento \
original — se você reescrever em vez de copiar, a exigência não vai ser \
confirmada.
- "obrigatorio_para": "todos" se a exigência vale para todo licitante na fase \
de habilitação, ou "vencedor" se vale só para quem vencer (ex.: documento \
pedido só na hora de assinar o contrato).

Responda em JSON estrito e SOMENTE o JSON — sem markdown, sem \
```json, sem texto antes ou depois. O formato é um objeto com uma chave para \
cada categoria acima, e cada uma é um array de exigências no formato \
descrito. Se não encontrar nada de uma categoria, retorne um array vazio para \
ela. Nunca invente uma exigência que não está no texto."""


def _montar_prompt_usuario(texto_completo: str, contexto_processo: dict[str, Any]) -> str:
    # contexto_processo é opcional: dados como órgão, modalidade e objeto do
    # processo (ver tabela "processo" no planejamento), quando quem chama já
    # os tiver. Servem só de contexto extra pro prompt — a extração funciona
    # mesmo com o dicionário vazio.
    partes = []
    contexto_preenchido = {
        chave: valor for chave, valor in contexto_processo.items() if valor
    }
    if contexto_preenchido:
        linhas = "\n".join(f"{chave}: {valor}" for chave, valor in contexto_preenchido.items())
        partes.append(f"Contexto do processo licitatório:\n{linhas}")
    partes.append(f"Texto do documento:\n{texto_completo}")
    return "\n\n".join(partes)


def extrair_checklist(
    texto_completo: str, contexto_processo: dict[str, Any]
) -> list[dict[str, Any]]:
    """Extrai o checklist de exigências de um texto já extraído (PDF/DOCX).

    Devolve uma lista plana de exigências, cada uma um dict com "categoria",
    "descricao", "base_legal", "trecho" e "obrigatorio_para".

    O que este módulo NÃO faz:
    - Não confere se "trecho" existe de verdade no texto-fonte — isso é o
      validador do Passo 4, um módulo separado.
    - Não preenche o arquivo de origem de cada exigência: quem chama esta
      função sabe de qual arquivo veio `texto_completo` (o extrator do Passo
      2 devolve `nome_arquivo`) e deve anexar isso a cada item do retorno
      antes de salvar no banco.
    """
    if LLM_PROVIDER != "gemini":
        raise ProvedorNaoSuportadoError(
            f"provedor de IA '{LLM_PROVIDER}' ainda não está implementado "
            "(por enquanto só 'gemini' é suportado)"
        )

    resposta_bruta = _chamar_gemini(texto_completo, contexto_processo)
    return _parsear_resposta(resposta_bruta)


def _chamar_gemini(texto_completo: str, contexto_processo: dict[str, Any]) -> str:
    # IMPORTANTE: nunca colocar GEMINI_API_KEY em mensagem de erro, log ou
    # print — só ela é passada, sem interpolação em string nenhuma.
    if not GEMINI_API_KEY:
        raise RespostaIAError(
            "GEMINI_API_KEY não está definida no .env — gere uma chave no "
            "Google AI Studio e preencha o .env antes de usar a IA"
        )
    if not GEMINI_MODEL:
        raise RespostaIAError(
            "GEMINI_MODEL não está definido no .env — confirme o nome do "
            "modelo (ex.: gemini-3.6-flash) e preencha o .env"
        )

    cliente = genai.Client(api_key=GEMINI_API_KEY)

    try:
        resposta = cliente.models.generate_content(
            model=GEMINI_MODEL,
            contents=_montar_prompt_usuario(texto_completo, contexto_processo),
            config=types.GenerateContentConfig(
                system_instruction=_montar_prompt_sistema(),
                response_mime_type="application/json",
                response_json_schema=SCHEMA_RESPOSTA_CHECKLIST,
                temperature=TEMPERATURA_EXTRACAO,
                max_output_tokens=MAX_OUTPUT_TOKENS,
                thinking_config=types.ThinkingConfig(
                    thinking_level=THINKING_LEVEL_EXTRACAO
                ),
            ),
        )
    except Exception as erro:
        # A mensagem de erro do SDK do Google não inclui a chave (ela viaja
        # só em cabeçalho HTTP, não aparece na resposta), então é seguro
        # repassar. Mesmo assim, nunca adicionamos a chave manualmente aqui.
        raise RespostaIAError(f"falha ao chamar a API do Gemini: {erro}") from erro

    if resposta.text is None:
        raise RespostaIAError(
            f"a IA não devolveu texto na resposta (finish_reason: {resposta.candidates[0].finish_reason if resposta.candidates else 'desconhecido'})"
        )

    return resposta.text


def _parsear_resposta(resposta_bruta: str) -> list[dict[str, Any]]:
    try:
        dados = json.loads(resposta_bruta)
    except json.JSONDecodeError as erro:
        logger.error("resposta da IA não é JSON válido: %r", resposta_bruta)
        raise RespostaIAError(
            "a IA não devolveu um JSON válido — o conteúdo bruto foi registrado no log"
        ) from erro

    if not isinstance(dados, dict):
        raise RespostaIAError(
            "a IA devolveu um JSON válido, mas não é um objeto com uma chave por categoria"
        )

    exigencias: list[dict[str, Any]] = []
    for categoria in CATEGORIAS_CHECKLIST:
        itens_categoria = dados.get(categoria, [])
        if not isinstance(itens_categoria, list):
            raise RespostaIAError(
                f"categoria '{categoria}' veio no JSON da IA, mas não é uma lista"
            )
        for item in itens_categoria:
            exigencias.append({**item, "categoria": categoria})

    return exigencias


def responder_pergunta(texto_completo: str, pergunta: str) -> Any:
    """Fase 2 (RAG por long-context): responde uma pergunta em linguagem
    natural com base no texto do processo, citando trecho e página.

    Só a assinatura por enquanto — implementação entra na Fase 2, depois do
    MVP (ver planejamento-nexlicit-engine.md, seção 7).
    """
    raise NotImplementedError(
        f"responder_pergunta é da Fase 2, ainda não implementado "
        f"(pergunta recebida: {pergunta!r}, texto com {len(texto_completo)} caracteres)"
    )
