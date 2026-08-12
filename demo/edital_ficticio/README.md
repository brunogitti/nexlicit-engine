# Edital fictício — material do demo público

`Edital_Ficticio_Pregao_01-2026_Exemplopolis.pdf` é o único edital que o
demo público (somente leitura) do NexLicit Engine mostra. **Nenhum dado
real**: Município de Exemplópolis, CNPJ 12.345.678/0001-90, servidor
"João da Silva Fictício" — tudo inventado. Toda página traz o aviso
"DOCUMENTO INTEIRAMENTE FICTÍCIO — GERADO PARA FINS DE DEMONSTRAÇÃO DO
NEXLICIT ENGINE. NÃO CORRESPONDE A NENHUM ÓRGÃO, PROCESSO, SERVIDOR OU
FORNECEDOR REAL."

Essa exigência (fictício, sem dado real de cliente) veio de uma decisão
anterior: um edital real (Toledo/PR) foi cogitado pro demo e descartado
justamente por não ser fictício — ver
[[toledo-edital-limite-tokens-por-minuto]] na memória do projeto para o
contexto completo dessa decisão.

## Como foi gerado

`gerar_edital_ficticio.py` monta o PDF do zero com PyMuPDF (`pymupdf`,
já uma dependência do projeto — nenhuma biblioteca nova). Pra regenerar:

```
python demo/edital_ficticio/gerar_edital_ficticio.py
```

Duas decisões de layout no script que não são só estética — ver os
comentários no topo do arquivo pra detalhe:

1. O aviso de "fictício" nunca fica colado direto acima de um título de
   anexo (existe uma linha curta "Anexos" entre os dois) — sem isso, o
   próprio aviso de segurança derruba o detector de blocos edital×TR da
   Camada 3 (`_parece_titulo`, em `app/inconsistencias/limite_tr.py`).
2. Nenhum travessão (`—` ou `–`) no texto, só hífen simples (`-`) — a
   fonte embutida do PyMuPDF substitui travessão por "·" silenciosamente.

## Divergência proposital

O item 6.1 do Anexo I (Termo de Referência) diz que a entrega é em até
**5 dias úteis**; a cláusula 9.1 do corpo do edital diz **10 dias
úteis**. É de propósito — sem isso, a Camada 3 (motor de
inconsistências) não teria nada pra encontrar num edital fictício escrito
de forma consistente, e o demo público não mostraria a funcionalidade em
ação. Confirmado ao vivo (pipeline completo, sem stub) que a IA encontra
e cita as duas cláusulas corretamente:

> Divergência quanto ao prazo de entrega dos materiais: o Edital
> estabelece prazo de até 10 dias úteis, enquanto o Termo de Referência
> estipula prazo máximo de 5 dias úteis.
> — trecho_edital: cláusula 9.1 (pág. 4) · trecho_tr: item 6.1 (pág. 5)

## Resultado esperado rodando o pipeline completo

- Checklist: 23 exigências, todas com confiança "localizado".
- Requisitos por item (Passo 8, sem IA): 2 (itens 1 e 4, únicos com
  "registro na ANVISA obrigatório" no texto da tabela).
- Camada 3: `comparacao_possivel = True`, 1 achado (tipo "prazo", acima).

Custo: 3 chamadas de IA por rodada completa (1 checklist + 2 da execução
dupla da Camada 3) — edital pequeno (6 páginas), sem risco de 429/503 por
tamanho.
