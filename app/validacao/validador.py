# Validador de trecho: confere se o "trecho" que a IA (Passo 3) devolveu para
# cada exigência existe de verdade, palavra por palavra, no texto extraído do
# documento original (Passo 2). É a trava contra alucinação descrita no
# planejamento: "localizado" quando bate, "inferido" quando não bate — nunca
# descarta a exigência, só marca o quanto dá pra confiar nela.
#
# Este módulo não fala com IA nem com banco: só processamento de texto puro.

from __future__ import annotations

import re
from typing import Any

from app.extracao.extrator import DocumentoExtraido

# Tabela de caracteres que variam entre o texto extraído do PDF/DOCX e o que
# a IA copia, sem mudar o conteúdo: aspas curvas -> aspas retas, travessão e
# meia-risca -> hífen comum. Acentuação, maiúsculas/minúsculas e pontuação de
# conteúdo (vírgula, ponto, número) NÃO entram aqui de propósito — mudar isso
# mudaria o sentido do trecho, e o pedido era normalizar só formatação.
_EQUIVALENCIAS_DE_FORMATACAO = str.maketrans(
    {
        "“": '"',  # “
        "”": '"',  # ”
        "„": '"',  # „
        "‘": "'",  # ‘
        "’": "'",  # ’
        "–": "-",  # – (meia-risca)
        "—": "-",  # — (travessão)
    }
)

# Abaixo deste tamanho (já normalizado), o trecho vira "inferido" sem sequer
# tentar buscar. Um trecho curto ("CNPJ;", por exemplo) é comum o bastante
# pra bater em qualquer lugar de um edital de dezenas de páginas por pura
# coincidência — "achar" ele não seria uma confirmação de verdade. 20
# caracteres cobre uma oração curta mas completa; abaixo disso é palavra
# solta ou fragmento. Como referência: no teste de ouro (Passo 3, Câmara de
# Lins), o trecho real mais curto tem ~80 caracteres — bem acima do limite.
TAMANHO_MINIMO_TRECHO = 20


def normalizar(texto: str) -> str:
    """Colapsa espaços/quebras de linha/tabs em um espaço só e unifica aspas
    e travessões equivalentes. Não mexe em acentuação, caixa ou pontuação de
    conteúdo — só formatação que varia entre o PDF/DOCX e a cópia da IA.

    Fica exposta (sem "_" no nome) porque compara trecho-contra-texto usando
    a mesma regra dos dois lados; se algum dia outro módulo precisar da
    mesma normalização, reaproveita daqui em vez de duplicar.
    """
    texto = texto.translate(_EQUIVALENCIAS_DE_FORMATACAO)
    texto = re.sub(r"\s+", " ", texto)
    return texto.strip()


def validar_exigencias(
    exigencias: list[dict[str, Any]],
    documentos: list[DocumentoExtraido],
) -> list[dict[str, Any]]:
    """Confere o campo "trecho" de cada exigência (saída do Passo 3) contra
    os blocos de texto extraídos dos documentos de origem (saída do Passo 2).

    Devolve uma nova lista, na mesma ordem, com cada exigência enriquecida
    por: "confianca" ("localizado"/"inferido"), "pagina", "localizador" e
    "arquivo_origem" (os três None quando inferido), "ocorrencias_encontradas"
    (quantas vezes o trecho apareceu ao todo) e "cruzou_pagina" (True quando
    o trecho só foi achado juntando um bloco com o seguinte — nesse caso
    "pagina"/"localizador" viram um intervalo, ex. "9-10").

    Não reavalia categoria, descrição, base legal ou nenhum outro campo que
    já veio do Passo 3 — este módulo só confere o trecho.
    """
    return [_validar_uma_exigencia(exigencia, documentos) for exigencia in exigencias]


def _validar_uma_exigencia(
    exigencia: dict[str, Any], documentos: list[DocumentoExtraido]
) -> dict[str, Any]:
    trecho = exigencia.get("trecho")

    if not trecho or not trecho.strip():
        return _marcar_como_inferido(exigencia)

    trecho_normalizado = normalizar(trecho)
    if len(trecho_normalizado) < TAMANHO_MINIMO_TRECHO:
        return _marcar_como_inferido(exigencia)

    # Primeiro tenta bloco a bloco (o caso comum e mais barato). Só parte pra
    # busca em par de blocos consecutivos se isso falhar — cobre o caso de
    # a IA copiar um trecho que atravessa uma quebra de página/parágrafo.
    enriquecimento = _buscar_em_blocos_unicos(trecho_normalizado, documentos)
    if enriquecimento is None:
        enriquecimento = _buscar_em_pares_de_blocos(trecho_normalizado, documentos)
    if enriquecimento is None:
        return _marcar_como_inferido(exigencia)

    return {**exigencia, **enriquecimento}


