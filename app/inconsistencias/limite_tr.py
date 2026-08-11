# Motor de inconsistências edital-vs-TR (Fase 2, segunda metade) — Camada 0:
# achar o LIMITE entre o corpo do edital e o Termo de Referência (TR), sem
# comparar conteúdo nenhum ainda (isso é a Camada 1). Se esse limite errar,
# qualquer comparação depois fica sem valor — por isso esta camada só
# detecta e reporta, não decide sozinha se algo é uma inconsistência.
#
# Reaproveita o que já existe, sem abordagem nova:
# - texto_pagina (Fase 2, Camada 0 do Q&A) já guarda o texto bruto por
#   página, com marcador de página — é a fonte de dados aqui.
# - normalizar_com_mapa (app/extracao/tabela_itens.py, Passo 8) já resolve
#   busca por palavra-chave ignorando acento/caixa, com mapa de volta pra
#   posição original — mesma função, não uma reimplementação.
# - Busca por marcador com regex sobre texto normalizado é o MESMO padrão
#   do extrator determinístico do Passo 8 (_PADRAO_ANEXO, _PADRAO_CLAUSULA
#   em tabela_itens.py) — dois marcadores, do mais forte pro mais fraco.
#
# Dois casos, mesma ideia por trás (achar o marcador de início do TR),
# aplicada em granularidade diferente:
# - Caso A (processo com >1 arquivo): cada arquivo é candidato inteiro a
#   ser "o TR" — procura o marcador dentro de cada um, separadamente.
# - Caso B (processo com 1 arquivo só): o TR está embutido como anexo —
#   procura o marcador dentro da sequência de páginas desse arquivo, e o
#   ponto onde aparece corta o texto em "antes" (edital) e "a partir daqui"
#   (TR).
#
# Nunca adivinha: se a busca não encontrar nada, ou encontrar de um jeito
# ambíguo (mais de um arquivo com o marcador, por exemplo), devolve
# identificado=False com o motivo em texto — quem chama decide o que fazer
# com isso, este módulo não força um palpite.

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.db.repositorio import obter_texto_paginas
from app.extracao.tabela_itens import normalizar_com_mapa

# Marcadores de início de bloco de TR, em ordem de confiança (o mais forte
# é tentado primeiro; o mais fraco só entra se o forte não aparecer em
# lugar nenhum, EM FORMA DE TÍTULO — ver _parece_titulo). "TERMO DE
# REFERENCIA" é o título explícito da seção — alta confiança. "ANEXO I" é
# convenção comum (muitos editais numeram o TR como primeiro anexo), mas
# não é garantia — um "Anexo I" pode ser outra coisa (ex.: modelo de
# proposta) —, por isso só como fallback, com o método reportando qual dos
# dois foi usado, pra quem revisar saber o quão forte é o sinal.
_PADRAO_TERMO_REFERENCIA = re.compile(r"TERMO\s+DE\s+REFERENCIA")
# \b depois do "I"/"1" evita casar com "ANEXO II", "ANEXO III", "ANEXO IV"
# ou "ANEXO 10" — mesmo cuidado do _PADRAO_ANEXO de tabela_itens.py, mas
# restrito ao primeiro anexo especificamente (não qualquer anexo).
_PADRAO_ANEXO_I = re.compile(r"ANEXO\s+(I|1)\b")

_ROTULO_TERMO_REFERENCIA = "TERMO DE REFERÊNCIA"
_ROTULO_ANEXO_I = "ANEXO I"

