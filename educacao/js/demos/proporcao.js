import { setupCanvas, createControlGroup, createSlider, createButton, fillConcept, loop, onResize } from '../engine.js';

export const meta = {
  id: 'proporcao',
  title: 'Proporção e Escala',
  description: 'Ajuste a escala e veja como as medidas se relacionam proporcionalmente.',
  icon: '⚖️',
  tags: ['fund2', 'matematica'],
  accent: '#ffd166',
};

export function init(canvas, controlsPanel, conceptBox) {
  fillConcept(conceptBox,
    'Razão e proporção',
    'Duas razões são proporcionais quando uma é múltipla da outra. Se 2 cm no desenho = 10 cm na realidade, a escala é 1:5.',
    'a/b = c/d'
  );

  let escala = 5;
  let realMedida = 10;
  let running = true;

  const sliderEscala = createSlider(2, 10, 1, escala, v => { escala = v; });
  const sliderReal = createSlider(5, 30, 1, realMedida, v => { realMedida = v; });
  controlsPanel.appendChild(createControlGroup('Escala 1:N', sliderEscala.wrap));
  controlsPanel.appendChild(createControlGroup('Medida real (cm)', sliderReal.wrap));

  const pauseBtn = createButton('⏸ Pausar', 'btn-outline', () => {
    running = !running;
    pauseBtn.textContent = running ? '⏸ Pausar' : '▶ Continuar';
  });
  controlsPanel.appendChild(createControlGroup('', pauseBtn));

  let ctx, w, h;
  let stopLoop;
  let pulse = 0;

  function drawRuler(x, y, length, label, color) {
    ctx.fillStyle = color;
    ctx.fillRect(x, y, length, 20);
    ctx.strokeStyle = '#1a1a2e';
    ctx.lineWidth = 2;
    ctx.strokeRect(x, y, length, 20);
    for (let i = 0; i <= length; i += 20) {
      ctx.beginPath();
      ctx.moveTo(x + i, y);
      ctx.lineTo(x + i, y - 8);
      ctx.stroke();
    }
    ctx.font = '700 14px Nunito';
    ctx.fillStyle = '#1a1a2e';
    ctx.fillText(label, x + length / 2, y + 45);
  }

  function draw() {
    if (!ctx) return;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = '#f8f9ff';
    ctx.fillRect(0, 0, w, h);

    const desenho = realMedida / escala;
    const maxLen = w * 0.7;
    const scalePx = maxLen / realMedida;
    const desenhoPx = desenho * scalePx * (1 + Math.sin(pulse) * 0.03);
    const realPx = realMedida * scalePx;

    const y1 = h * 0.35;
    const y2 = h * 0.6;
    const startX = (w - realPx) / 2;

    drawRuler(startX, y1, desenhoPx, `Desenho: ${desenho.toFixed(1)} cm`, '#4361ee');
    drawRuler(startX, y2, realPx, `Real: ${realMedida} cm`, '#f72585');

    ctx.setLineDash([6, 4]);
    ctx.strokeStyle = '#06d6a0';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(startX + desenhoPx, y1 + 10);
    ctx.lineTo(startX + desenhoPx, y2 + 10);
    ctx.stroke();
    ctx.setLineDash([]);

    ctx.fillStyle = '#1a1a2e';
    ctx.font = '800 20px Nunito';
    ctx.textAlign = 'center';
    ctx.fillText(`Escala 1:${escala}`, w / 2, h - 50);
    ctx.font = '600 16px Nunito';
    ctx.fillStyle = '#5a6278';
    ctx.fillText(`1 cm no desenho = ${escala} cm na realidade`, w / 2, h - 25);
  }

  function resize() {
    ({ ctx, w, h } = setupCanvas(canvas));
  }

  const stopResize = onResize(canvas, resize);
  resize();
  stopLoop = loop((time) => {
    if (running) pulse = time * 0.003;
    draw();
  });

  return { destroy: () => { stopLoop(); stopResize(); } };
}
