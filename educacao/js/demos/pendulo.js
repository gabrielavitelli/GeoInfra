import { setupCanvas, createControlGroup, createSlider, createButton, fillConcept, loop, onResize } from '../engine.js';

export const meta = {
  id: 'pendulo',
  title: 'Pêndulo Simples',
  description: 'Observe o movimento oscilatório e como o comprimento afeta o período.',
  icon: '🕰️',
  tags: ['fund2', 'fisica'],
  accent: '#7209b7',
};

export function init(canvas, controlsPanel, conceptBox) {
  fillConcept(conceptBox,
    'Movimento pendular',
    'O pêndulo oscila convertendo energia potencial em cinética e vice-versa. Pêndulos mais longos demoram mais para completar uma oscilação.',
    'T ≈ 2π√(L/g)'
  );

  let comprimento = 150;
  let angulo = Math.PI / 4;
  let angVel = 0;
  const g = 980;
  let running = true;

  const sliderL = createSlider(80, 220, 5, comprimento, v => { comprimento = v; });
  controlsPanel.appendChild(createControlGroup('Comprimento do fio (px)', sliderL.wrap));

  const pauseBtn = createButton('⏸ Pausar', 'btn-outline', () => {
    running = !running;
    pauseBtn.textContent = running ? '⏸ Pausar' : '▶ Continuar';
  });
  const resetBtn = createButton('↺ Reiniciar', 'btn-outline', () => {
    angulo = Math.PI / 4;
    angVel = 0;
  });
  controlsPanel.appendChild(createControlGroup('', pauseBtn));
  controlsPanel.appendChild(createControlGroup('', resetBtn));

  let ctx, w, h;
  let stopLoop;
  let lastTime = null;

  function draw() {
    if (!ctx) return;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = '#f8f9ff';
    ctx.fillRect(0, 0, w, h);

    const pivotX = w / 2;
    const pivotY = 80;
    const bobX = pivotX + Math.sin(angulo) * comprimento;
    const bobY = pivotY + Math.cos(angulo) * comprimento;

    ctx.strokeStyle = '#5a6278';
    ctx.lineWidth = 2;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(pivotX - 60, pivotY);
    ctx.lineTo(pivotX + 60, pivotY);
    ctx.stroke();
    ctx.setLineDash([]);

    ctx.strokeStyle = '#1a1a2e';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(pivotX, pivotY);
    ctx.lineTo(bobX, bobY);
    ctx.stroke();

    ctx.fillStyle = '#4361ee';
    ctx.beginPath();
    ctx.arc(bobX, bobY, 20, 0, Math.PI * 2);
    ctx.fill();

    const T = 2 * Math.PI * Math.sqrt(comprimento / g);
    const energiaPot = (1 - Math.cos(angulo)) * 100;
    const energiaCin = Math.abs(angVel) * 50;

    ctx.fillStyle = '#1a1a2e';
    ctx.font = '700 15px Nunito';
    ctx.textAlign = 'left';
    ctx.fillText(`θ = ${(angulo * 180 / Math.PI).toFixed(1)}°`, 20, 30);
    ctx.fillText(`Período ≈ ${T.toFixed(2)} s`, 20, 52);

    const barW = 120;
    ctx.fillStyle = '#4361ee';
    ctx.fillRect(20, 70, barW * Math.min(energiaPot / 100, 1), 12);
    ctx.strokeStyle = '#4361ee';
    ctx.strokeRect(20, 70, barW, 12);
    ctx.fillStyle = '#5a6278';
    ctx.font = '12px Nunito';
    ctx.fillText('Ep (potencial)', 150, 80);

    ctx.fillStyle = '#f72585';
    ctx.fillRect(20, 92, barW * Math.min(energiaCin / 100, 1), 12);
    ctx.strokeStyle = '#f72585';
    ctx.strokeRect(20, 92, barW, 12);
    ctx.fillText('Ec (cinética)', 150, 102);
  }

  function resize() {
    ({ ctx, w, h } = setupCanvas(canvas));
  }

  const stopResize = onResize(canvas, resize);
  resize();

  stopLoop = loop((time) => {
    if (lastTime === null) lastTime = time;
    const dt = Math.min((time - lastTime) / 1000, 0.032);
    lastTime = time;
    if (running) {
      const accel = -(g / comprimento) * Math.sin(angulo);
      angVel += accel * dt;
      angVel *= 0.999;
      angulo += angVel * dt;
    }
    draw();
  });

  return { destroy: () => { stopLoop(); stopResize(); } };
}