# ---------- Filtro de "forma de título" (tentativa 2 da Camada 0) ----------
#
# A tentativa 1 (primeira ocorrência do marcador, sem olhar o contexto)
# errou em 7/7 processos reais: em todos, a primeira ocorrência de "Termo de
# Referência" é uma CITAÇÃO dentro de uma cláusula do próprio edital
# ("...conforme disposto no Termo de Referência...", "...em conformidade
# com o que dispõe o Termo de Referência..."), não o título da seção.
#
# Calibrado contra os 7 editais reais (rodada de diagnóstico linha a linha):
# toda ocorrência que É de fato o título tem a MESMA forma — a linha inteira
# (no texto ORIGINAL, com quebra de linha física do PDF preservada por
# fitz.get_text()) não sobra nada além do próprio marcador e, no máximo, uma
# menção a "ANEXO <numeral>" colada nele (ex.: linha isolada "TERMO DE
# REFERÊNCIA", ou "ANEXO I – TERMO DE REFERÊNCIA"). Toda ocorrência que é
# citação de passagem tem texto de frase de verdade sobrando na mesma linha.
#
# Dois casos de falso positivo restante, achados nos mesmos 7 dados reais,
# tratados à parte (ver comentário de cada regex abaixo):
#   1) o próprio marcador aparece dentro de uma lista de anexos do edital,
#      numerada com a numeração de cláusula do PRÓPRIO edital (ex.:
#      "14.16.1. ANEXO I - Termo de Referência;") — a linha começa com
#      numeração de cláusula, o que um título de seção nunca faz aqui.
#   2) o marcador aparece numa tabela de anexos "achatada" pela extração de
#      PDF, em que o número do item da lista vira uma linha PRÓPRIA logo
#      ANTES da linha do marcador (ex.: linha "12.11.1" sozinha, depois
#      "ANEXO I - Termo de Referência", depois "12.11.2" sozinha) — a linha
#      do marcador fica "limpa" (passaria no primeiro filtro), mas a linha
#      imediatamente anterior é só um número de lista solto, isolado — um
#      título de verdade nunca fica colado assim, embaixo de um número de
#      lista solto (nos 7 casos reais, um título de verdade vem depois de
#      linha em branco, de "Página N/M", ou de outra linha do próprio
#      título/rótulo do anexo — nunca de um número de lista solto).
_PADRAO_NUMERACAO_CLAUSULA_INICIO = re.compile(r"^\d+(\.\d+)*\.?\s")
_PADRAO_ENUMERACAO_PURA = re.compile(r"[\dIVXLCDM]+(\.[\dIVXLCDM]+)*\.?", re.IGNORECASE)
_PADRAO_LETRA = re.compile(r"[^\W\d_]", re.UNICODE)  # "letra" = \w que não é dígito
_PADRAO_REMOVER_TR = re.compile(r"TERMO\s+DE\s+REFERENCIA", re.IGNORECASE)
_PADRAO_REMOVER_ANEXO = re.compile(r"ANEXO\s+([IVXLCDM]+|\d+)\b", re.IGNORECASE)

# Terceiro caso de falso positivo, achado ao RODAR o refinamento acima
# contra os 7 reais (Pirangi e Frutal): uma frase de cláusula que termina
# EXATAMENTE em "...Termo de Referência." — sem mais nenhuma palavra depois
# do marcador na mesma linha — passa pelo filtro de resíduo (o "." que sobra
# não é letra nenhuma), mesmo com uma frase de verdade colada ANTES dele.
# Ex.: linha_antes "com os requisitos estabelecidos neste Edital, contenham
# vícios insanáveis..." (mais de 60 caracteres) seguida de "Termo de
# Referência." isolada. Um título de verdade nunca tem uma frase tão comprida
# assim imediatamente antes — nos 7 reais, a maior linha_antes de um título
# genuíno tem 56 caracteres (Paulínia, "ANEXO I – ESPECIFICAÇÕES DO OBJETO /
# TERMO DE REFERÊNCIA"); a menor linha_antes de um falso positivo desse tipo
# tem 85 (Frutal). 60 fica no meio dessa folga real, não é chute.
_LIMITE_CARACTERES_LINHA_ANTES = 60


def _eh_enumeracao_pura(linha: str) -> bool:
    """True quando a linha inteira (já sem espaço nas bordas) é só um
    número/numeral de lista, sem mais nada — ex.: "12.11.1", "I.", "7"."""
    return bool(_PADRAO_ENUMERACAO_PURA.fullmatch(linha.strip()))


