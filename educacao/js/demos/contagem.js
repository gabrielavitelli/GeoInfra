import { setupCanvas, createControlGroup, createButton, createButtonRow, fillConcept, onResize } from '../engine.js';

export const meta = {
  id: 'contagem',
  title: 'Contagem com Objetos',
  description: 'Clique nas maçãs para contar de 1 a 20. Ideal para o 1º ano.',
  icon: '🍎',
  tags: ['fund1', 'matematica'],
  accent: '#4cc9f0',
};

export function init(canvas, controlsPanel, conceptBox) {
  fillConcept(conceptBox,
    'Por que contar?',
    'A contagem é a base da matemática. Associar cada objeto a um número (1, 2, 3...) desenvolve o raciocínio quantitativo.',
    '1 → um objeto · 2 → dois objetos · ...'
  );

  let count = 0;
  const max = 20;
  const apples = [];

  const addBtn = createButton('+ Adicionar maçã', 'btn-primary', () => {
    if (count < max) { count++; apples.push({ x: 0, y: 0, pop: 1 }); layout(); draw(); }
  });
  const removeBtn = createButton('− Remover', 'btn-outline', () => {
    if (count > 0) { count--; apples.pop(); layout(); draw(); }
  });
  const resetBtn = createButton('Zerar', 'btn-outline', () => {
    count = 0; apples.length = 0; layout(); draw();
  });
  controlsPanel.appendChild(createControlGroup('Controles', createButtonRow(addBtn, removeBtn, resetBtn)));

  let ctx, w, h;

  function layout() {
    apples.length = 0;
    for (let i = 0; i < count; i++) {
      apples.push({ idx: i });
    }
  }

  function drawApple(x, y, scale) {
    ctx.save();
    ctx.translate(x, y);
    ctx.scale(scale, scale);
    ctx.fillStyle = '#e63946';
    ctx.beginPath();
    ctx.arc(0, 0, 22, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = '#2d6a4f';
    ctx.beginPath();
    ctx.ellipse(0, -24, 6, 10, -0.3, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }

  function draw() {
    if (!ctx) return;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = '#f8f9ff';
    ctx.fillRect(0, 0, w, h);

    const cols = Math.min(5, Math.max(1, Math.ceil(Math.sqrt(count || 1))));
    const size = 56;
    const startX = (w - cols * size) / 2 + size / 2;
    const rows = Math.ceil((count || 1) / cols);
    const startY = (h - rows * size) / 2 + size / 2 - 20;

    apples.forEach((_, i) => {
      const col = i % cols;
      const row = Math.floor(i / cols);
      const x = startX + col * size;
      const y = startY + row * size;
      drawApple(x, y, 1);
      ctx.fillStyle = '#1a1a2e';
      ctx.font = 'bold 12px Nunito';
      ctx.textAlign = 'center';
      ctx.fillText(String(i + 1), x, y + 4);
    });

    ctx.fillStyle = '#4361ee';
    ctx.font = 'bold 48px Nunito';
    ctx.textAlign = 'center';
    ctx.fillText(String(count), w / 2, h - 40);
    ctx.font = '600 18px Nunito';
    ctx.fillStyle = '#5a6278';
    ctx.fillText('Total de maçãs', w / 2, h - 70);
  }

  function resize() {
    ({ ctx, w, h } = setupCanvas(canvas));
    draw();
  }

  const stopResize = onResize(canvas, resize);
  resize();
  layout();

  return { destroy: () => stopResize() };
}
