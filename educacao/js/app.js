import * as contagem from './demos/contagem.js';
import * as adicao from './demos/adicao.js';
import * as fracoes from './demos/fracoes.js';
import * as formas from './demos/formas.js';
import * as areaPerimetro from './demos/area-perimetro.js';
import * as pitagoras from './demos/pitagoras.js';
import * as funcaoAfim from './demos/funcao-afim.js';
import * as proporcao from './demos/proporcao.js';
import * as mru from './demos/mru.js';
import * as quedaLivre from './demos/queda-livre.js';
import * as pendulo from './demos/pendulo.js';
import * as alavanca from './demos/alavanca.js';
import * as colisao from './demos/colisao.js';

const DEMOS = [
  contagem, adicao, fracoes, formas,
  areaPerimetro, pitagoras, funcaoAfim, proporcao,
  mru, quedaLivre, pendulo, alavanca, colisao,
];

const cardsGrid = document.getElementById('cardsGrid');
const demoPanel = document.getElementById('demoPanel');
const demoCanvas = document.getElementById('demoCanvas');
const controlsPanel = document.getElementById('controlsPanel');
const conceptBox = document.getElementById('conceptBox');
const demoTitle = document.getElementById('demoTitle');
const demoDescription = document.getElementById('demoDescription');
const demoBadge = document.getElementById('demoBadge');
const backBtn = document.getElementById('backBtn');
const filterNav = document.getElementById('filterNav');
const hero = document.querySelector('.hero');

let currentDemo = null;
let activeFilter = 'all';

function tagLabel(tag) {
  const labels = {
    fund1: 'Fund. I',
    fund2: 'Fund. II',
    matematica: 'Matemática',
    fisica: 'Física',
  };
  return labels[tag] || tag;
}

function renderCards() {
  cardsGrid.innerHTML = '';
  const filtered = DEMOS.filter(d =>
    activeFilter === 'all' || d.meta.tags.includes(activeFilter)
  );

  filtered.forEach(demo => {
    const card = document.createElement('article');
    card.className = 'demo-card';
    card.style.setProperty('--card-accent', demo.meta.accent || '#4361ee');
    card.innerHTML = `
      <span class="card-icon">${demo.meta.icon}</span>
      <div class="card-tags">
        ${demo.meta.tags.map(t => `<span class="tag tag-${t}">${tagLabel(t)}</span>`).join('')}
      </div>
      <h3>${demo.meta.title}</h3>
      <p>${demo.meta.description}</p>
    `;
    card.addEventListener('click', () => openDemo(demo));
    cardsGrid.appendChild(card);
  });
}

function openDemo(demo) {
  if (currentDemo?.destroy) currentDemo.destroy();

  cardsGrid.classList.add('hidden');
  hero.classList.add('hidden');
  demoPanel.classList.remove('hidden');

  demoTitle.textContent = demo.meta.title;
  demoDescription.textContent = demo.meta.description;
  demoBadge.textContent = demo.meta.tags.map(tagLabel).join(' · ');
  controlsPanel.innerHTML = '';

  currentDemo = demo.init(demoCanvas, controlsPanel, conceptBox);
}

function closeDemo() {
  if (currentDemo?.destroy) currentDemo.destroy();
  currentDemo = null;

  demoPanel.classList.add('hidden');
  cardsGrid.classList.remove('hidden');
  hero.classList.remove('hidden');
}

backBtn.addEventListener('click', closeDemo);

filterNav.addEventListener('click', e => {
  const btn = e.target.closest('.filter-btn');
  if (!btn) return;
  filterNav.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  activeFilter = btn.dataset.filter;
  if (!demoPanel.classList.contains('hidden')) closeDemo();
  renderCards();
});

renderCards();
