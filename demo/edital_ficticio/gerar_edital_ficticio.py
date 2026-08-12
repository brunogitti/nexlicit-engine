# Gera o edital fictício de Exemplópolis (Pregão Eletrônico nº 01/2026,
# Lei 14.133/2021) usado como conteúdo fixo do demo público do NexLicit
# Engine. Nenhum dado real -- município, CNPJ, servidor e valores são
# inventados; aviso "DOCUMENTO INTEIRAMENTE FICTÍCIO" em toda página.
#
# Duas decisões de layout que não são só estética:
#
# 1. Nas páginas de anexo (5 e 6), o aviso vermelho de "fictício" NÃO fica
#    colado direto acima do título "ANEXO I - TERMO DE REFERÊNCIA" /
#    "ANEXO II - ..." -- existe uma linha curta ("Anexos") entre os dois.
#    Sem isso, o filtro de "linha anterior comprida demais" da Camada 0 do
#    motor de inconsistências (_parece_titulo, em
#    app/inconsistencias/limite_tr.py) rejeita o título verdadeiro,
#    achando que é citação de cláusula -- o próprio aviso de segurança
#    derrubava a detecção. O aviso continua com a mesma frase forte, só
#    reposicionado.
#
# 2. Nenhum travessão ("-", "--") nos textos -- só hífen simples ("-").
#    A fonte embutida do PyMuPDF (Helvetica base14, sem encoding
#    Unicode completo) substitui esses caracteres por "·" silenciosamente.
#
# A divergência de prazo entre a cláusula 9.1 (corpo do edital, 10 dias
# úteis) e o item 6.1 do Anexo I (5 dias úteis) é PROPOSITAL -- existe só
# pra o demo público mostrar a Camada 3 (motor de inconsistências)
# encontrando um achado de verdade, no mesmo padrão de divergência já
# visto em editais reais de teste (mesma informação repetida em dois
# lugares, só o número mudando). Confirmado ao vivo (ver
# demo/edital_ficticio/README.md) que a IA encontra e cita as duas
# cláusulas corretamente.
#
# Pra regenerar: python gerar_edital_ficticio.py (a partir desta pasta,
# ou ajustando CAMINHO_SAIDA abaixo). Depende só de PyMuPDF (pymupdf),
# já uma dependência do projeto -- nenhuma biblioteca nova.
import os
import fitz

CAMINHO_SAIDA = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "Edital_Ficticio_Pregao_01-2026_Exemplopolis.pdf",
)

LARGURA, ALTURA = fitz.paper_size("a4")
MARGEM_X = 56
Y_TOPO = 40
Y_RODAPE = ALTURA - 30

BANNER_L1 = "DOCUMENTO INTEIRAMENTE FICTÍCIO - GERADO PARA FINS DE DEMONSTRAÇÃO DO NEXLICIT ENGINE."
BANNER_L2 = "NÃO CORRESPONDE A NENHUM ÓRGÃO, PROCESSO, SERVIDOR OU FORNECEDOR REAL."
VERMELHO = (0.72, 0.11, 0.11)
CINZA = (0.45, 0.45, 0.45)
PRETO = (0, 0, 0)


