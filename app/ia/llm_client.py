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
        # Preenchido só quando esta exigência é UMA ENTRE VÁRIAS alternativas
        # pra satisfazer a mesma exigência de fundo (ver instrução no prompt
        # de sistema). NULL na imensa maioria dos casos.
        "grupo_hipoteses": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    },
    "required": ["descricao", "base_legal", "trecho", "obrigatorio_para", "grupo_hipoteses"],
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

# Schema da Camada 1 (Fase 2): resposta de pergunta em linguagem natural
# sobre o edital, por long-context. "encontrado" separado de "resposta" de
# propósito — dá pra IA dizer "não achei" de um jeito que o código consegue
# checar (`if not dados["encontrado"]`) sem precisar interpretar texto livre
# procurando frase de negação.
_SCHEMA_RESPOSTA_PERGUNTA = {
    "type": "object",
    "properties": {
        "encontrado": {"type": "boolean"},
        "resposta": {"type": "string"},
        # Páginas de onde a IA tirou a informação — vazio quando
        # "encontrado" é false (nada foi usado, então não há página a citar).
        "paginas": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["encontrado", "resposta", "paginas"],
    "additionalProperties": False,
}

# Motor de inconsistências edital-vs-TR (Fase 2, segunda metade), Camada 1.
# Cinco tipos fixos, sem hierarquia entre eles — a IA classifica cada
# achado numa dessas categorias, todas buscadas na mesma chamada.
# "administrativo" entrou depois do golden test real contra o Frutal:
# nome divergente do servidor responsável pela fiscalização do contrato
# entre edital e TR é um achado real, mas não é "especificacao_tecnica"
# (não é sobre o objeto) — sem uma quinta categoria, a IA forçava esse tipo
# de achado numa das quatro originais.
TIPOS_INCONSISTENCIA = ("quantidade", "valor", "prazo", "especificacao_tecnica", "administrativo")

# "trecho_edital"/"trecho_tr" são OBRIGATÓRIOS os dois (não anyOf com null,
# diferente de "base_legal" no schema do checklist) — de propósito: o
# guarda-corpo anti-alucinação deste recurso é justamente exigir uma citação
# literal DE CADA LADO pra uma inconsistência contar. Se a IA não consegue
# citar os dois, a instrução no prompt de sistema é pra não incluir o item
# na lista, não pra incluir com um campo vazio/nulo.
_SCHEMA_INCONSISTENCIA = {
    "type": "object",
    "properties": {
        "tipo": {"type": "string", "enum": list(TIPOS_INCONSISTENCIA)},
        "descricao": {"type": "string"},
        "trecho_edital": {"type": "string"},
        "pagina_edital": {"type": "integer"},
        "trecho_tr": {"type": "string"},
        "pagina_tr": {"type": "integer"},
    },
    "required": ["tipo", "descricao", "trecho_edital", "pagina_edital", "trecho_tr", "pagina_tr"],
    "additionalProperties": False,
}

_SCHEMA_RESPOSTA_INCONSISTENCIAS = {
    "type": "object",
    "properties": {
        "inconsistencias": {"type": "array", "items": _SCHEMA_INCONSISTENCIA},
    },
    "required": ["inconsistencias"],
    "additionalProperties": False,
}

# Confirmado ao vivo em 2026-07 via cliente.models.get(model=GEMINI_MODEL)
# .output_token_limit: o teto de saída do gemini-3.6-flash é 65536 tokens.
# Usamos o teto inteiro como max_output_tokens — um edital de 50-60 páginas
# pode gerar uma lista longa de exigências, e a maior folga possível é usar
# o próprio limite do modelo.
MAX_OUTPUT_TOKENS = 65536

# Confirmado ao vivo em 2026-08 via cliente.models.get(model=GEMINI_MODEL)
# .input_token_limit: o teto de ENTRADA do gemini-3.6-flash é 1.048.576
# tokens (~1M) — é o número real da API nesse momento, não uma suposição de
# memória (o valor muda entre versões de modelo, por isso a checagem é
# sempre ao vivo via count_tokens, não hardcoded contra o texto de cada
# chamada). Margem de segurança cobre o prompt de sistema (pequeno, mas o
# count_tokens do modo Developer API não aceita contar system_instruction
# separado — ver _verificar_tamanho_do_contexto) e evita colar exatamente no
# limite. Nome sem "PERGUNTA": Fase 2 tem duas chamadas de long-context que
# compartilham essa checagem (Q&A e o motor de inconsistências edital-vs-TR),
# não é mais específico de perguntar.
LIMITE_TOKENS_ENTRADA = 1_048_576
MARGEM_SEGURANCA_TOKENS_CONTEXTO = 5_000

# Confirmado ao vivo em 2026-08 via erro 429 real (Fase 2, Camada 3, golden
# test do Frutal): "generate_content_free_tier_input_token_count...
# limit: 250000". DIFERENTE de LIMITE_TOKENS_ENTRADA (tamanho máximo de UMA
# chamada, ~1M) — este é quantos tokens de ENTRADA cabem em TODAS as
# chamadas dentro de 60s, no free tier. Usado pelo pipeline (Camada 3) pra
# decidir se vale espaçar as duas chamadas da execução dupla do motor de
# inconsistências — ver app.pipeline.detectar_inconsistencias_processo.
LIMITE_TOKENS_POR_MINUTO = 250_000

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


class ConfiguracaoAusenteError(Exception):
    """GEMINI_API_KEY ou GEMINI_MODEL não está definido no .env.

    Separado de RespostaIAError de propósito: isso é um problema de
    configuração do servidor (ninguém preencheu o .env), não uma falha da
    API do Gemini em si — a chamada nem chega a ser tentada.
    """


class ContextoGrandeDemaisError(Exception):
    """O texto do processo (todas as páginas juntas) excede o limite de
    contexto de entrada do modelo — não dá pra responder a pergunta de uma
    vez só.

    Detectado ANTES de chamar generate_content, via count_tokens (contagem
    real, não estimativa por caractere) — de propósito: truncar o texto
    silenciosamente seria pior do que recusar. A resposta pareceria
    completa, mas teria sido calculada sem parte do edital, sem nenhum
    aviso disso pra quem perguntou.
    """


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

Regra fixa para catálogos, folders e fichas técnicas do PRODUTO ofertado: \
sempre classifique em "requisitos_proposta", nunca em "qualificacao_tecnica" \
— mesmo que o edital liste essa exigência dentro de uma seção chamada \
"qualificação técnica" ou correlata. O motivo é o mesmo da regra do CNPJ \
acima: classifique pelo que o documento comprova, não pelo título da seção. \
Catálogo/ficha técnica comprova as características do PRODUTO oferecido na \
proposta, não a capacidade técnica da empresa (que é o que \
"qualificacao_tecnica" mede — atestados de capacidade técnica, registros \
profissionais, licenças da empresa, etc.).

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
- "grupo_hipoteses": null na grande maioria das vezes. Preencha só quando \
esta exigência é UMA ENTRE VÁRIAS formas ALTERNATIVAS de satisfazer a MESMA \
exigência de fundo — o licitante vai apresentar UMA delas, nunca todas ao \
mesmo tempo. O caso mais comum é o documento constitutivo da empresa \
variando pelo tipo societário ("registro comercial" para empresário \
individual OU "contrato social" para sociedade empresária OU "ato \
constitutivo no Registro Civil de PJ" para sociedade civil OU "decreto de \
autorização" para empresa estrangeira OU documentos de cooperativa — o \
mesmo licitante nunca apresenta duas dessas). Outro exemplo: uma certidão \
OU uma declaração de isenção equivalente, quando o edital oferece as duas \
como opção. Quando usar, escreva um título curto que identifique a \
exigência de fundo (ex.: "Documento constitutivo da empresa") e repita \
EXATAMENTE o mesmo texto em "grupo_hipoteses" em cada uma das exigências \
alternativas dessa mesma família — é assim que o sistema identifica quais \
exigências pertencem ao mesmo grupo. NÃO use isso para exigências que são \
cumulativas (todas exigidas ao mesmo tempo, não alternativas) — CNPJ e \
contrato social, por exemplo, são sempre exigidos JUNTOS, nunca alternativos \
entre si, então nenhum dos dois leva "grupo_hipoteses".

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
        raise ConfiguracaoAusenteError(
            "GEMINI_API_KEY não está definida no .env — gere uma chave no "
            "Google AI Studio e preencha o .env antes de usar a IA"
        )
    if not GEMINI_MODEL:
        raise ConfiguracaoAusenteError(
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
    """Faz o parsing do JSON da IA e ACHATA o formato aninhado por categoria
    (o que o response_json_schema exige: um dict com 6 chaves fixas, cada
    uma uma lista de exigências sem "categoria" embutida) numa lista plana
    de exigências, cada uma com "categoria" preenchida a partir da chave de
    origem — é o formato que validar_exigencias (Passo 4) e salvar_exigencias
    (Passo 5) esperam."""
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


def _montar_prompt_sistema_pergunta() -> str:
    return """Você responde perguntas sobre um edital de licitação brasileiro \
regido pela Lei 14.133/2021, com base SOMENTE no texto fornecido — um \
recorte do documento real, marcado por página no formato "[PÁGINA N]" antes \
do texto de cada página.

Regras que não podem ser quebradas:
- Responda SÓ com informação que está literalmente no texto fornecido. \
Nunca complete com conhecimento geral sobre licitações, nunca infira o que \
"normalmente" um edital diria — se não está escrito no texto, não existe \
pra você.
- "encontrado": true só quando a resposta está de fato no texto fornecido. \
Se não encontrar, "encontrado": false e "resposta" deve dizer explicitamente \
que a informação não foi localizada no texto fornecido — nunca invente um \
número, prazo, valor ou condição pra preencher a lacuna.
- "paginas": a lista das páginas (os números que aparecem em "[PÁGINA N]") \
de onde tirou a informação usada na resposta. Obrigatório preencher com \
pelo menos uma página quando "encontrado" é true; lista vazia quando é \
false. Uma informação sem página que a sustente não pode ser dada como \
encontrada — sem citação, sem uso da informação, mesmo princípio de nunca \
afirmar algo que não dá pra apontar de onde veio.
- "resposta": objetiva, em português, respondendo diretamente à pergunta.

Responda em JSON estrito e SOMENTE o JSON — sem markdown, sem \
```json, sem texto antes ou depois."""


def _montar_prompt_usuario_pergunta(texto_completo_marcado: str, pergunta: str) -> str:
    return f"Texto do edital (marcado por página):\n{texto_completo_marcado}\n\nPergunta: {pergunta}"


def _verificar_tamanho_do_contexto(
    cliente: genai.Client, modelo: str, texto_prompt_usuario: str
) -> None:
    """Checagem de tamanho compartilhada pelas duas chamadas de long-context
    da Fase 2 (Q&A e o motor de inconsistências edital-vs-TR) — recebe o
    prompt de usuário JÁ MONTADO (cada chamador monta o seu, com
    _montar_prompt_usuario_pergunta ou _montar_prompt_usuario_inconsistencias),
    não decide aqui o que vai dentro do texto.

    "modelo" vem por parâmetro (em vez de ler GEMINI_MODEL direto do
    módulo) só pra o verificador de tipos enxergar que já foi conferido
    como não-None por quem chama — a checagem em si (`if not GEMINI_MODEL`)
    só vale dentro da função onde é feita.
    """
    resposta_contagem = cliente.models.count_tokens(model=modelo, contents=texto_prompt_usuario)
    total_tokens = resposta_contagem.total_tokens
    limite_efetivo = LIMITE_TOKENS_ENTRADA - MARGEM_SEGURANCA_TOKENS_CONTEXTO

    if total_tokens is not None and total_tokens > limite_efetivo:
        raise ContextoGrandeDemaisError(
            f"o texto deste processo tem aproximadamente {total_tokens} tokens, "
            f"acima do limite de {limite_efetivo} tokens que o modelo "
            f"{modelo} suporta numa chamada só (contagem feita ao vivo "
            "via count_tokens, não estimada). Este edital é grande demais "
            "para processar de uma vez — não dá pra truncar o texto sem "
            "arriscar perder a parte relevante."
        )


def responder_pergunta(texto_completo_marcado: str, pergunta: str) -> dict[str, Any]:
    """Fase 2, Camada 1 (RAG por long-context, sem embeddings): responde uma
    pergunta em linguagem natural com base no texto do processo, citando a
    página de onde tirou a informação.

    `texto_completo_marcado` precisa já vir com marcação de página no
    formato "[PÁGINA N]" antes do texto de cada página — quem monta isso é
    app.pipeline.responder_pergunta_processo, a partir da tabela
    texto_pagina (Camada 0). Este módulo não lê banco, só recebe texto
    pronto e devolve a resposta da IA.

    Devolve um dict {"encontrado": bool, "resposta": str, "paginas":
    list[int]}. "encontrado" é False quando a informação não está no texto
    — a IA é instruída a nunca inventar quando isso acontece (ver
    _montar_prompt_sistema_pergunta).

    Levanta ContextoGrandeDemaisError ANTES de chamar a API se o texto
    ultrapassar o limite de entrada do modelo (checado ao vivo via
    count_tokens — nunca trunca silenciosamente).
    """
    if LLM_PROVIDER != "gemini":
        raise ProvedorNaoSuportadoError(
            f"provedor de IA '{LLM_PROVIDER}' ainda não está implementado "
            "(por enquanto só 'gemini' é suportado)"
        )

    resposta_bruta = _chamar_gemini_pergunta(texto_completo_marcado, pergunta)
    return _parsear_resposta_pergunta(resposta_bruta)


def _chamar_gemini_pergunta(texto_completo_marcado: str, pergunta: str) -> str:
    # IMPORTANTE: nunca colocar GEMINI_API_KEY em mensagem de erro, log ou
    # print — só ela é passada, sem interpolação em string nenhuma.
    if not GEMINI_API_KEY:
        raise ConfiguracaoAusenteError(
            "GEMINI_API_KEY não está definida no .env — gere uma chave no "
            "Google AI Studio e preencha o .env antes de usar a IA"
        )
    if not GEMINI_MODEL:
        raise ConfiguracaoAusenteError(
            "GEMINI_MODEL não está definido no .env — confirme o nome do "
            "modelo (ex.: gemini-3.6-flash) e preencha o .env"
        )

    cliente = genai.Client(api_key=GEMINI_API_KEY)

    _verificar_tamanho_do_contexto(
        cliente, GEMINI_MODEL, _montar_prompt_usuario_pergunta(texto_completo_marcado, pergunta)
    )

    try:
        resposta = cliente.models.generate_content(
            model=GEMINI_MODEL,
            contents=_montar_prompt_usuario_pergunta(texto_completo_marcado, pergunta),
            config=types.GenerateContentConfig(
                system_instruction=_montar_prompt_sistema_pergunta(),
                response_mime_type="application/json",
                response_json_schema=_SCHEMA_RESPOSTA_PERGUNTA,
                # Mesma configuração de modelo do resto do Engine (extração
                # do checklist): esta tarefa também é "responder fiel ao
                # texto fornecido", não geração criativa — o mesmo raciocínio
                # de temperatura baixa e thinking "low" (não desligado, pra
                # não perder informação escondida no meio do texto) se aplica
                # igual aqui.
                temperature=TEMPERATURA_EXTRACAO,
                max_output_tokens=MAX_OUTPUT_TOKENS,
                thinking_config=types.ThinkingConfig(
                    thinking_level=THINKING_LEVEL_EXTRACAO
                ),
            ),
        )
    except Exception as erro:
        raise RespostaIAError(f"falha ao chamar a API do Gemini: {erro}") from erro

    if resposta.text is None:
        raise RespostaIAError(
            f"a IA não devolveu texto na resposta (finish_reason: {resposta.candidates[0].finish_reason if resposta.candidates else 'desconhecido'})"
        )

    return resposta.text


def _parsear_resposta_pergunta(resposta_bruta: str) -> dict[str, Any]:
    try:
        dados = json.loads(resposta_bruta)
    except json.JSONDecodeError as erro:
        logger.error("resposta da IA (pergunta) não é JSON válido: %r", resposta_bruta)
        raise RespostaIAError(
            "a IA não devolveu um JSON válido para a pergunta — o conteúdo bruto foi registrado no log"
        ) from erro

    if not isinstance(dados, dict):
        raise RespostaIAError(
            "a IA devolveu um JSON válido, mas não é um objeto com os campos esperados"
        )

    return dados


# ---------- Motor de inconsistências edital-vs-TR (Fase 2), Camada 1 ----------
#
# Reaproveita a mesma infraestrutura de long-context da Camada 1 do Q&A
# (texto marcado por página, checagem de tamanho via count_tokens, mesma
# configuração de modelo) — só muda o prompt e o schema, que agora comparam
# DOIS blocos de texto (edital e TR) em vez de responder uma pergunta sobre
# um só.
#
# Guarda-corpo reforçado (rodada 2, depois do golden test real contra os 5
# processos identificados): "duas citações literais" sozinho não bastava —
# achados suspeitos passavam porque um dos dois lados era só uma REMISSÃO
# ("conforme disciplinado no Termo de Referência", sem repetir o valor) ou
# um campo de formulário sem marcação legível ("Sim   Não"), nunca um FATO
# concreto que pudesse contradizer o outro lado de verdade. Agora o prompt
# exige que cada trecho afirme um fato verificável por si só.
#
# Decisão de escopo: quando só um lado tem fato concreto e o outro só
# remete a ele (ex.: uma contradição INTERNA do próprio TR, não entre
# edital e TR), o prompt instrui a NÃO reportar — não é forçado no formato
# de duas citações. A tabela "inconsistencia" (trecho_edital/trecho_tr
# ambos NOT NULL) foi desenhada especificamente para comparação
# edital-vs-TR; detectar contradição interna de um documento só seria uma
# funcionalidade nova, com formato de dado próprio — fica de fora desta
# reforço de propósito, não foi pedido nesta rodada.


def _montar_prompt_sistema_inconsistencias() -> str:
    tipos_formatados = "\n".join(f"- {tipo}" for tipo in TIPOS_INCONSISTENCIA)
    return f"""Você compara o CORPO DO EDITAL com o TERMO DE REFERÊNCIA (TR) \
de uma licitação brasileira regida pela Lei 14.133/2021. Os dois vêm como \
texto marcado por página, no formato "[PÁGINA N]" antes do texto de cada \
página.

Sua tarefa: encontrar CONTRADIÇÕES reais entre o que o edital diz e o que \
o TR diz sobre o MESMO assunto, em cinco tipos, todos buscados de uma vez, \
sem hierarquia entre eles:
{tipos_formatados}

- "quantidade": número de itens/unidades/lotes divergente entre edital e TR.
- "valor": valor monetário divergente.
- "prazo": prazo (entrega, vigência, validade da proposta, execução...) \
divergente.
- "especificacao_tecnica": característica ou especificação do OBJETO \
(o que está sendo licitado) divergente.
- "administrativo": dado de gestão do processo divergente, não sobre o \
objeto — ex.: nome do servidor responsável pela fiscalização do contrato, \
setor/departamento gestor, número de processo administrativo.

Regras que não podem ser quebradas:
- Só reporte uma inconsistência quando conseguir citar um TRECHO LITERAL do \
edital E um TRECHO LITERAL do TR que realmente se contradigam sobre o MESMO \
ponto. Copie os dois trechos exatamente como estão escritos no texto \
fornecido, sem parafrasear, sem corrigir ortografia ou pontuação — um \
validador automático vai conferir depois se cada trecho existe, palavra por \
palavra, no bloco de origem correspondente.
- Sem os dois trechos literais (um do edital, um do TR) que realmente \
conflitem, NÃO inclua o item na lista — não é uma inconsistência só porque \
"parece" haver diferença; é preciso ter as duas citações reais que se \
contradizem.
- Cada um dos dois trechos precisa AFIRMAR um FATO CONCRETO E VERIFICÁVEL \
por si só — um número, data, prazo, quantidade, valor ou especificação \
técnica definida escrita ali mesmo. NÃO conta como citação válida:
  - um trecho que só REMETE ao outro documento sem repetir o fato (ex.: \
"conforme disciplinado no Termo de Referência", sem o prazo/valor escrito \
naquele mesmo trecho — isso é referência, não afirmação);
  - um campo de formulário ou caixa de seleção sem indicação legível de \
qual opção foi marcada (ex.: "Sim   Não", sem marcação visível no texto);
  - qualquer trecho onde o "fato" não é, na prática, um dado conferível.
- Se só um dos dois lados afirma um fato concreto e o outro só remete a \
ele sem repetir o fato, isso NÃO é uma inconsistência edital-vs-TR válida \
neste formato — não force a comparação encaixando uma remissão como se \
fosse o lado contraditório; não inclua o item na lista.
- Uma informação que aparece em só um dos dois lados (só no edital, ou só \
no TR) NÃO é inconsistência — é informação complementar, não contradição.
- Diferença de nível de detalhe entre os dois lados (um detalhar mais que o \
outro) também NÃO é inconsistência — só conta quando há conflito de \
verdade: números diferentes, prazos diferentes, valores diferentes, \
especificações incompatíveis sobre o mesmo ponto.
- "pagina_edital" e "pagina_tr": o número que aparece em "[PÁGINA N]" de \
onde cada trecho foi tirado (o do edital vem do bloco do EDITAL, o do TR \
vem do bloco do TR — nunca inverta).

Responda em JSON estrito e SOMENTE o JSON — sem markdown, sem \
```json, sem texto antes ou depois. Se não encontrar nenhuma inconsistência \
real, devolva a lista vazia — nunca invente uma só para preencher a \
resposta."""


def _montar_prompt_usuario_inconsistencias(texto_edital_marcado: str, texto_tr_marcado: str) -> str:
    return (
        f"Corpo do edital (marcado por página):\n{texto_edital_marcado}\n\n"
        f"Termo de Referência (marcado por página):\n{texto_tr_marcado}"
    )


def contar_tokens_comparacao(texto_edital_marcado: str, texto_tr_marcado: str) -> int:
    """Mede, ao vivo via count_tokens (não estimativa), quantos tokens de
    entrada UMA chamada de detectar_inconsistencias usaria para estes dois
    textos — sem chamar generate_content, então não gasta a cota de
    tokens/minuto que está sendo medida (count_tokens é uma chamada
    separada, mais barata).

    Usado pelo pipeline (Camada 3) pra decidir se vale espaçar as duas
    chamadas da execução dupla, evitando LIMITE_TOKENS_POR_MINUTO — não é
    a mesma checagem de "grande demais pra uma chamada só"
    (_verificar_tamanho_do_contexto, que olha LIMITE_TOKENS_ENTRADA); esta
    é sobre DUAS chamadas seguidas somarem mais que o limite por minuto.
    """
    if not GEMINI_API_KEY:
        raise ConfiguracaoAusenteError(
            "GEMINI_API_KEY não está definida no .env — gere uma chave no "
            "Google AI Studio e preencha o .env antes de usar a IA"
        )
    if not GEMINI_MODEL:
        raise ConfiguracaoAusenteError(
            "GEMINI_MODEL não está definido no .env — confirme o nome do "
            "modelo (ex.: gemini-3.6-flash) e preencha o .env"
        )

    cliente = genai.Client(api_key=GEMINI_API_KEY)
    prompt_usuario = _montar_prompt_usuario_inconsistencias(texto_edital_marcado, texto_tr_marcado)
    resposta_contagem = cliente.models.count_tokens(model=GEMINI_MODEL, contents=prompt_usuario)
    return resposta_contagem.total_tokens if resposta_contagem.total_tokens is not None else 0


def detectar_inconsistencias(
    texto_edital_marcado: str, texto_tr_marcado: str
) -> list[dict[str, Any]]:
    """Fase 2 (motor de inconsistências), Camada 1: compara o corpo do
    edital com o TR, buscando contradições reais em quantidade, valor,
    prazo e especificação técnica — todos os tipos de uma vez, sem
    hierarquia entre eles.

    `texto_edital_marcado` e `texto_tr_marcado` precisam já vir com marcação
    de página no formato "[PÁGINA N]" — quem monta isso é
    app.pipeline.detectar_inconsistencias_processo, a partir do limite
    identificado na Camada 0 (app.inconsistencias.limite_tr). Este módulo
    não lê banco nem decide o limite, só recebe os dois textos prontos e
    devolve o que a IA achou.

    Devolve uma lista de inconsistências (pode ser vazia — a IA é instruída
    a nunca inventar uma só pra preencher a resposta, ver
    _montar_prompt_sistema_inconsistencias). Cada item tem "tipo",
    "descricao", "trecho_edital", "pagina_edital", "trecho_tr", "pagina_tr".

    Levanta ContextoGrandeDemaisError ANTES de chamar a API se os dois
    textos juntos ultrapassarem o limite de entrada do modelo (checado ao
    vivo via count_tokens — nunca trunca silenciosamente, mesmo princípio
    do Q&A).
    """
    if LLM_PROVIDER != "gemini":
        raise ProvedorNaoSuportadoError(
            f"provedor de IA '{LLM_PROVIDER}' ainda não está implementado "
            "(por enquanto só 'gemini' é suportado)"
        )

    resposta_bruta = _chamar_gemini_inconsistencias(texto_edital_marcado, texto_tr_marcado)
    return _parsear_resposta_inconsistencias(resposta_bruta)


def _chamar_gemini_inconsistencias(texto_edital_marcado: str, texto_tr_marcado: str) -> str:
    # IMPORTANTE: nunca colocar GEMINI_API_KEY em mensagem de erro, log ou
    # print — só ela é passada, sem interpolação em string nenhuma.
    if not GEMINI_API_KEY:
        raise ConfiguracaoAusenteError(
            "GEMINI_API_KEY não está definida no .env — gere uma chave no "
            "Google AI Studio e preencha o .env antes de usar a IA"
        )
    if not GEMINI_MODEL:
        raise ConfiguracaoAusenteError(
            "GEMINI_MODEL não está definido no .env — confirme o nome do "
            "modelo (ex.: gemini-3.6-flash) e preencha o .env"
        )

    cliente = genai.Client(api_key=GEMINI_API_KEY)
    prompt_usuario = _montar_prompt_usuario_inconsistencias(texto_edital_marcado, texto_tr_marcado)

    _verificar_tamanho_do_contexto(cliente, GEMINI_MODEL, prompt_usuario)

    try:
        resposta = cliente.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt_usuario,
            config=types.GenerateContentConfig(
                system_instruction=_montar_prompt_sistema_inconsistencias(),
                response_mime_type="application/json",
                response_json_schema=_SCHEMA_RESPOSTA_INCONSISTENCIAS,
                # Mesma configuração do resto do Engine (checklist e Q&A):
                # "comparar fielmente dois textos fornecidos" também não é
                # geração criativa — mesmo raciocínio de temperatura baixa e
                # thinking "low" (não desligado, pra não perder uma
                # contradição escondida no meio de um texto longo).
                temperature=TEMPERATURA_EXTRACAO,
                max_output_tokens=MAX_OUTPUT_TOKENS,
                thinking_config=types.ThinkingConfig(
                    thinking_level=THINKING_LEVEL_EXTRACAO
                ),
            ),
        )
    except Exception as erro:
        raise RespostaIAError(f"falha ao chamar a API do Gemini: {erro}") from erro

    if resposta.text is None:
        raise RespostaIAError(
            f"a IA não devolveu texto na resposta (finish_reason: {resposta.candidates[0].finish_reason if resposta.candidates else 'desconhecido'})"
        )

    return resposta.text


def _parsear_resposta_inconsistencias(resposta_bruta: str) -> list[dict[str, Any]]:
    try:
        dados = json.loads(resposta_bruta)
    except json.JSONDecodeError as erro:
        logger.error("resposta da IA (inconsistências) não é JSON válido: %r", resposta_bruta)
        raise RespostaIAError(
            "a IA não devolveu um JSON válido para a comparação — o conteúdo bruto foi registrado no log"
        ) from erro

    if not isinstance(dados, dict):
        raise RespostaIAError(
            "a IA devolveu um JSON válido, mas não é um objeto com os campos esperados"
        )

    inconsistencias = dados.get("inconsistencias")
    if not isinstance(inconsistencias, list):
        raise RespostaIAError(
            "a IA devolveu um JSON válido, mas 'inconsistencias' não é uma lista"
        )

    return inconsistencias
