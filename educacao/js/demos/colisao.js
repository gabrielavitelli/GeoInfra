import { setupCanvas, createControlGroup, createSlider, createButton, fillConcept, loop, onResize } from '../engine.js';

export const meta = {
  id: 'colisao',
  title: 'Colisões Elásticas',
  description: 'Duas bolas trocam energia ao colidir — explore massa e velocidade.',
  icon: '⚫',
  tags: ['fund2', 'fisica'],
  accent: '#06d6a0',
};

export function init(canvas, controlsPanel, conceptBox) {
  fillConcept(conceptBox,
    'Conservação de momentum',
    'Em colisões, o momentum total (massa × velocidade) se conserva. Bolas de massas diferentes se comportam de formas distintas.',
    'm₁v₁ + m₂v₂ = m₁v₁\' + m₂v₂\''
  );

  let m1 = 2, m2 = 1;
  let v1 = 4, v2 = -2;
  let running = true;

  const ball1 = { x: 0, vx: 0, m: m1, color: '#4361ee', r: 0 };
  const ball2 = { x: 0, vx: 0, m: m2, color: '#f72585', r: 0 };

  function resetBalls() {
    ball1.m = m1;
    ball2.m = m2;
    ball1.r = 14 + m1 * 6;
    ball2.r = 14 + m2 * 6;
    ball1.vx = v1 * 40;
    ball2.vx = v2 * 40;
    ball1.x = 0;
    ball2.x = 0;
  }

  const sM1 = createSlider(1, 5, 0.5, m1, v => { m1 = v; resetBalls(); });
  const sM2 = createSlider(1, 5, 0.5, m2, v => { m2 = v; resetBalls(); });
  const sV1 = createSlider(-6, 6, 0.5, v1, v => { v1 = v; resetBalls(); });
  const sV2 = createSlider(-6, 6, 0.5, v2, v => { v2 = v; resetBalls(); });

  controlsPanel.appendChild(createControlGroup('Massa bola azul', sM1.wrap));
  controlsPanel.appendChild(createControlGroup('Massa bola rosa', sM2.wrap));
  controlsPanel.appendChild(createControlGroup('Velocidade inicial azul', sV1.wrap));
  controlsPanel.appendChild(createControlGroup('Velocidade inicial rosa', sV2.wrap));

  const resetBtn = createButton('↺ Reiniciar', 'btn-outline', resetBalls);
  const pauseBtn = createButton('⏸ Pausar', 'btn-outline', () => {
    running = !running;
    pauseBtn.textContent = running ? '⏸ Pausar' : '▶ Continuar';
  });
  controlsPanel.appendChild(createControlGroup('', resetBtn));
  controlsPanel.appendChild(createControlGroup('', pauseBtn));

  let ctx, w, h;
  let stopLoop;
  let lastTime = null;

  function elasticCollision(b1, b2) {
    const mTotal = b1.m + b2.m;
    const v1n = ((b1.m - b2.m) * b1.vx + 2 * b2.m * b2.vx) / mTotal;
    const v2n = ((b2.m - b1.m) * b2.vx + 2 * b1.m * b1.vx) / mTotal;
    b1.vx = v1n;
    b2.vx = v2n;
  }

  function draw() {
    if (!ctx) return;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = '#f8f9ff';
    ctx.fillRect(0, 0, w, h);

    const trackY = h * 0.5;
    ctx.strokeStyle = '#ccc';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(30, trackY);
    ctx.lineTo(w - 30, trackY);
    ctx.stroke();

    [ball1, ball2].forEach(b => {
      ctx.fillStyle = b.color;
      ctx.beginPath();
      ctx.arc(b.x, trackY, b.r, 0, Math.PI * 2);
      ctx.fill();
      ctx.strokeStyle = '#1a1a2e';
      ctx.lineWidth = 2;
      ctx.stroke();
      ctx.fillStyle = '#1a1a2e';
      ctx.font = '600 11px Nunito';
      ctx.textAlign = 'center';
      ctx.fillText(b.m + 'kg', b.x, trackY + 4);
    });

    const pTotal = ball1.m * ball1.vx + ball2.m * ball2.vx;
    ctx.fillStyle = '#1a1a2e';
    ctx.font = '700 14px Nunito';
    ctx.textAlign = 'left';
    ctx.fillText(`v₁ = ${(ball1.vx / 40).toFixed(1)} m/s`, 20, 30);
    ctx.fillText(`v₂ = ${(ball2.vx / 40).toFixed(1)} m/s`, 20, 50);
    ctx.fillStyle = '#06d6a0';
    ctx.fillText(`Momentum total = ${(pTotal / 40).toFixed(1)} kg·m/s`, 20, 75);
  }

  function update(dt) {
    ball1.x += ball1.vx * dt;
    ball2.x += ball2.vx * dt;

    const left = 30 + ball1.r;
    const right = w - 30 - ball2.r;
    const gap = ball2.x - ball1.x - ball1.r - ball2.r;

    if (ball1.x < left) { ball1.x = left; ball1.vx *= -1; }
    if (ball2.x > right) { ball2.x = right; ball2.vx *= -1; }

    if (gap <= 0 && ball1.vx > ball2.vx) {
      const overlap = -gap;
      ball1.x -= overlap / 2;
      ball2.x += overlap / 2;
      elasticCollision(ball1, ball2);
    }

    if (ball1.x === 0 && ball2.x === 0) {
      ball1.x = w * 0.3;
      ball2.x = w * 0.65;
    }
  }

  function resize() {
    ({ ctx, w, h } = setupCanvas(canvas));
    resetBalls();
    ball1.x = w * 0.3;
    ball2.x = w * 0.65;
  }

  const stopResize = onResize(canvas, resize);
  resize();

  stopLoop = loop((time) => {
    if (lastTime === null) lastTime = time;
    const dt = Math.min((time - lastTime) / 1000, 0.032);
    lastTime = time;
    if (running) update(dt);
    draw();
  });

  return { destroy: () => { stopLoop(); stopResize(); } };
}