class Escritor:
    """Cursor vertical simples por página -- cada método soma texto e avança
    "y". Não tenta ser um motor de layout genérico, só o suficiente pra
    recriar as 6 páginas deste edital com controle total de onde cada
    linha cai (importante pra não repetir a colisão que estamos
    corrigindo)."""

    def __init__(self, doc: fitz.Document):
        self.doc = doc
        self.pagina: fitz.Page | None = None
        self.y = Y_TOPO
        self.numero_pagina = 0

    def nova_pagina(self):
        self.pagina = self.doc.new_page(width=LARGURA, height=ALTURA)
        self.numero_pagina += 1
        self.y = Y_TOPO
        self._banner()
        self._rodape()

    def _banner(self):
        assert self.pagina is not None
        self.pagina.insert_text((MARGEM_X, self.y), BANNER_L1, fontsize=8, fontname="hebo", color=VERMELHO)
        self.y += 11
        self.pagina.insert_text((MARGEM_X, self.y), BANNER_L2, fontsize=8, fontname="hebo", color=VERMELHO)
        self.y += 26

    def _rodape(self):
        assert self.pagina is not None
        texto = f"Documento fictício - NexLicit Engine (demo) · página {self.numero_pagina}"
        self.pagina.insert_text((MARGEM_X, Y_RODAPE), texto, fontsize=8, fontname="helv", color=CINZA)

    def _garante_espaco(self, altura_necessaria: float):
        if self.pagina is None or self.y + altura_necessaria > Y_RODAPE - 20:
            self.nova_pagina()

    def titulo(self, texto: str, tamanho: float = 13, centralizado: bool = True, espaco_antes: float = 0):
        self._garante_espaco(tamanho + 10 + espaco_antes)
        self.y += espaco_antes
        assert self.pagina is not None
        if centralizado:
            largura_texto = fitz.get_text_length(texto, fontname="hebo", fontsize=tamanho)
            x = (LARGURA - largura_texto) / 2
        else:
            x = MARGEM_X
        self.pagina.insert_text((x, self.y), texto, fontsize=tamanho, fontname="hebo", color=PRETO)
        self.y += tamanho + 10

    def subtitulo_centralizado(self, texto: str, tamanho: float = 10):
        self._garante_espaco(tamanho + 14)
        assert self.pagina is not None
        largura_texto = fitz.get_text_length(texto, fontname="helv", fontsize=tamanho)
        x = (LARGURA - largura_texto) / 2
        self.pagina.insert_text((x, self.y), texto, fontsize=tamanho, fontname="helv", color=PRETO)
        self.y += tamanho + 14

    def divisor_curto(self, texto: str, tamanho: float = 11, espaco_antes: float = 8):
        """Linha curta e isolada (ex.: "Anexos") -- usada de propósito nas
        páginas de anexo pra separar o aviso de fictício do título do
        anexo, sem mudar o texto do aviso em si."""
        self._garante_espaco(tamanho + 16 + espaco_antes)
        self.y += espaco_antes
        assert self.pagina is not None
        self.pagina.insert_text((MARGEM_X, self.y), texto, fontsize=tamanho, fontname="hebo", color=PRETO)
        self.y += tamanho + 12

    def paragrafo(self, texto: str, tamanho: float = 10, espaco_antes: float = 6):
        largura_util = LARGURA - 2 * MARGEM_X
        linhas = _quebrar_linhas(texto, largura_util, "helv", tamanho)
        altura = len(linhas) * (tamanho + 3) + espaco_antes
        self._garante_espaco(altura)
        self.y += espaco_antes
        assert self.pagina is not None
        for linha in linhas:
            self.pagina.insert_text((MARGEM_X, self.y), linha, fontsize=tamanho, fontname="helv", color=PRETO)
            self.y += tamanho + 3

    def tabela_campos(self, linhas: list[tuple[str, str]], tamanho: float = 9.5):
        altura_linha = 22
        largura_col1 = 190
        largura_total = LARGURA - 2 * MARGEM_X
        self._garante_espaco(len(linhas) * altura_linha + 10)
        self.y += 6
        assert self.pagina is not None
        x0 = MARGEM_X
        for rotulo, valor in linhas:
            retangulo = fitz.Rect(x0, self.y, x0 + largura_total, self.y + altura_linha)
            self.pagina.draw_rect(retangulo, color=(0.6, 0.6, 0.6), width=0.6)
            self.pagina.draw_line((x0 + largura_col1, self.y), (x0 + largura_col1, self.y + altura_linha), color=(0.6, 0.6, 0.6), width=0.6)
            self.pagina.insert_text((x0 + 6, self.y + 14.5), rotulo, fontsize=tamanho, fontname="helv", color=PRETO)
            self.pagina.insert_text((x0 + largura_col1 + 6, self.y + 14.5), valor, fontsize=tamanho, fontname="helv", color=PRETO)
            self.y += altura_linha

    def tabela_itens(self, cabecalho: list[str], linhas: list[list[str]], larguras: list[float], tamanho: float = 8.5):
        altura_linha = 26
        self._garante_espaco((len(linhas) + 1) * altura_linha + 10)
        self.y += 6
        assert self.pagina is not None
        x0 = MARGEM_X
        # cabeçalho
        x = x0
        largura_total = sum(larguras)
        retangulo = fitz.Rect(x0, self.y, x0 + largura_total, self.y + altura_linha)
        self.pagina.draw_rect(retangulo, color=(0.6, 0.6, 0.6), width=0.6, fill=(0.92, 0.92, 0.92))
        for titulo, largura in zip(cabecalho, larguras):
            self.pagina.insert_text((x + 4, self.y + 16), titulo, fontsize=tamanho, fontname="hebo", color=PRETO)
            x += largura
        self.y += altura_linha
        for linha in linhas:
            x = x0
            retangulo = fitz.Rect(x0, self.y, x0 + largura_total, self.y + altura_linha)
            self.pagina.draw_rect(retangulo, color=(0.6, 0.6, 0.6), width=0.6)
            for celula, largura in zip(linha, larguras):
                texto_quebrado = _quebrar_linhas(celula, largura - 8, "helv", tamanho)
                y_cel = self.y + 10
                for sublinha in texto_quebrado[:2]:
                    self.pagina.insert_text((x + 4, y_cel), sublinha, fontsize=tamanho, fontname="helv", color=PRETO)
                    y_cel += tamanho + 2
                x += largura
            self.y += altura_linha


