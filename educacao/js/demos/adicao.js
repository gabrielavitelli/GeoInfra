import { setupCanvas, createControlGroup, createSlider, createButton, createButtonRow, fillConcept, onResize } from '../engine.js';

export const meta = {
  id: 'adicao',
  title: 'Adição com Barras',
  description: 'Veja como juntar quantidades forma uma soma maior.',
  icon: '➕',
  tags: ['fund1', 'matematica'],
  accent: '#4361ee',
};

export function init(canvas, controlsPanel, conceptBox) {
  fillConcept(conceptBox,
    'O que é adição?',
    'Adicionar significa juntar grupos. Se temos 3 bolinhas e mais 4 bolinhas, juntando tudo temos 7.',
    '3 + 4 = 7'
  );

  let a = 3, b = 4;
  let animating = false;
  let progress = 1;

  const sliderA = createSlider(0, 10, 1, a, v => { a = v; progress = 1; draw(); });
  const sliderB = createSlider(0, 10, 1, b, v => { b = v; progress = 1; draw(); });
  controlsPanel.appendChild(createControlGroup('Primeiro número', sliderA.wrap));
  controlsPanel.appendChild(createControlGroup('Segundo número', sliderB.wrap));

  const animBtn = createButton('▶ Animar junção', 'btn-accent', () => {
    if (animating) return;
    animating = true;
    progress = 0;
    const start = performance.now();
    function step(now) {
      progress = Math.min(1, (now - start) / 1200);
      draw();
      if (progress < 1) requestAnimationFrame(step);
      else animating = false;
    }
    requestAnimationFrame(step);
  });
  controlsPanel.appendChild(createControlGroup('', animBtn));

  let ctx, w, h;

  function drawDot(x, y, color, r = 14) {
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fill();
  }

  function drawGroup(count, baseX, baseY, color, offsetX = 0) {
    const cols = Math.min(5, count || 1);
    for (let i = 0; i < count; i++) {
      const col = i % cols;
      const row = Math.floor(i / cols);
      drawDot(baseX + col * 32 + offsetX, baseY + row * 32, color);
    }
  }

  function draw() {
    if (!ctx) return;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = '#f8f9ff';
    ctx.fillRect(0, 0, w, h);

    const midY = h * 0.45;
    const groupA_X = w * 0.2;
    const groupB_X = w * 0.55;
    const result_X = w * 0.5;

    ctx.font = '700 20px Nunito';
    ctx.fillStyle = '#4361ee';
    ctx.textAlign = 'center';
    ctx.fillText(String(a), groupA_X, midY - 60);
    ctx.fillStyle = '#f72585';
    ctx.fillText(String(b), groupB_X, midY - 60);

    drawGroup(a, groupA_X - 40, midY, '#4361ee');
    const shift = (groupB_X - result_X + 40) * (1 - progress);
    drawGroup(b, groupB_X - 40, midY, '#f72585', -shift * progress);

    ctx.fillStyle = '#5a6278';
    ctx.font = '600 28px Nunito';
    ctx.fillText('+', w * 0.38, midY + 8);
    ctx.fillText('=', w * 0.72, midY + 8);

    if (progress >= 1) {
      const total = a + b;
      ctx.fillStyle = '#06d6a0';
      ctx.font = '800 24px Nunito';
      ctx.fillText(String(total), result_X, midY - 60);
      drawGroup(total, result_X - Math.min(total, 5) * 16, midY + 80, '#06d6a0');
    }

    ctx.fillStyle = '#1a1a2e';
    ctx.font = '800 32px Nunito';
    ctx.fillText(`${a} + ${b} = ${a + b}`, w / 2, h - 50);
  }

  function resize() {
    ({ ctx, w, h } = setupCanvas(canvas));
    draw();
  }

  const stopResize = onResize(canvas, resize);
  resize();

  return { destroy: () => stopResize() };
}
