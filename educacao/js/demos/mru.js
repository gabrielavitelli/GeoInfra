import { setupCanvas, createControlGroup, createSlider, createButton, fillConcept, loop, onResize } from '../engine.js';

export const meta = {
  id: 'mru',
  title: 'Movimento Retilíneo Uniforme',
  description: 'Observe um carrinho se deslocando com velocidade constante.',
  icon: '🚗',
  tags: ['fund2', 'fisica'],
  accent: '#4361ee',
};

export function init(canvas, controlsPanel, conceptBox) {
  fillConcept(conceptBox,
    'MRU — Velocidade constante',
    'No movimento retilíneo uniforme, o corpo percorre distâncias iguais em tempos iguais. A velocidade não muda.',
    'v = Δs/Δt · s = v·t'
  );

  let velocidade = 5;
  let tempo = 0;
  let running = true;

  const sliderV = createSlider(1, 15, 0.5, velocidade, v => { velocidade = v; });
  controlsPanel.appendChild(createControlGroup('Velocidade (m/s)', sliderV.wrap));

  const pauseBtn = createButton('⏸ Pausar', 'btn-outline', () => {
    running = !running;
    pauseBtn.textContent = running ? '⏸ Pausar' : '▶ Continuar';
  });
  const resetBtn = createButton('↺ Reiniciar', 'btn-outline', () => { tempo = 0; });
  controlsPanel.appendChild(createControlGroup('', pauseBtn));
  controlsPanel.appendChild(createControlGroup('', resetBtn));

  let ctx, w, h;
  let stopLoop;
  let lastTime = null;

  function draw() {
    if (!ctx) return;
    ctx.clearRect(0, 0, w, h);

    const groundY = h * 0.65;
    ctx.fillStyle = '#e8ecf4';
    ctx.fillRect(0, 0, w, groundY);
    ctx.fillStyle = '#06d6a0';
    ctx.fillRect(0, groundY, w, h - groundY);

    ctx.strokeStyle = '#5a6278';
    ctx.lineWidth = 2;
    ctx.setLineDash([10, 8]);
    ctx.beginPath();
    ctx.moveTo(0, groundY);
    ctx.lineTo(w, groundY);
    ctx.stroke();
    ctx.setLineDash([]);

    for (let m = 0; m < w; m += 50) {
      ctx.fillStyle = '#1a1a2e';
      ctx.font = '11px Nunito';
      ctx.fillText(String(Math.floor(m / 50)) + 'm', m + 4, groundY + 18);
      ctx.beginPath();
      ctx.moveTo(m, groundY);
      ctx.lineTo(m, groundY + 8);
      ctx.stroke();
    }

    const maxDist = (w - 80) / 50;
    const dist = (velocidade * tempo) % (maxDist + 5);
    const carX = 40 + dist * 50;

    ctx.fillStyle = '#4361ee';
    ctx.fillRect(carX - 25, groundY - 35, 50, 25);
    ctx.fillStyle = '#1a1a2e';
    ctx.beginPath();
    ctx.arc(carX - 12, groundY - 8, 8, 0, Math.PI * 2);
    ctx.arc(carX + 12, groundY - 8, 8, 0, Math.PI * 2);
    ctx.fill();

    ctx.fillStyle = '#1a1a2e';
    ctx.font = '800 18px Nunito';
    ctx.textAlign = 'left';
    ctx.fillText(`s = ${dist.toFixed(1)} m`, 20, 30);
    ctx.fillText(`t = ${tempo.toFixed(1)} s`, 20, 55);
    ctx.fillText(`v = ${velocidade.toFixed(1)} m/s`, 20, 80);
    ctx.font = '600 14px Nunito';
    ctx.fillStyle = '#4361ee';
    ctx.fillText(`s = v × t = ${(velocidade * tempo).toFixed(1)} m (total)`, 20, 105);
  }

  function resize() {
    ({ ctx, w, h } = setupCanvas(canvas));
  }

  const stopResize = onResize(canvas, resize);
  resize();
  stopLoop = loop((time) => {
    if (lastTime === null) lastTime = time;
    const dt = (time - lastTime) / 1000;
    lastTime = time;
    if (running) tempo += dt;
    draw();
  });

  return { destroy: () => { stopLoop(); stopResize(); } };
}