def _quebrar_linhas(texto: str, largura_max: float, fontname: str, tamanho: float) -> list[str]:
    palavras = texto.split(" ")
    linhas: list[str] = []
    atual = ""
    for palavra in palavras:
        candidata = f"{atual} {palavra}".strip()
        if fitz.get_text_length(candidata, fontname=fontname, fontsize=tamanho) <= largura_max:
            atual = candidata
        else:
            if atual:
                linhas.append(atual)
            atual = palavra
    if atual:
        linhas.append(atual)
    return linhas or [""]


def montar() -> fitz.Document:
    doc = fitz.open()
    w = Escritor(doc)
    w.nova_pagina()

    # ---------------- página 1: capa + quadro-resumo ----------------
    w.titulo("MUNICÍPIO DE EXEMPLÓPOLIS", tamanho=15)
    w.subtitulo_centralizado("SECRETARIA MUNICIPAL DE ADMINISTRAÇÃO")
    w.titulo("EDITAL DE PREGÃO ELETRÔNICO Nº 01/2026", tamanho=12, espaco_antes=6)
    w.titulo("PROCESSO ADMINISTRATIVO Nº 1000/2026", tamanho=12)
    w.paragrafo(
        "O MUNICÍPIO DE EXEMPLÓPOLIS, pessoa jurídica de direito público interno, inscrito no CNPJ "
        "sob nº 12.345.678/0001-90, torna público, para conhecimento dos interessados, que fará "
        "realizar licitação na modalidade PREGÃO ELETRÔNICO, do tipo MENOR PREÇO POR ITEM, sob o "
        "regime da Lei Federal nº 14.133, de 1º de abril de 2021, e demais normas pertinentes, "
        "mediante as condições estabelecidas neste Edital e seus Anexos.",
        espaco_antes=14,
    )
    w.tabela_campos([
        ("Órgão", "MUNICÍPIO DE EXEMPLÓPOLIS"),
        ("Objeto", "Aquisição de materiais de limpeza e higienização"),
        ("Modalidade", "Pregão Eletrônico"),
        ("Critério de julgamento", "Menor preço por item"),
        ("Valor estimado", "R$ 87.450,00"),
        ("Data limite para recebimento de propostas", "01/09/2026, às 08h30"),
        ("Data e horário da sessão pública", "01/09/2026, às 09h00"),
        ("Plataforma", "www.exemplopregao.com.br (fictícia)"),
        ("Prazo de validade da proposta", "60 (sessenta) dias"),
    ])

    # ---------------- página 2: seções 1-4 ----------------
    w.nova_pagina()
    w.divisor_curto("1. DO OBJETO")
    w.paragrafo(
        "1.1. O presente Pregão tem por objeto a aquisição de materiais de limpeza e higienização "
        "para atendimento das unidades administrativas do Município de Exemplópolis, conforme "
        "especificações e quantitativos constantes do Anexo I - Termo de Referência."
    )
    w.paragrafo(
        "1.2. Em caso de discordância entre as especificações deste objeto descritas no sistema "
        "eletrônico de compras e as especificações constantes do Anexo I, prevalecerão as últimas."
    )
    w.divisor_curto("2. DA PARTICIPAÇÃO NA LICITAÇÃO")
    w.paragrafo(
        "2.1. Poderão participar deste Pregão os interessados que atenderem a todas as exigências, "
        "inclusive quanto à documentação, constantes deste Edital e seus Anexos."
    )
    w.paragrafo(
        "2.2. Não poderão participar desta licitação os interessados que se enquadrem nas vedações "
        "previstas no art. 14 da Lei nº 14.133/2021."
    )
    w.divisor_curto("3. DA HABILITAÇÃO JURÍDICA")
    w.paragrafo("3.1. Para fins de habilitação jurídica, o licitante deverá apresentar, conforme o caso:")
    for letra, texto in [
        ("a", "registro comercial, no caso de empresa individual;"),
        ("b", "ato constitutivo, estatuto ou contrato social em vigor, devidamente registrado na Junta "
              "Comercial, em se tratando de sociedades comerciais, acompanhado de documento comprobatório "
              "de seus administradores;"),
        ("c", "documentos de eleição dos atuais administradores, tratando-se de sociedades por ações, "
              "acompanhados da documentação mencionada na alínea 'b';"),
        ("d", "ato constitutivo devidamente registrado no Registro Civil de Pessoas Jurídicas, "
              "tratando-se de sociedade simples, acompanhado de prova da diretoria em exercício;"),
        ("e", "decreto de autorização, tratando-se de empresa ou sociedade estrangeira em funcionamento "
              "no país, e ato de registro ou autorização para funcionamento expedido pelo órgão "
              "competente, quando a atividade assim o exigir;"),
        ("f", "prova de inscrição no Cadastro Nacional de Pessoas Jurídicas (CNPJ)."),
    ]:
        w.paragrafo(f"{letra}) {texto}", espaco_antes=3)
    w.divisor_curto("4. DA HABILITAÇÃO FISCAL, SOCIAL E TRABALHISTA")
    w.paragrafo("4.1. Para fins de habilitação fiscal, social e trabalhista, o licitante deverá apresentar:")
    for letra, texto in [
        ("a", "prova de inscrição no cadastro de contribuintes estadual e/ou municipal, relativo ao "
              "domicílio ou sede do licitante, pertinente ao seu ramo de atividade;"),
        ("b", "prova de regularidade fiscal perante a Fazenda Nacional, mediante certidão expedida "
              "conjuntamente pela Secretaria Especial da Receita Federal do Brasil e pela "
              "Procuradoria-Geral da Fazenda Nacional;"),
        ("c", "prova de regularidade com a Fazenda Estadual do domicílio ou sede do licitante;"),
        ("d", "prova de regularidade relativa ao Fundo de Garantia do Tempo de Serviço (FGTS), "
              "demonstrando situação regular no cumprimento dos encargos sociais;"),
        ("e", "prova de inexistência de débitos inadimplidos perante a Justiça do Trabalho, mediante "
              "Certidão Negativa de Débitos Trabalhistas (CNDT) ou Positiva com efeitos de Negativa."),
    ]:
        w.paragrafo(f"{letra}) {texto}", espaco_antes=3)

    # ---------------- página 3: seções 5-6 ----------------
    w.nova_pagina()
    w.divisor_curto("5. DA QUALIFICAÇÃO ECONÔMICO-FINANCEIRA")
    w.paragrafo(
        "5.1. Certidão negativa de feitos sobre falência, recuperação judicial ou extrajudicial, "
        "expedida pelo distribuidor da sede do licitante, com data de expedição não superior a 90 "
        "(noventa) dias da data de abertura do certame, se outro prazo não constar do próprio "
        "documento."
    )
    w.divisor_curto("6. DA QUALIFICAÇÃO TÉCNICA")
    w.paragrafo(
        "6.1. Os itens constantes do Anexo I cujos produtos sejam classificados como saneantes "
        "deverão ter registro ou notificação vigente junto à Agência Nacional de Vigilância "
        "Sanitária (ANVISA), a ser comprovado no momento da entrega."
    )

    # ---------------- página 4: seções 7-10 ----------------
    w.nova_pagina()
    w.divisor_curto("7. DAS DECLARAÇÕES EXIGIDAS")
    w.paragrafo("7.1. O licitante, ao registrar sua proposta no sistema eletrônico, declarará, sob as penas da lei, que:")
    for letra, texto in [
        ("a", "não emprega menor de 18 (dezoito) anos em trabalho noturno, perigoso ou insalubre, nem "
              "menor de 16 (dezesseis) anos em qualquer trabalho, salvo na condição de aprendiz, a "
              "partir de 14 (quatorze) anos, nos termos do art. 7º, XXXIII, da Constituição Federal;"),
        ("b", "cumpre as exigências de reserva de cargos para pessoa com deficiência e para reabilitado "
              "da Previdência Social, previstas em lei e em outras normas específicas, conforme art. "
              "63, IV, da Lei nº 14.133/2021;"),
        ("c", "sua proposta econômica compreende a integralidade dos custos para atendimento dos "
              "direitos trabalhistas assegurados na Constituição Federal, nas leis trabalhistas e nas "
              "convenções coletivas de trabalho, conforme art. 63, §1º, da Lei nº 14.133/2021;"),
        ("d", "não se enquadra em nenhuma das hipóteses de impedimento previstas no art. 14 da Lei nº "
              "14.133/2021;"),
        ("e", "cumpre plenamente os requisitos de habilitação definidos neste Edital."),
    ]:
        w.paragrafo(f"{letra}) {texto}", espaco_antes=3)
    w.divisor_curto("8. DOS REQUISITOS DA PROPOSTA")
    w.paragrafo(
        "8.1. A proposta deverá ser enviada exclusivamente por meio do sistema eletrônico, indicando "
        "marca, fabricante e modelo dos itens ofertados."
    )
    w.paragrafo(
        "8.2. Os preços deverão ser cotados em moeda corrente nacional, com até duas casas decimais, "
        "incluindo todas as despesas necessárias ao fornecimento do objeto."
    )
    w.paragrafo(
        "8.3. O prazo de validade da proposta não poderá ser inferior a 60 (sessenta) dias, contados "
        "da data de abertura da sessão pública."
    )
    w.paragrafo(
        "8.4. O licitante provisoriamente vencedor deverá apresentar, quando solicitado pelo "
        "Pregoeiro, catálogo, ficha técnica ou folder do produto ofertado, a fim de comprovar a "
        "conformidade com as especificações do Anexo I."
    )
    w.divisor_curto("9. DA ENTREGA E DO PAGAMENTO")
    w.paragrafo(
        "9.1. O prazo de entrega dos materiais será de até 10 (dez) dias úteis, contados do "
        "recebimento da respectiva Ordem de Fornecimento."
    )
    w.paragrafo(
        "9.2. O pagamento será efetuado em até 30 (trinta) dias, contados da entrega do objeto e do "
        "ateste da respectiva Nota Fiscal."
    )
    w.divisor_curto("10. DAS SANÇÕES ADMINISTRATIVAS")
    w.paragrafo(
        "10.1. Comete infração administrativa, nos termos da Lei nº 14.133/2021, o licitante que "
        "deixar de entregar a documentação exigida, não mantiver a proposta, ou não celebrar o "
        "contrato quando convocado dentro do prazo de validade de sua proposta, sujeitando-se às "
        "penalidades previstas em lei, sem prejuízo da responsabilização civil e criminal cabível."
    )

    # ---------------- página 5: Anexo I ----------------
    w.nova_pagina()
    w.divisor_curto("Anexos")  # <- linha curta de propósito, ver docstring do módulo
    w.titulo("ANEXO I - TERMO DE REFERÊNCIA", tamanho=13)
    w.divisor_curto("1. OBJETO")
    w.paragrafo(
        "Aquisição de materiais de limpeza e higienização para as unidades administrativas do "
        "Município de Exemplópolis, conforme quantitativos e especificações a seguir."
    )
    w.divisor_curto("2. ITENS")
    cabecalho = ["Item", "Descrição", "Unid.", "Qtd.", "Valor unit. (R$)"]
    larguras = [28.0, 300.0, 40.0, 40.0, 75.0]
    itens = [
        ["1", "Água sanitária, cloro ativo 2,0 a 2,5%, frasco de 5 litros, registro na ANVISA obrigatório", "UN", "300", "12,50"],
        ["2", "Detergente neutro, concentração mínima 5% de tensoativos aniônicos, frasco de 500 ml", "UN", "500", "3,20"],
        ["3", "Papel toalha interfolha, folha dupla, pacote com 1.000 unidades", "PCT", "200", "18,90"],
        ["4", "Sabonete líquido antisséptico, pH neutro, frasco de 500 ml, registro na ANVISA obrigatório", "UN", "250", "9,80"],
        ["5", "Luva de procedimento, látex natural, tamanho M, caixa com 100 unidades", "CX", "150", "24,00"],
        ["6", "Desinfetante de uso geral, ação bactericida comprovada, frasco de 1 litro", "UN", "300", "8,40"],
        ["7", "Saco para lixo, capacidade 100 litros, reforçado, pacote com 100 unidades", "PCT", "180", "32,00"],
    ]
    w.tabela_itens(cabecalho, itens, larguras)
    w.divisor_curto("3. AMOSTRA")
    w.paragrafo(
        "3.1. Para os itens 1, 4 e 6, a licitante provisoriamente classificada em primeiro lugar "
        "deverá apresentar amostra do produto ofertado, no prazo de 3 (três) dias úteis contados da "
        "solicitação do Pregoeiro, para fins de verificação da conformidade com as especificações "
        "constantes deste Termo de Referência."
    )
    w.divisor_curto("4. GARANTIA")
    w.paragrafo("4.1. Não será exigida a prestação de garantia contratual para o objeto desta licitação.")
    w.divisor_curto("5. FISCALIZAÇÃO")
    w.paragrafo(
        "5.1. A fiscalização do contrato ficará a cargo do servidor João da Silva Fictício, matrícula "
        "00000, lotado na Secretaria Municipal de Administração."
    )
    w.divisor_curto("6. PRAZO DE ENTREGA")
    w.paragrafo(
        "6.1. Os materiais deverão ser entregues no prazo máximo de 5 (cinco) dias úteis, contados do "
        "recebimento da Ordem de Fornecimento, no almoxarifado central da Secretaria Municipal de "
        "Administração."
    )
    # Divergência PROPOSITAL com a cláusula 9.1 do corpo do edital (10 dias
    # úteis) -- mesmo padrão real já visto em editais de teste (a mesma
    # informação repetida em dois lugares, só o número mudando). Existe só
    # pra o demo público mostrar a Camada 3 (motor de inconsistências)
    # encontrando um achado de verdade, sem inventar um caso artificial
    # demais.

    # ---------------- página 6: Anexo II ----------------
    w.nova_pagina()
    w.divisor_curto("Anexos")  # mesma correção da página 5
    w.titulo("ANEXO II - MODELO DE DECLARAÇÃO UNIFICADA", tamanho=12)
    w.paragrafo(
        "A empresa (razão social fictícia), inscrita no CNPJ sob nº (fictício), por intermédio de "
        "seu representante legal, DECLARA, para fins do disposto no Edital de Pregão Eletrônico nº "
        "01/2026:"
    )
    for letra, texto in [
        ("a", "que não emprega menor de 18 anos em trabalho noturno, perigoso ou insalubre, e não "
              "emprega menor de 16 anos, salvo na condição de aprendiz, a partir de 14 anos, nos "
              "termos do art. 7º, XXXIII, da Constituição Federal;"),
        ("b", "que cumpre as exigências de reserva de cargos para pessoa com deficiência e para "
              "reabilitado da Previdência Social;"),
        ("c", "que sua proposta econômica compreende a integralidade dos custos para atendimento dos "
              "direitos trabalhistas;"),
        ("d", "que não se enquadra em nenhuma das hipóteses de impedimento previstas no art. 14 da "
              "Lei nº 14.133/2021;"),
        ("e", "que cumpre plenamente os requisitos de habilitação definidos neste Edital."),
    ]:
        w.paragrafo(f"{letra}) {texto}", espaco_antes=3)
    w.paragrafo("Exemplópolis, ___ de ___________ de 2026.", espaco_antes=20)
    w.paragrafo("____________________________________", espaco_antes=30)
    w.paragrafo("Representante legal (assinatura fictícia)", espaco_antes=2)

    return doc


if __name__ == "__main__":
    documento = montar()
    documento.save(CAMINHO_SAIDA)
    documento.close()
    print(f"Gerado: {CAMINHO_SAIDA} ({fitz.open(CAMINHO_SAIDA).page_count} páginas)")
