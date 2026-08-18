import { setupCanvas, createControlGroup, createSlider, fillConcept, onResize } from '../engine.js';

export const meta = {
  id: 'fracoes',
  title: 'Frações com Pizza',
  description: 'Divida a pizza em partes iguais e veja a fração correspondente.',
  icon: '🍕',
  tags: ['fund1', 'matematica'],
  accent: '#ffd166',
};

export function init(canvas, controlsPanel, conceptBox) {
  fillConcept(conceptBox,
    'O que é uma fração?',
    'Uma fração representa partes de um todo dividido em pedaços iguais. Se a pizza tem 8 pedaços e comemos 3, comemos 3/8.',
    'numerador / denominador = 3/8'
  );

  let denominador = 8;
  let numerador = 3;

  const sliderDen = createSlider(2, 12, 1, denominador, v => {
    denominador = v;
    if (numerador > denominador) numerador = denominador;
    sliderNum.input.max = denominador;
    draw();
  });
  const sliderNum = createSlider(0, denominador, 1, numerador, v => { numerador = v; draw(); });

  controlsPanel.appendChild(createControlGroup('Partes totais (denominador)', sliderDen.wrap));
  controlsPanel.appendChild(createControlGroup('Partes coloridas (numerador)', sliderNum.wrap));

  let ctx, w, h;

  function draw() {
    if (!ctx) return;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = '#f8f9ff';
    ctx.fillRect(0, 0, w, h);

    const cx = w / 2;
    const cy = h / 2 - 20;
    const r = Math.min(w, h) * 0.28;

    for (let i = 0; i < denominador; i++) {
      const start = -Math.PI / 2 + (i / denominador) * Math.PI * 2;
      const end = start + (Math.PI * 2) / denominador;
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      ctx.arc(cx, cy, r, start, end);
      ctx.closePath();
      ctx.fillStyle = i < numerador ? '#ffd166' : '#ffe8a3';
      ctx.fill();
      ctx.strokeStyle = '#1a1a2e';
      ctx.lineWidth = 2;
      ctx.stroke();
    }

    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.strokeStyle = '#1a1a2e';
    ctx.lineWidth = 3;
    ctx.stroke();

    ctx.fillStyle = '#1a1a2e';
    ctx.font = '800 42px Nunito';
    ctx.textAlign = 'center';
    ctx.fillText(`${numerador}/${denominador}`, cx, h - 60);

    const pct = ((numerador / denominador) * 100).toFixed(1);
    ctx.font = '600 18px Nunito';
    ctx.fillStyle = '#5a6278';
    ctx.fillText(`${pct}% da pizza`, cx, h - 30);
  }

  function resize() {
    ({ ctx, w, h } = setupCanvas(canvas));
    draw();
  }

  const stopResize = onResize(canvas, resize);
  resize();

  return { destroy: () => stopResize() };
}
