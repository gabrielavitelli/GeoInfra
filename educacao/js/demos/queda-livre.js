import { setupCanvas, createControlGroup, createSlider, createButton, fillConcept, loop, onResize } from '../engine.js';

export const meta = {
  id: 'queda-livre',
  title: 'Queda Livre',
  description: 'Solte a bola e observe a aceleração da gravidade (g ≈ 10 m/s²).',
  icon: '🎾',
  tags: ['fund2', 'fisica'],
  accent: '#f72585',
};

export function init(canvas, controlsPanel, conceptBox) {
  fillConcept(conceptBox,
    'Queda livre',
    'Corpos em queda livre aceleram por causa da gravidade. A cada segundo, a velocidade aumenta cerca de 10 m/s (g ≈ 10 m/s²).',
    'v = g·t · s = ½·g·t²'
  );

  const g = 10;
  let altura = 20;
  let t = 0;
  let running = false;
  let finished = false;

  const sliderH = createSlider(5, 30, 1, altura, v => {
    if (!running) { altura = v; reset(); draw(); }
  });
  controlsPanel.appendChild(createControlGroup('Altura inicial (m)', sliderH.wrap));

  const dropBtn = createButton('⬇ Soltar', 'btn-accent', () => {
    if (running) return;
    running = true;
    finished = false;
    t = 0;
  });
  const resetBtn = createButton('↺ Reiniciar', 'btn-outline', reset);
  controlsPanel.appendChild(createControlGroup('', dropBtn));
  controlsPanel.appendChild(createControlGroup('', resetBtn));

  function reset() {
    running = false;
    finished = false;
    t = 0;
    draw();
  }

  let ctx, w, h;
  let stopLoop;
  let lastTime = null;

  function draw() {
    if (!ctx) return;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = '#f0f4ff';
    ctx.fillRect(0, 0, w, h);

    const topY = 60;
    const groundY = h - 60;
    const scale = (groundY - topY) / 30;

    ctx.strokeStyle = '#ccc';
    ctx.lineWidth = 1;
    for (let m = 0; m <= 30; m += 5) {
      const y = groundY - m * scale;
      ctx.beginPath();
      ctx.moveTo(w * 0.7, y);
      ctx.lineTo(w * 0.7 + 20, y);
      ctx.stroke();
      ctx.fillStyle = '#5a6278';
      ctx.font = '11px Nunito';
      ctx.fillText(m + 'm', w * 0.7 + 24, y + 4);
    }

    ctx.fillStyle = '#06d6a0';
    ctx.fillRect(0, groundY, w, h - groundY);

    const s = Math.min(0.5 * g * t * t, altura);
    const v = g * t;
    const ballY = groundY - s * scale;

    ctx.fillStyle = '#f72585';
    ctx.beginPath();
    ctx.arc(w / 2, ballY, 18, 0, Math.PI * 2);
    ctx.fill();

    if (running && v > 0) {
      ctx.strokeStyle = '#f72585';
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.moveTo(w / 2, ballY + 20);
      ctx.lineTo(w / 2, ballY + 20 + Math.min(v * 3, 60));
      ctx.stroke();
      ctx.fillStyle = '#f72585';
      ctx.beginPath();
      ctx.moveTo(w / 2, ballY + 20 + Math.min(v * 3, 60));
      ctx.lineTo(w / 2 - 6, ballY + 12 + Math.min(v * 3, 60));
      ctx.lineTo(w / 2 + 6, ballY + 12 + Math.min(v * 3, 60));
      ctx.fill();
    }

    ctx.fillStyle = '#1a1a2e';
    ctx.font = '800 16px Nunito';
    ctx.textAlign = 'left';
    ctx.fillText(`t = ${t.toFixed(2)} s`, 20, 30);
    ctx.fillText(`s = ${s.toFixed(1)} m`, 20, 52);
    ctx.fillText(`v = ${Math.min(v, Math.sqrt(2 * g * altura)).toFixed(1)} m/s`, 20, 74);
    ctx.font = '600 13px Nunito';
    ctx.fillStyle = '#5a6278';
    ctx.fillText(`g = ${g} m/s²`, 20, 96);

    if (finished) {
      ctx.fillStyle = '#06d6a0';
      ctx.font = '700 18px Nunito';
      ctx.textAlign = 'center';
      ctx.fillText('Impacto!', w / 2, groundY - 30);
    }
  }

  function resize() {
    ({ ctx, w, h } = setupCanvas(canvas));
  }

  const stopResize = onResize(canvas, resize);
  resize();
  draw();

  stopLoop = loop((time) => {
    if (lastTime === null) lastTime = time;
    const dt = (time - lastTime) / 1000;
    lastTime = time;
    if (running) {
      t += dt;
      if (0.5 * g * t * t >= altura) {
        t = Math.sqrt(2 * altura / g);
        running = false;
        finished = true;
      }
    }
    draw();
  });

  return { destroy: () => { stopLoop(); stopResize(); } };
}
