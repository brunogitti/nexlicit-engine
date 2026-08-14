// Busca client-side na listagem de processos (Passo 7, polimento de
// necessidade real — dashboard com 7+ processos já ficava difícil de
// escanear). Filtra os cards já renderizados pelo texto de
// "data-busca" (nome + órgão, já em minúsculo — ver
// app/rotas/paginas.py, painel_principal), sem chamar o backend de
// novo: listar_processos() já trouxe tudo que precisa pra isso.

(function () {
  const campoBusca = document.getElementById('busca-processos-input');
  if (!campoBusca) return; // tela "nenhum processo ainda" não tem o campo

  const cards = Array.from(document.querySelectorAll('.card-processo'));
  const avisoSemResultado = document.getElementById('busca-sem-resultado');

  campoBusca.addEventListener('input', function () {
    const termo = campoBusca.value.trim().toLowerCase();
    let algumVisivel = false;

    cards.forEach(function (card) {
      const bate = card.dataset.busca.includes(termo);
      card.hidden = !bate;
      if (bate) algumVisivel = true;
    });

    avisoSemResultado.hidden = algumVisivel;
  });
})();
