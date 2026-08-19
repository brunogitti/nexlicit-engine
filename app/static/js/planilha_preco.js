// Interatividade da planilha de preço (Fase 4, Camada 1, decisão B --
// 16/08/2026): salva quantidade/preço de um item ao sair do campo
// (blur) -- mesmo padrão de salvarObservacao() em checklist.js. Salvar
// linha a linha, não um formulário só com todos os itens de uma vez,
// porque um edital grande (ex.: Ouroeste, 584 itens) tornaria um envio
// único lento e arriscado -- se falhar no meio, não dá pra saber o que
// já tinha sido salvo antes.

(function () {
  const tabela = document.getElementById('tabela-planilha-preco');
  if (!tabela) return; // sem catálogo nesta tela, nada a fazer

  const processoId = tabela.dataset.processoId;
  const avisoErro = document.getElementById('aviso-salvamento-preco');
  let avisoTimer = null;

  function mostrarAvisoDeErro() {
    avisoErro.hidden = false;
    clearTimeout(avisoTimer);
    avisoTimer = setTimeout(function () { avisoErro.hidden = true; }, 5000);
  }

  // Campo vazio vira "null" (sem valor nenhum), não zero -- diferença
  // que importa pra planilha saber a diferença entre "não preenchido"
  // (célula vazia/destacada) e "preenchido como zero" (célula com 0).
  function valorOuNulo(campo) {
    const texto = campo.value.trim();
    return texto === '' ? null : parseFloat(texto);
  }

  async function salvarPreco(linha) {
    const numeroItem = linha.dataset.numeroItem;
    const dados = {
      quantidade: valorOuNulo(linha.querySelector('.campo-quantidade')),
      preco_unitario: valorOuNulo(linha.querySelector('.campo-preco-unitario')),
    };

    try {
      const resposta = await fetch('/processos/' + processoId + '/itens/' + numeroItem + '/preco', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(dados),
      });
      if (!resposta.ok) {
        throw new Error('falha ao salvar preço do item ' + numeroItem);
      }
    } catch (erro) {
      mostrarAvisoDeErro();
    }
  }

  tabela.querySelectorAll('.campo-quantidade, .campo-preco-unitario').forEach(function (campo) {
    campo.addEventListener('blur', function () {
      salvarPreco(campo.closest('tr'));
    });
  });
})();
