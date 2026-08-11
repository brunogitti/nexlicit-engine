// Interatividade da seção de inconsistências edital-vs-TR (Fase 2, Camada
// 2). Diferente de perguntas.js, esta seção não monta nada dinamicamente
// no DOM — o resultado é PERSISTIDO (tabela inconsistencia + colunas
// inconsistencias_* em processo) e o servidor já renderiza o estado certo
// no HTML. O botão só dispara uma verificação nova e recarrega a página
// pra pegar o estado atualizado do servidor — mais simples e mais
// confiável do que duplicar a lógica de agrupar por categoria aqui em JS
// também.

(function () {
  const secao = document.getElementById('secao-inconsistencias');
  if (!secao) return; // segurança: se o template mudar e a seção sumir, não quebra o resto da página

  const processoId = secao.dataset.processoId;
  const botao = document.getElementById('btn-verificar-inconsistencias');
  const carregando = document.getElementById('inconsistencias-carregando');
  const avisoErro = document.getElementById('aviso-erro-inconsistencias');

  if (!botao) return;

  botao.addEventListener('click', async function () {
    avisoErro.hidden = true;
    avisoErro.textContent = '';
    carregando.hidden = false;
    botao.disabled = true;

    try {
      const resposta = await fetch('/processos/' + processoId + '/detectar-inconsistencias', {
        method: 'POST',
      });

      if (!resposta.ok) {
        let corpo = {};
        try { corpo = await resposta.json(); } catch (erro) { /* corpo sem JSON */ }
        throw new Error(corpo.detail || 'Não foi possível verificar as inconsistências, tente de novo.');
      }

      // Sucesso: recarrega a página pra o servidor renderizar o estado
      // novo (achados agrupados por categoria, ou "sem achados", etc.) —
      // não tenta reconstruir isso em JS.
      window.location.reload();
    } catch (erro) {
      carregando.hidden = true;
      botao.disabled = false;
      avisoErro.textContent = erro.message;
      avisoErro.hidden = false;
    }
  });
})();
