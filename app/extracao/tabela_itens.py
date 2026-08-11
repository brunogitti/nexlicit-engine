# Fatiamento de uma tabela de itens (Anexo I / Termo de Referência) em
# registros por item — promovido do diagnóstico exploratório feito antes de
# decidir se a extração de amostra dava pra ser determinística (sem IA).
# A heurística (números sequenciais como âncora, limite de fim por marcador
# estrutural do documento) foi validada contra um edital real de 584 itens
# e um caso sintético; ver conversa do Passo 8.
#
# Não fala com IA nem com banco: só processamento de texto sobre o que o
# extrator do Passo 2 (app/extracao/extrator.py) já devolveu.

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.extracao.extrator import DocumentoExtraido


def _remover_acento_char(char: str) -> str:
    decomposto = unicodedata.normalize("NFKD", char)
    sem_marca = "".join(c for c in decomposto if not unicodedata.combining(c))
    return sem_marca if sem_marca else char


def normalizar_com_mapa(texto: str) -> tuple[str, list[int]]:
    """Normaliza pra busca (remove acento, maiúsculas, colapsa espaço em um
    só) e devolve junto um mapa de posição: mapa[i] é o índice em `texto`
    que originou o caractere normalizado[i].

    Diferente do normalizar() do validador (Passo 4), que preserva acento e
    caixa DE PROPÓSITO (ali a comparação precisa manter o sentido do
    trecho). Aqui a busca é por palavra-chave — acento e caixa atrapalhariam
    o casamento — então normalizam; o mapa é o que permite, depois de achar
    algo no texto normalizado, voltar pro texto original (com acento/caixa
    de verdade) pra montar um trecho legível.
    """
    caracteres: list[str] = []
    mapa: list[int] = []
    espaco_pendente = False
    for indice, char in enumerate(texto):
        if char.isspace():
            espaco_pendente = True
            continue
        if espaco_pendente and caracteres:
            caracteres.append(" ")
            mapa.append(indice)
        espaco_pendente = False
        for base in _remover_acento_char(char).upper():
            caracteres.append(base)
            mapa.append(indice)
    return "".join(caracteres), mapa


@dataclass
class ItemTabela:
    """Um item isolado da tabela (uma linha da planilha de itens)."""

    numero: int
    pagina: int | None
    localizador: str
    texto: str  # texto ORIGINAL (acento/caixa reais), só desta linha


# Marcadores de fim de tabela — o que vier primeiro depois do último item
# mapeado. Testado e explicado na conversa: um marcador de "ANEXO" sozinho
# NÃO basta (o próprio anexo que contém a tabela pode continuar com texto de
# cláusula depois da tabela, antes do próximo anexo começar); a numeração de
# cláusula retomando é o sinal que efetivamente pegou isso no edital real.
_PADRAO_ANEXO = re.compile(r"ANEXO\s+([IVXLCDM]+|\d+)")
# Exige o PONTO FINAL logo após o segundo grupo de dígitos (ex.: "2.3.") —
# sem isso, bate em separador de milhar brasileiro dentro da tabela (ex.:
# "2.000 ML", que não tem ponto imediatamente após "000").
_PADRAO_CLAUSULA = re.compile(r"(?<!\d)\d{1,2}\.\d{1,2}\.")

# Detecção do cabeçalho da tabela: não depende do texto exato de um edital
# específico (o antigo "ITEM DESCRICAO DO PRODUTO" só batia no Ouroeste) —
# procura "ITEM" isolado, com "DESCRICAO"/"ESPECIFICACAO" pertinho depois, e
# "QUANTIDADE"/"QTD"/"QTDE" um pouco mais adiante. É como tabela de item de
# edital costuma ser rotulada, mesmo variando a redação exata.
_PADRAO_ITEM_PALAVRA = re.compile(r"\bITEM\b")
_PADRAO_DESCRICAO = re.compile(r"DESCRICAO|ESPECIFICACAO")
_PADRAO_QUANTIDADE = re.compile(r"QUANTIDADE|\bQTDE?\b")
_JANELA_DESCRICAO = 100
_JANELA_QUANTIDADE = 200


def _localizar_fim_cabecalho(texto_normalizado: str) -> int | None:
    """Acha onde termina o cabeçalho de uma tabela de itens. None se não
    achar os três marcos (ITEM, DESCRICAO/ESPECIFICACAO, QUANTIDADE/QTD)
    perto um do outro em nenhum lugar do texto."""
    for m_item in _PADRAO_ITEM_PALAVRA.finditer(texto_normalizado):
        inicio = m_item.start()
        m_desc = _PADRAO_DESCRICAO.search(
            texto_normalizado, inicio, inicio + _JANELA_DESCRICAO
        )
        if not m_desc:
            continue
        m_qtd = _PADRAO_QUANTIDADE.search(
            texto_normalizado, inicio, inicio + _JANELA_QUANTIDADE
        )
        if not m_qtd:
            continue
        return m_qtd.end()
    return None