def _buscar_em_blocos_unicos(
    trecho_normalizado: str, documentos: list[DocumentoExtraido]
) -> dict[str, Any] | None:
    """Procura o trecho dentro de cada bloco isoladamente. Devolve None se
    não achar em bloco nenhum (não é erro — só significa "tenta o par")."""
    total_ocorrencias = 0
    primeira_ocorrencia: tuple[str, int | None, str] | None = None

    for documento in documentos:
        for bloco in documento.blocos:
            texto_normalizado = normalizar(bloco.texto)
            ocorrencias_no_bloco = texto_normalizado.count(trecho_normalizado)
            if ocorrencias_no_bloco:
                if primeira_ocorrencia is None:
                    primeira_ocorrencia = (
                        documento.nome_arquivo,
                        bloco.pagina,
                        bloco.localizador,
                    )
                total_ocorrencias += ocorrencias_no_bloco

    if primeira_ocorrencia is None:
        return None

    nome_arquivo, pagina, localizador = primeira_ocorrencia
    return {
        "confianca": "localizado",
        "pagina": pagina,
        "localizador": localizador,
        "arquivo_origem": nome_arquivo,
        "ocorrencias_encontradas": total_ocorrencias,
        "cruzou_pagina": False,
    }


def _buscar_em_pares_de_blocos(
    trecho_normalizado: str, documentos: list[DocumentoExtraido]
) -> dict[str, Any] | None:
    """Procura o trecho juntando cada bloco com o seguinte (mesmo arquivo),
    pro caso de o trecho atravessar uma quebra de página/parágrafo. Não vai
    além de um par — se não achar aqui, quem chamou marca como "inferido"."""
    total_ocorrencias = 0
    primeira_ocorrencia: tuple[str, str | None, str] | None = None

    for documento in documentos:
        blocos = documento.blocos
        for indice in range(len(blocos) - 1):
            bloco_atual = blocos[indice]
            bloco_seguinte = blocos[indice + 1]
            # Um espaço entre os dois: cada lado já veio sem espaço nas
            # bordas (normalizar() dá strip), e uma quebra de página no
            # documento original corresponde a essa mesma separação.
            texto_do_par = normalizar(bloco_atual.texto) + " " + normalizar(bloco_seguinte.texto)
            ocorrencias_no_par = texto_do_par.count(trecho_normalizado)
            if ocorrencias_no_par:
                if primeira_ocorrencia is None:
                    pagina_intervalo = (
                        f"{bloco_atual.pagina}-{bloco_seguinte.pagina}"
                        if bloco_atual.pagina is not None and bloco_seguinte.pagina is not None
                        else None
                    )
                    localizador_intervalo = _combinar_localizadores(
                        bloco_atual.localizador, bloco_seguinte.localizador
                    )
                    primeira_ocorrencia = (
                        documento.nome_arquivo,
                        pagina_intervalo,
                        localizador_intervalo,
                    )
                total_ocorrencias += ocorrencias_no_par

    if primeira_ocorrencia is None:
        return None

    nome_arquivo, pagina_intervalo, localizador_intervalo = primeira_ocorrencia
    return {
        "confianca": "localizado",
        "pagina": pagina_intervalo,
        "localizador": localizador_intervalo,
        "arquivo_origem": nome_arquivo,
        "ocorrencias_encontradas": total_ocorrencias,
        "cruzou_pagina": True,
    }


def _combinar_localizadores(localizador_1: str, localizador_2: str) -> str:
    """Junta "página 9" + "página 10" em "página 9-10" (mesma lógica serve
    pra "parágrafo 14" + "parágrafo 15" -> "parágrafo 14-15", por extrair o
    número do final de cada localizador em vez de assumir o formato PDF)."""
    numero_1 = re.search(r"(\d+)$", localizador_1)
    numero_2 = re.search(r"(\d+)$", localizador_2)
    if numero_1 and numero_2:
        prefixo = localizador_1[: numero_1.start()].rstrip()
        return f"{prefixo} {numero_1.group(1)}-{numero_2.group(1)}"
    # Fallback pouco provável: localizador num formato sem número no final.
    return f"{localizador_1} / {localizador_2}"


def _marcar_como_inferido(exigencia: dict[str, Any]) -> dict[str, Any]:
    return {
        **exigencia,
        "confianca": "inferido",
        "pagina": None,
        "localizador": None,
        "arquivo_origem": None,
        "ocorrencias_encontradas": 0,
        "cruzou_pagina": False,
    }
