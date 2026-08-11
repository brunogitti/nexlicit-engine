// Interatividade da seção de perguntas em linguagem natural (Fase 2,
// Camada 2). Chama POST /processos/{id}/perguntar (Camada 1, já validada
// contra 7 editais reais, sem alucinação em nenhum caso adversarial) e
// mostra a resposta reaproveitando o mesmo estilo visual de citação
// (.trecho) já usado no checklist de exigências — mantém a linguagem
// visual de "isto é conferível" consistente na tela inteira.
//
// Sem histórico persistido no banco (decisão da Camada 2): cada pergunta
// feita nesta sessão de página entra no TOPO da lista, só em memória do
// navegador — recarregar a página limpa tudo, de propósito.
//
// Nunca usa innerHTML com texto vindo da API/usuário: todo conteúdo
// dinâmico é montado via createElement + textContent, então não tem risco
// de HTML injetado (nem precisa de função de escape separada).

(function () {
  const secao = document.getElementById('secao-perguntas');
  if (!secao) return; // segurança: se o template mudar e a seção sumir, não quebra o resto da página

  const processoId = secao.dataset.processoId;
  const formulario = document.getElementById('form-pergunta');
  const campoPergunta = document.getElementById('campo-pergunta');
  const carregando = document.getElementById('pergunta-carregando');
  const avisoErro = document.getElementById('aviso-erro-pergunta');
  const lista = document.getElementById('lista-pergunta-resposta');
  const botaoEnviar = formulario.querySelector('button[type="submit"]');

  function criarElemento(tag, opcoes) {
    const elemento = document.createElement(tag);
    if (opcoes && opcoes.className) elemento.className = opcoes.className;
    if (opcoes && opcoes.texto !== undefined) elemento.textContent = opcoes.texto;
    return elemento;
  }

  function mostrarErro(mensagem) {
    avisoErro.textContent = mensagem;
    avisoErro.hidden = false;
  }

  function esconderErro() {
    avisoErro.hidden = true;
    avisoErro.textContent = '';
  }

  // Cabeçalho comum aos três tipos de card (encontrada / não encontrada /
  // contexto grande demais): a pergunta feita sempre aparece, não importa
  // o resultado.
  function montarCabecalhoPergunta(pergunta) {
    const fragmento = document.createDocumentFragment();
    fragmento.appendChild(criarElemento('span', { className: 'eyebrow', texto: 'Pergunta' }));
    fragmento.appendChild(criarElemento('p', { className: 'pergunta-feita', texto: pergunta }));
    return fragmento;
  }

  function montarCardEncontrada(pergunta, dados) {
    const card = criarElemento('div', { className: 'card-pergunta-resposta' });
    card.appendChild(montarCabecalhoPergunta(pergunta));

    const bloco = document.createElement('blockquote');
    bloco.className = 'trecho';
    bloco.appendChild(criarElemento('p', { texto: dados.resposta }));

    const paginas = dados.paginas && dados.paginas.length ? dados.paginas.join(', ') : '—';
    bloco.appendChild(criarElemento('cite', { texto: 'Páginas: ' + paginas }));

    card.appendChild(bloco);
    return card;
  }

  function montarCardNaoEncontrada(pergunta, dados) {
    const card = criarElemento('div', { className: 'card-pergunta-resposta' });
    card.appendChild(montarCabecalhoPergunta(pergunta));

    const bloco = criarElemento('div', { className: 'resposta-nao-encontrada' });
    bloco.appendChild(criarElemento('span', { className: 'eyebrow eyebrow-stamp', texto: 'Não localizado no edital' }));
    bloco.appendChild(criarElemento('p', { texto: dados.resposta }));

    card.appendChild(bloco);
    return card;
  }

  function montarCardContextoGrande(pergunta, mensagem) {
    const card = criarElemento('div', { className: 'card-pergunta-resposta' });
    card.appendChild(montarCabecalhoPergunta(pergunta));

    const bloco = criarElemento('div', { className: 'aviso-limitacao' });
    bloco.appendChild(criarElemento('span', { className: 'eyebrow', texto: 'Edital grande demais' }));
    bloco.appendChild(criarElemento('p', { texto: mensagem }));

    card.appendChild(bloco);
    return card;
  }

  async function lerCorpoJson(resposta) {
    try {
      return await resposta.json();
    } catch (erro) {
      return {};
    }
  }

  formulario.addEventListener('submit', async function (evento) {
    evento.preventDefault();
    const pergunta = campoPergunta.value.trim();
    if (!pergunta) return;

    esconderErro();
    carregando.hidden = false;
    campoPergunta.disabled = true;
    botaoEnviar.disabled = true;

    try {
      const resposta = await fetch('/processos/' + processoId + '/perguntar', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pergunta: pergunta }),
      });

      if (resposta.status === 413) {
        // Limitação conhecida (edital grande demais), não erro de sistema
        // nem resposta negativa — card próprio, ver montarCardContextoGrande.
        const corpo = await lerCorpoJson(resposta);
        lista.prepend(montarCardContextoGrande(
          pergunta,
          corpo.detail || 'Este edital é grande demais para esta função responder de uma vez, nesta versão.'
        ));
        campoPergunta.value = '';
      } else if (!resposta.ok) {
        // Qualquer outro erro (400/404/500/502/501...) é tratado como falha
        // de sistema genérica — a mensagem do backend (corpo.detail) já é
        // clara o bastante nesses casos, não precisa de tradução por tipo.
        const corpo = await lerCorpoJson(resposta);
        mostrarErro(corpo.detail || 'Não foi possível obter a resposta, tente de novo.');
      } else {
        const dados = await resposta.json();
        lista.prepend(
          dados.encontrado
            ? montarCardEncontrada(pergunta, dados)
            : montarCardNaoEncontrada(pergunta, dados)
        );
        campoPergunta.value = '';
      }
    } catch (erro) {
      // Falha de rede (fetch rejeitou antes de chegar a ter resposta HTTP).
      mostrarErro('Não foi possível obter a resposta, tente de novo.');
    } finally {
      carregando.hidden = true;
      campoPergunta.disabled = false;
      botaoEnviar.disabled = false;
      campoPergunta.focus();
    }
  });
})();
