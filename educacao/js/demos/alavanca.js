import { setupCanvas, createControlGroup, createSlider, fillConcept, onResize } from '../engine.js';

export const meta = {
  id: 'alavanca',
  title: 'Alavanca e Equilíbrio',
  description: 'Ajuste as massas e distâncias para equilibrar a alavanca.',
  icon: '⚖️',
  tags: ['fund2', 'fisica'],
  accent: '#ffd166',
};

export function init(canvas, controlsPanel, conceptBox) {
  fillConcept(conceptBox,
    'Princípio das alavancas',
    'Para equilibrar uma alavanca, o momento de força de cada lado deve ser igual: força × distância.',
    'F₁·d₁ = F₂·d₂'
  );

  let m1 = 3;
  let m2 = 2;
  let d1 = 3;
  let d2 = 4;

  const sliders = [
    ['Massa esquerda (kg)', 1, 8, 1, () => m1, v => { m1 = v; draw(); }],
    ['Massa direita (kg)', 1, 8, 1, () => m2, v => { m2 = v; draw(); }],
    ['Distância esquerda (m)', 1, 6, 0.5, () => d1, v => { d1 = v; draw(); }],
    ['Distância direita (m)', 1, 6, 0.5, () => d2, v => { d2 = v; draw(); }],
  ];

  sliders.forEach(([label, min, max, step, getVal, setVal]) => {
    const s = createSlider(min, max, step, getVal(), setVal);
    controlsPanel.appendChild(createControlGroup(label, s.wrap));
  });

  let ctx, w, h;

  function drawBlock(x, y, mass, color) {
    const size = 20 + mass * 8;
    ctx.fillStyle = color;
    ctx.fillRect(x - size / 2, y - size, size, size);
    ctx.strokeStyle = '#1a1a2e';
    ctx.lineWidth = 2;
    ctx.strokeRect(x - size / 2, y - size, size, size);
    ctx.fillStyle = '#fff';
    ctx.font = '700 13px Nunito';
    ctx.textAlign = 'center';
    ctx.fillText(mass + 'kg', x, y - size / 2 + 5);
  }

  function draw() {
    if (!ctx) return;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = '#f8f9ff';
    ctx.fillRect(0, 0, w, h);

    const fulcrumX = w / 2;
    const fulcrumY = h * 0.55;
    const barW = w * 0.75;
    const pxPerM = barW / 12;

    const moment1 = m1 * d1;
    const moment2 = m2 * d2;
    const balanced = Math.abs(moment1 - moment2) < 0.05;
    const tilt = Math.max(-0.15, Math.min(0.15, (moment2 - moment1) * 0.02));

    ctx.save();
    ctx.translate(fulcrumX, fulcrumY);
    ctx.rotate(tilt);

    ctx.fillStyle = '#5a6278';
    ctx.fillRect(-barW / 2, -8, barW, 16);
    ctx.strokeStyle = '#1a1a2e';
    ctx.lineWidth = 2;
    ctx.strokeRect(-barW / 2, -8, barW, 16);

    const leftX = -d1 * pxPerM;
    const rightX = d2 * pxPerM;
    drawBlock(leftX, -8, m1, '#4361ee');
    drawBlock(rightX, -8, m2, '#f72585');

    ctx.restore();

    ctx.fillStyle = '#1a1a2e';
    ctx.beginPath();
    ctx.moveTo(fulcrumX, fulcrumY + 8);
    ctx.lineTo(fulcrumX - 20, fulcrumY + 50);
    ctx.lineTo(fulcrumX + 20, fulcrumY + 50);
    ctx.closePath();
    ctx.fill();

    ctx.fillStyle = balanced ? '#06d6a0' : '#ffd166';
    ctx.font = '800 20px Nunito';
    ctx.textAlign = 'center';
    ctx.fillText(balanced ? '✓ Equilibrada!' : '↻ Desequilibrada', w / 2, 40);

    ctx.fillStyle = '#1a1a2e';
    ctx.font = '600 16px Nunito';
    ctx.fillText(`${m1}×${d1} = ${moment1.toFixed(1)}  |  ${m2}×${d2} = ${moment2.toFixed(1)}`, w / 2, h - 30);
  }

  function resize() {
    ({ ctx, w, h } = setupCanvas(canvas));
    draw();
  }

  const stopResize = onResize(canvas, resize);
  resize();

  return { destroy: () => stopResize() };
}
