# Extrator determinístico de exigências técnicas por item, sem IA (Passo 8).
# Para cada item já isolado (app/extracao/tabela_itens.py), procura palavras-
# chave fixas (app/extracao/palavras_chave_item.yaml) e monta um registro
# por achado, com o trecho literal ao redor do gatilho.
#
# Não precisa do validador do Passo 4: o trecho já vem direto do texto-
# fonte, por construção — sempre "localizado".

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from app.extracao.tabela_itens import ItemTabela, normalizar_com_mapa

_CAMINHO_PADRAO_PALAVRAS_CHAVE = Path(__file__).parent / "palavras_chave_item.yaml"

# Ponto de fim de frase: NÃO conta se for seguido de outro dígito — evita
# cortar no meio de números como "2.000" ou "3.0CM" (separador de milhar ou
# decimal no formato brasileiro), que aparecem dentro das próprias
# descrições dos itens (confirmado no diagnóstico de amostra).
_PADRAO_PONTO_FRASE = re.compile(r"\.(?!\d)")


def carregar_palavras_chave(caminho: str | Path | None = None) -> dict[str, list[str]]:
    """Lê o YAML de categorias -> lista de termos. Arquivo editável sem
    mexer em código (mesmo padrão do keywords.yaml do Radar NexLicit)."""
    caminho_final = Path(caminho) if caminho else _CAMINHO_PADRAO_PALAVRAS_CHAVE
    with open(caminho_final, encoding="utf-8") as arquivo:
        dados = yaml.safe_load(arquivo)
    return dados or {}


def _extrair_trecho(texto_original: str, inicio: int, fim: int) -> str:
    """Frase ao redor do gatilho: do caractere seguinte ao último ponto
    final ANTES do gatilho até o próximo ponto final DEPOIS dele (ver
    _PADRAO_PONTO_FRASE pra o que conta como "ponto final")."""
    inicio_frase = 0
    for m in _PADRAO_PONTO_FRASE.finditer(texto_original, 0, inicio):
        inicio_frase = m.end()

    m_fim = _PADRAO_PONTO_FRASE.search(texto_original, fim)
    fim_frase = m_fim.end() if m_fim else len(texto_original)

    return texto_original[inicio_frase:fim_frase].strip()


def extrair_exigencias_do_item(
    item: ItemTabela, palavras_chave: dict[str, list[str]]
) -> list[dict[str, Any]]:
    """Procura cada categoria de palavra-chave no texto de UM item. Um item
    pode não ter nenhuma exigência, ter várias categorias diferentes, ou
    vários achados dentro da mesma categoria (trechos diferentes)."""
    texto_normalizado, mapa_pos = normalizar_com_mapa(item.texto)

    exigencias: list[dict[str, Any]] = []
    for categoria, termos in palavras_chave.items():
        # Termos normalizados, sem duplicata (ex.: "CATALOGO"/"CATÁLOGO"
        # viram o mesmo), do mais longo pro mais curto — assim um termo mais
        # específico (ex.: "CERTIFICADO DE BOAS PRATICAS") reserva a posição
        # antes de um termo mais genérico que é substring dele (ex.:
        # "CERTIFICADO") reportar a MESMA ocorrência de novo.
        termos_normalizados = sorted(
            {normalizar_com_mapa(termo)[0] for termo in termos}, key=len, reverse=True
        )

        spans_ja_encontrados: list[tuple[int, int]] = []

        def sobrepoe(inicio: int, fim: int) -> bool:
            return any(
                inicio < fim_ja and fim > inicio_ja
                for inicio_ja, fim_ja in spans_ja_encontrados
            )

        for termo_normalizado in termos_normalizados:
            for m in re.finditer(re.escape(termo_normalizado), texto_normalizado):
                if sobrepoe(m.start(), m.end()):
                    continue
                spans_ja_encontrados.append((m.start(), m.end()))

                inicio_original = mapa_pos[m.start()]
                fim_original = mapa_pos[m.end() - 1] + 1
                gatilho = item.texto[inicio_original:fim_original]
                trecho = _extrair_trecho(item.texto, inicio_original, fim_original)

                exigencias.append(
                    {
                        "numero_item": item.numero,
                        "categoria": categoria,
                        "gatilho": gatilho,
                        "trecho": trecho,
                        "pagina": item.pagina,
                        "localizador": item.localizador,
                    }
                )

    return exigencias


def extrair_exigencias_por_item(
    itens: list[ItemTabela], palavras_chave: dict[str, list[str]]
) -> list[dict[str, Any]]:
    exigencias: list[dict[str, Any]] = []
    for item in itens:
        exigencias.extend(extrair_exigencias_do_item(item, palavras_chave))
    return exigencias


def deduplicar_exigencias_item(exigencias: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Agrupa achados idênticos (mesmo numero_item + categoria + gatilho
    normalizado + trecho normalizado) dentro do mesmo item num só registro,
    contando quantas vezes apareceu em "ocorrencias_encontradas" — mesmo
    nome e espírito do campo do validador (Passo 4).

    O gatilho entra na chave pra não confundir duas coisas diferentes: uma
    exigência genuinamente duplicada no PDF de origem (mesmo gatilho, mesmo
    trecho, repetido porque a descrição inteira foi copiada duas vezes —
    ex.: item 280 do edital de Ouroeste) DEVE colapsar numa linha com
    ocorrencias_encontradas=2; já dois termos DIFERENTES da mesma categoria
    caindo na mesma frase (ex.: "MANUAL" e "CATÁLOGO", ambos
    documentacao_produto) NÃO são a mesma exigência só porque produzem o
    mesmo trecho — sem o gatilho na chave, eles se fundiam num registro só
    com contagem inflada (2 cópias × 2 termos = 4), misturando duplicação
    real do documento com coincidência de termos na mesma janela de texto.
    """
    agrupado: dict[tuple[int, str, str, str], dict[str, Any]] = {}
    ordem: list[tuple[int, str, str, str]] = []

    for exigencia in exigencias:
        chave_gatilho = normalizar_com_mapa(exigencia["gatilho"])[0]
        chave_trecho = normalizar_com_mapa(exigencia["trecho"])[0]
        chave = (exigencia["numero_item"], exigencia["categoria"], chave_gatilho, chave_trecho)
        if chave not in agrupado:
            agrupado[chave] = {**exigencia, "ocorrencias_encontradas": 0}
            ordem.append(chave)
        agrupado[chave]["ocorrencias_encontradas"] += 1

    return [agrupado[chave] for chave in ordem]