def _parece_titulo(linha_match: str, linha_antes: str) -> bool:
    """Só aceita como TÍTULO de seção — não citação de passagem, não item de
    lista de anexos — quando os quatro critérios abaixo passam. Ver os
    blocos de comentário acima pra origem de cada um."""
    if _PADRAO_NUMERACAO_CLAUSULA_INICIO.match(linha_match):
        return False  # ex.: "14.16.1. ANEXO I - Termo de Referência;"
    if _eh_enumeracao_pura(linha_antes):
        return False  # ex.: linha anterior é só "12.11.1"
    if len(linha_antes) > _LIMITE_CARACTERES_LINHA_ANTES:
        return False  # linha anterior comprida demais pra ser rótulo/título, é frase
    # Normaliza (tira acento/caixa) SÓ pra checagem de resíduo: linha_match
    # vem do texto ORIGINAL (com acento de verdade, ex. "REFERÊNCIA"), mas
    # _PADRAO_REMOVER_TR é escrito sem acento — sem normalizar aqui, o sub()
    # nunca bate contra a forma acentuada e o resíduo "sobra" por engano,
    # rejeitando até o título mais limpo possível. Achado ao rodar contra os
    # 7 editais reais (tentativa 2 desta camada): sem essa normalização,
    # Mirassol e Paulínia voltavam "não identificado" à toa, e Pirangi caía
    # num "ANEXO I" errado (o TR de Pirangi é o ANEXO X, não o I).
    linha_normalizada, _ = normalizar_com_mapa(linha_match)
    residuo = _PADRAO_REMOVER_TR.sub("", linha_normalizada)
    residuo = _PADRAO_REMOVER_ANEXO.sub("", residuo)
    return _PADRAO_LETRA.search(residuo) is None


@dataclass
class DeteccaoLimiteTR:
    """Resultado da Camada 0 pra UM processo: onde termina o edital e começa
    o TR, ou por que não deu pra saber."""

    identificado: bool
    # "arquivo_separado" (Caso A) ou "marcador_no_texto" (Caso B); None se
    # não identificado.
    metodo: str | None
    # Qual dos dois marcadores decidiu (_ROTULO_TERMO_REFERENCIA ou
    # _ROTULO_ANEXO_I); None se não identificado.
    marcador_encontrado: str | None
    # Só preenchido no método "arquivo_separado" — id do arquivo que virou
    # o TR inteiro.
    arquivo_tr_id: int | None
    inicio_pagina: int | None
    inicio_localizador: str | None
    # Listas de linhas de texto_pagina (dict, mesmo formato de
    # obter_texto_paginas) — o conteúdo em si, pronto pra Camada 1 usar.
    paginas_edital: list[dict[str, Any]] = field(default_factory=list)
    paginas_tr: list[dict[str, Any]] = field(default_factory=list)
    # Só preenchido quando identificado=False — explica o motivo em texto,
    # pra reportar sem esconder o porquê.
    motivo_nao_identificado: str | None = None


def _linha_e_anterior(texto_original: str, pos_ini: int, pos_fim: int) -> tuple[str, str]:
    """(linha que contém [pos_ini, pos_fim), linha imediatamente anterior a
    ela), ambas já sem espaço nas bordas. 'linha' = trecho entre quebras de
    linha (\\n) do texto ORIGINAL — fitz.get_text() preserva a quebra de
    linha física do PDF, então isso reflete layout de verdade, não é um
    corte arbitrário de caracteres."""
    inicio_linha = texto_original.rfind("\n", 0, pos_ini) + 1
    fim_linha = texto_original.find("\n", pos_fim)
    if fim_linha == -1:
        fim_linha = len(texto_original)
    linha = texto_original[inicio_linha:fim_linha].strip()

    fim_linha_antes = max(inicio_linha - 1, 0)
    inicio_linha_antes = texto_original.rfind("\n", 0, fim_linha_antes) + 1
    linha_antes = texto_original[inicio_linha_antes:fim_linha_antes].strip()

    return linha, linha_antes