def fatiar_por_item(documento: DocumentoExtraido) -> list[ItemTabela]:
    """Isola cada item de uma tabela de itens dentro do texto já extraído.

    Detecta o começo da tabela automaticamente (ver _localizar_fim_cabecalho)
    — não precisa saber de antemão o texto exato do cabeçalho. Devolve lista
    vazia se não achar esse padrão (documento sem tabela de itens
    reconhecível, ou sem tabela nenhuma).
    """
    partes_originais: list[str] = []
    mapa_offset_pagina: list[tuple[int, int | None]] = []
    offset = 0
    for bloco in documento.blocos:
        mapa_offset_pagina.append((offset, bloco.pagina))
        partes_originais.append(bloco.texto)
        offset += len(bloco.texto) + 1
    texto_original = " ".join(partes_originais)

    def pagina_da_posicao(pos: int) -> int | None:
        pagina_atual = None
        for inicio, pagina in mapa_offset_pagina:
            if inicio <= pos:
                pagina_atual = pagina
            else:
                break
        return pagina_atual

    texto_normalizado, mapa_pos = normalizar_com_mapa(texto_original)

    posicao_inicial = _localizar_fim_cabecalho(texto_normalizado)
    if posicao_inicial is None:
        return []

    mapa_item_pos: dict[int, int] = {}
    pos = posicao_inicial
    numero = 1
    while numero <= 2000:
        padrao_item = re.compile(rf"(?<!\d){numero}(?!\d)\s+[A-Z]")
        encontro = padrao_item.search(texto_normalizado, pos)
        if not encontro:
            break
        mapa_item_pos[numero] = encontro.start()
        pos = encontro.start() + 1
        numero += 1

    if not mapa_item_pos:
        return []

    posicoes_ordenadas = sorted(mapa_item_pos.items(), key=lambda kv: kv[1])
    posicao_pos_ultimo_item = posicoes_ordenadas[-1][1]

    candidatos_fim: list[int] = []
    m_anexo = _PADRAO_ANEXO.search(texto_normalizado, posicao_pos_ultimo_item)
    if m_anexo:
        candidatos_fim.append(m_anexo.start())
    m_clausula = _PADRAO_CLAUSULA.search(texto_normalizado, posicao_pos_ultimo_item)
    if m_clausula:
        candidatos_fim.append(m_clausula.start())
    limite_fim = min(candidatos_fim) if candidatos_fim else len(texto_normalizado)

    itens: list[ItemTabela] = []
    for indice, (numero_item, pos_norm) in enumerate(posicoes_ordenadas):
        fim_norm = (
            posicoes_ordenadas[indice + 1][1]
            if indice + 1 < len(posicoes_ordenadas)
            else limite_fim
        )
        fim_norm = min(fim_norm, limite_fim)
        if pos_norm >= limite_fim:
            break

        pos_original = mapa_pos[pos_norm]
        fim_original = mapa_pos[fim_norm - 1] + 1 if fim_norm > pos_norm else pos_original
        pagina = pagina_da_posicao(pos_original)

        itens.append(
            ItemTabela(
                numero=numero_item,
                pagina=pagina,
                localizador=f"página {pagina}" if pagina is not None else "posição desconhecida",
                texto=texto_original[pos_original:fim_original].strip(),
            )
        )

    return itens


def localizar_documento_com_tabela(
    documentos: list[DocumentoExtraido],
) -> tuple[DocumentoExtraido, list[ItemTabela]] | None:
    """Identifica automaticamente qual arquivo (entre vários enviados pro
    mesmo processo) tem uma tabela de itens: tenta fatiar_por_item() em
    cada um e fica com o que produzir MAIS itens.

    Por que "mais itens" em vez de só "achou alguma coisa": um arquivo sem
    tabela nenhuma produz 0 itens; um arquivo com uma tabela de itens de
    verdade produz dezenas ou centenas (números sequenciais 1, 2, 3...) —
    não tem como confundir por acidente com um falso positivo eventual do
    padrão de cabeçalho (que na pior das hipóteses acharia 1 ou 2 itens).

    Devolve None se nenhum arquivo tiver tabela nenhuma.
    """
    melhor: tuple[DocumentoExtraido, list[ItemTabela]] | None = None
    for documento in documentos:
        itens = fatiar_por_item(documento)
        if itens and (melhor is None or len(itens) > len(melhor[1])):
            melhor = (documento, itens)
    return melhor
