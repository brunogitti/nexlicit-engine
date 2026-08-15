// Interatividade da tela de checklist (Passo 7): marca/desmarca exigência e
// salva observação via PATCH /exigencias/{id} (rota já existe, Passo 6),
// atualizando a barra de progresso na tela sem recarregar a página. Erro de
// rede mostra um aviso visível — nunca falha silenciosamente.
//
// Passo 9 (Mudança 3): um card pode representar VÁRIAS exigências ao mesmo
// tempo — o grupo de hipóteses alternativas (ex.: documento constitutivo por
// tipo societário) tem um checkbox só, mas cada hipótese continua sendo uma
// linha própria no banco. Por isso todo card usa "data-exigencia-ids", uma
// lista separada por vírgula (card avulso é só uma lista de 1 id) — assim o
// resto do código (contar progresso, marcar, salvar nota) usa um caminho só,
// sem precisar de "if é grupo, if não é".

(function () {
  const avisoErro = document.getElementById('aviso-salvamento');
  const barraPreenchida = document.getElementById('barra-preenchida');
  const progressoTexto = document.getElementById('progresso-texto');
  // Só os cards com checkbox de verdade contam como "exigência conferível"
  // — os cards de Requisitos por Item (Passo 8) têm a classe card-exigencia
  // também (pro visual combinar), mas não têm checkbox nenhum, então não
  // devem entrar nessa conta. Por isso a lista vem de .checkbox-exigencia,
  // não de .card-exigencia.
  const checkboxes = Array.from(document.querySelectorAll('.checkbox-exigencia'));

  // BUG CORRIGIDO (15/08/2026): "data-exigencia-ids" só existe no
  // <article class="card-exigencia"> (o card pai) -- nunca esteve no
  // <input> em si, provavelmente desde a Mudança 3 (agrupamento de
  // hipóteses), quando o atributo virou compartilhado pelo card em vez
  // de exclusivo do checkbox. Ler "checkbox.dataset.exigenciaIds"
  // direto sempre dava undefined e quebrava aqui (TypeError, antes de
  // qualquer PATCH ser tentado) -- silenciosamente: o checkbox nativo
  // ainda marcava visualmente, mas nada era salvo, e nem o card mudava
  // de cor nem a barra de progresso avançava (o erro acontecia ANTES
  // dessas atualizações, no fluxo do listener de "change"). Mesmo
  // padrão de busca que salvarObservacao() já usa corretamente logo
  // abaixo.
  function idsDoCheckbox(checkbox) {
    return checkbox.closest('.card-exigencia').dataset.exigenciaIds.split(',');
  }

  const totalExigencias = checkboxes.reduce(function (soma, cb) {
    return soma + idsDoCheckbox(cb).length;
  }, 0);

  let avisoTimer = null;

  function mostrarAvisoDeErro() {
    avisoErro.hidden = false;
    clearTimeout(avisoTimer);
    avisoTimer = setTimeout(function () { avisoErro.hidden = true; }, 5000);
  }

  function atualizarProgresso() {
    const feitas = checkboxes
      .filter(function (cb) { return cb.checked; })
      .reduce(function (soma, cb) { return soma + idsDoCheckbox(cb).length; }, 0);
    const porcentagem = totalExigencias ? (feitas / totalExigencias) * 100 : 0;
    barraPreenchida.style.width = porcentagem + '%';
    progressoTexto.textContent = feitas + ' de ' + totalExigencias + ' exigências conferidas';
  }

  async function salvarExigencia(id, dados) {
    const resposta = await fetch('/exigencias/' + id, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(dados),
    });
    if (!resposta.ok) {
      throw new Error('falha ao salvar exigência ' + id);
    }
    return resposta.json();
  }

  // Passo 9 (Mudança 2): o card inteiro muda de cor quando a exigência é
  // marcada como conferida — não muda nada no banco, só reage ao estado do
  // checkbox que já existe. Otimista igual ao resto (marca na hora, sem
  // esperar o PATCH), e desfaz junto com o checkbox se a gravação falhar.
  function atualizarCorDoCard(checkbox) {
    const card = checkbox.closest('.card-exigencia');
    if (card) {
      card.classList.toggle('card-exigencia--conferida', checkbox.checked);
    }
  }

  checkboxes.forEach(function (checkbox) {
    checkbox.addEventListener('change', async function () {
      const ids = idsDoCheckbox(checkbox);
      const novoStatus = checkbox.checked ? 'ok' : 'pendente';
      atualizarCorDoCard(checkbox);
      try {
        // Promise.all: as N linhas do grupo são salvas em paralelo. Se
        // qualquer uma falhar, cai no catch — o checkbox volta pro estado
        // anterior e mostra o aviso, mesmo que outras linhas do grupo já
        // tenham sido salvas com sucesso (fica pro usuário tentar de novo).
        await Promise.all(ids.map(function (id) {
          return salvarExigencia(id, { status_check: novoStatus });
        }));
        atualizarProgresso();
      } catch (erro) {
        checkbox.checked = !checkbox.checked; // desfaz a marcação visual
        atualizarCorDoCard(checkbox);
        mostrarAvisoDeErro();
      }
    });
  });

  async function salvarObservacao(elementoDisparador) {
    const card = elementoDisparador.closest('.card-exigencia');
    const ids = card.dataset.exigenciaIds.split(',');
    const textarea = card.querySelector('.observacao-texto');
    const checkbox = card.querySelector('.checkbox-exigencia');
    // Reenvia o status_check atual: a rota sempre aplica esse campo, então
    // deixar de repassar apagaria o estado do checkbox sem querer. Num
    // grupo, a mesma observação é salva em todas as linhas — o card trata
    // as hipóteses como uma unidade só, a observação acompanha.
    const statusAtual = checkbox.checked ? 'ok' : 'pendente';
    try {
      await Promise.all(ids.map(function (id) {
        return salvarExigencia(id, { status_check: statusAtual, observacao: textarea.value });
      }));
    } catch (erro) {
      mostrarAvisoDeErro();
    }
  }

  document.querySelectorAll('.btn-salvar-nota').forEach(function (botao) {
    botao.addEventListener('click', function () { salvarObservacao(botao); });
  });
  document.querySelectorAll('.observacao-texto').forEach(function (campo) {
    campo.addEventListener('blur', function () { salvarObservacao(campo); });
  });

  // Passo 9 (Mudança 5): abrir/fechar CADA card é o <details> nativo
  // fazendo sozinho, sem JS nenhum — só o botão "expandir/recolher tudo"
  // precisa de JS mesmo, porque é a única parte que mexe em vários blocos
  // de uma vez (o HTML puro não tem esse conceito de "abrir todos").
  const blocosRecolhiveis = document.querySelectorAll('.categoria-recolhivel');
  const botaoExpandirTudo = document.querySelector('.btn-expandir-tudo');
  const botaoRecolherTudo = document.querySelector('.btn-recolher-tudo');

  if (botaoExpandirTudo) {
    botaoExpandirTudo.addEventListener('click', function () {
      blocosRecolhiveis.forEach(function (bloco) { bloco.open = true; });
    });
  }
  if (botaoRecolherTudo) {
    botaoRecolherTudo.addEventListener('click', function () {
      blocosRecolhiveis.forEach(function (bloco) { bloco.open = false; });
    });
  }

  atualizarProgresso();
})();