def _primeira_ocorrencia_marcador(texto_original: str) -> tuple[str, int] | None:
    """Procura os dois marcadores, do mais forte pro mais fraco — mas só
    aceita uma ocorrência que PAREÇA TÍTULO de seção (ver _parece_titulo),
    não a primeira ocorrência crua. Continua procurando as próximas
    ocorrências do MESMO marcador antes de cair pro marcador mais fraco.

    Trabalha em cima do texto ORIGINAL (não normalizado) porque o filtro de
    título depende de quebra de linha real, que normalizar_com_mapa
    colapsa de propósito (útil pra busca por palavra-chave, ruim pra saber
    onde termina uma linha). A normalização entra só internamente, pra
    achar as posições candidatas — o retorno já vem em posição do texto
    ORIGINAL (mapa_pos aplicado aqui dentro, quem chama não precisa saber
    disso).
    """
    texto_normalizado, mapa_pos = normalizar_com_mapa(texto_original)

    for padrao, rotulo in ((_PADRAO_TERMO_REFERENCIA, _ROTULO_TERMO_REFERENCIA),
                            (_PADRAO_ANEXO_I, _ROTULO_ANEXO_I)):
        for encontro in padrao.finditer(texto_normalizado):
            pos_ini_original = mapa_pos[encontro.start()]
            pos_fim_original = mapa_pos[encontro.end() - 1] + 1
            linha, linha_antes = _linha_e_anterior(texto_original, pos_ini_original, pos_fim_original)
            if _parece_titulo(linha, linha_antes):
                return (rotulo, pos_ini_original)
    return None


def _concatenar_paginas(paginas: list[dict[str, Any]]) -> tuple[str, list[int]]:
    """Concatena o texto de uma lista de linhas de texto_pagina (na ordem
    dada) num texto só, e devolve junto a lista de offsets: offsets[i] é a
    posição (no texto concatenado) onde paginas[i] começa. Mesmo padrão de
    mapa_offset_pagina usado em tabela_itens.fatiar_por_item."""
    partes: list[str] = []
    offsets: list[int] = []
    offset = 0
    for pagina in paginas:
        offsets.append(offset)
        partes.append(pagina["texto"])
        offset += len(pagina["texto"]) + 1  # +1 pelo separador do join abaixo
    return "\n".join(partes), offsets


def _indice_pagina_da_posicao(offsets: list[int], posicao: int) -> int:
    """offsets vem de _concatenar_paginas — devolve o índice da página cujo
    intervalo contém `posicao` (a última cujo offset começa em posicao ou
    antes dela)."""
    indice_atual = 0
    for indice, inicio in enumerate(offsets):
        if inicio <= posicao:
            indice_atual = indice
        else:
            break
    return indice_atual


def _detectar_caso_arquivo_unico(paginas: list[dict[str, Any]]) -> DeteccaoLimiteTR:
    """Caso B: um arquivo só — o TR, se existir, está embutido como anexo.
    Acha o marcador na sequência de páginas e corta ali: antes é edital, a
    partir dali (inclusive) é TR."""
    texto, offsets = _concatenar_paginas(paginas)

    ocorrencia = _primeira_ocorrencia_marcador(texto)
    if ocorrencia is None:
        return DeteccaoLimiteTR(
            identificado=False,
            metodo=None,
            marcador_encontrado=None,
            arquivo_tr_id=None,
            inicio_pagina=None,
            inicio_localizador=None,
            motivo_nao_identificado=(
                "não achei nem 'termo de referência' nem 'anexo I' isolado "
                "no texto deste processo — não dá pra saber onde o TR começa"
            ),
        )

    marcador, posicao_original = ocorrencia
    indice_inicio = _indice_pagina_da_posicao(offsets, posicao_original)
    pagina_inicio = paginas[indice_inicio]

    return DeteccaoLimiteTR(
        identificado=True,
        metodo="marcador_no_texto",
        marcador_encontrado=marcador,
        arquivo_tr_id=None,
        inicio_pagina=pagina_inicio["numero_pagina"],
        inicio_localizador=pagina_inicio["localizador"],
        paginas_edital=paginas[:indice_inicio],
        paginas_tr=paginas[indice_inicio:],
    )


def _detectar_caso_multiplos_arquivos(
    paginas: list[dict[str, Any]], arquivo_ids: list[int]
) -> DeteccaoLimiteTR:
    """Caso A: mais de um arquivo — cada um é candidato inteiro a TR.
    Procura o marcador dentro do texto de CADA arquivo, separadamente (não
    supõe que o segundo arquivo é automaticamente o TR — um segundo arquivo
    pode ser outra coisa, ex.: um anexo de declarações)."""
    por_arquivo = {aid: [p for p in paginas if p["arquivo_id"] == aid] for aid in arquivo_ids}

    achados: dict[int, tuple[str, int]] = {}
    for arquivo_id, paginas_arquivo in por_arquivo.items():
        texto, _ = _concatenar_paginas(paginas_arquivo)
        ocorrencia = _primeira_ocorrencia_marcador(texto)
        if ocorrencia is not None:
            achados[arquivo_id] = ocorrencia

    # Se algum arquivo tem o marcador FORTE (título explícito), ele decide
    # sozinho — mesmo que outro arquivo tenha só o marcador fraco (ANEXO I).
    # Só cai pro conjunto fraco se NENHUM arquivo tiver o forte.
    achados_fortes = {
        aid: oc for aid, oc in achados.items() if oc[0] == _ROTULO_TERMO_REFERENCIA
    }
    candidatos = achados_fortes if achados_fortes else achados

    if len(candidatos) == 1:
        arquivo_tr_id, (marcador, _posicao_original) = next(iter(candidatos.items()))
        paginas_tr = por_arquivo[arquivo_tr_id]
        paginas_edital = [
            p for aid in arquivo_ids if aid != arquivo_tr_id for p in por_arquivo[aid]
        ]
        primeira_pagina_tr = paginas_tr[0]
        return DeteccaoLimiteTR(
            identificado=True,
            metodo="arquivo_separado",
            marcador_encontrado=marcador,
            arquivo_tr_id=arquivo_tr_id,
            inicio_pagina=primeira_pagina_tr["numero_pagina"],
            inicio_localizador=primeira_pagina_tr["localizador"],
            paginas_edital=paginas_edital,
            paginas_tr=paginas_tr,
        )

    if len(candidatos) > 1:
        motivo = (
            "mais de um arquivo contém marcador de início de TR "
            f"(arquivos {sorted(candidatos)}) — ambíguo, não dá pra escolher sozinho"
        )
    else:
        motivo = (
            "nenhum dos arquivos contém 'termo de referência' nem 'anexo I' "
            "isolado — não dá pra saber qual arquivo é o TR"
        )

    return DeteccaoLimiteTR(
        identificado=False,
        metodo=None,
        marcador_encontrado=None,
        arquivo_tr_id=None,
        inicio_pagina=None,
        inicio_localizador=None,
        motivo_nao_identificado=motivo,
    )


def identificar_blocos_edital_tr(
    processo_id: int, caminho_banco: str | None = None
) -> DeteccaoLimiteTR:
    """Ponto de entrada da Camada 0: devolve onde o edital termina e o TR
    começa, pra este processo. Não decide se o processo TEM ou não um TR de
    verdade — só reporta o que achou (ou não) a partir do texto salvo em
    texto_pagina.

    Escolhe o caso (A ou B) pelo número de arquivos DISTINTOS com texto
    salvo — não pelo total de arquivos do processo (ver
    app.db.repositorio.criar_arquivo): um arquivo cadastrado mas sem
    texto_pagina nenhum (processo ainda não analisado com essa parte) não
    entra na conta.
    """
    paginas = obter_texto_paginas(processo_id, caminho_banco=caminho_banco)
    if not paginas:
        return DeteccaoLimiteTR(
            identificado=False,
            metodo=None,
            marcador_encontrado=None,
            arquivo_tr_id=None,
            inicio_pagina=None,
            inicio_localizador=None,
            motivo_nao_identificado=(
                "processo sem texto extraído (texto_pagina vazio) — rode a "
                "análise antes de detectar o limite edital/TR"
            ),
        )

    arquivo_ids = sorted({p["arquivo_id"] for p in paginas})

    if len(arquivo_ids) > 1:
        return _detectar_caso_multiplos_arquivos(paginas, arquivo_ids)
    return _detectar_caso_arquivo_unico(paginas)
