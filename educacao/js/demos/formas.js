import { setupCanvas, createControlGroup, createButton, createButtonRow, fillConcept, onResize } from '../engine.js';

const SHAPES = [
  { name: 'Quadrado', sides: 4, color: '#4361ee' },
  { name: 'Triângulo', sides: 3, color: '#f72585' },
  { name: 'Pentágono', sides: 5, color: '#06d6a0' },
  { name: 'Hexágono', sides: 6, color: '#7209b7' },
  { name: 'Círculo', sides: 0, color: '#ffd166' },
];

export const meta = {
  id: 'formas',
  title: 'Formas Geométricas',
  description: 'Explore polígonos e o círculo. Gire e conte os lados.',
  icon: '🔷',
  tags: ['fund1', 'matematica'],
  accent: '#7209b7',
};

export function init(canvas, controlsPanel, conceptBox) {
  fillConcept(conceptBox,
    'Figuras planas',
    'Polígonos são figuras fechadas com lados retos. Um triângulo tem 3 lados, um quadrado tem 4. O círculo é uma curva fechada sem lados retos.',
    'Triângulo: 3 lados · Quadrado: 4 lados'
  );

  let idx = 0;
  let angle = 0;
  let spinning = false;

  const prevBtn = createButton('← Anterior', 'btn-outline', () => { idx = (idx - 1 + SHAPES.length) % SHAPES.length; draw(); });
  const nextBtn = createButton('Próxima →', 'btn-outline', () => { idx = (idx + 1) % SHAPES.length; draw(); });
  const spinBtn = createButton('🔄 Girar', 'btn-primary', () => {
    if (spinning) return;
    spinning = true;
    const start = angle;
    const target = start + Math.PI * 2;
    const t0 = performance.now();
    function step(now) {
      const t = Math.min(1, (now - t0) / 1500);
      angle = start + (target - start) * (1 - Math.pow(1 - t, 3));
      draw();
      if (t < 1) requestAnimationFrame(step);
      else spinning = false;
    }
    requestAnimationFrame(step);
  });
  controlsPanel.appendChild(createControlGroup('Navegar', createButtonRow(prevBtn, nextBtn)));
  controlsPanel.appendChild(createControlGroup('', spinBtn));

  let ctx, w, h;

  function drawPolygon(cx, cy, r, sides, rot, color) {
    ctx.beginPath();
    for (let i = 0; i < sides; i++) {
      const a = rot + (i / sides) * Math.PI * 2 - Math.PI / 2;
      const x = cx + Math.cos(a) * r;
      const y = cy + Math.sin(a) * r;
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.closePath();
    ctx.fillStyle = color + '44';
    ctx.fill();
    ctx.strokeStyle = color;
    ctx.lineWidth = 4;
    ctx.stroke();
  }

  function draw() {
    if (!ctx) return;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = '#f8f9ff';
    ctx.fillRect(0, 0, w, h);

    const shape = SHAPES[idx];
    const cx = w / 2;
    const cy = h / 2 - 10;
    const r = Math.min(w, h) * 0.22;

    if (shape.sides === 0) {
      ctx.beginPath();
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
      ctx.fillStyle = shape.color + '44';
      ctx.fill();
      ctx.strokeStyle = shape.color;
      ctx.lineWidth = 4;
      ctx.stroke();
    } else {
      drawPolygon(cx, cy, r, shape.sides, angle, shape.color);
    }

    ctx.fillStyle = '#1a1a2e';
    ctx.font = '800 28px Nunito';
    ctx.textAlign = 'center';
    ctx.fillText(shape.name, cx, cy + r + 50);

    const info = shape.sides === 0 ? 'Curva fechada · 0 lados retos' : `${shape.sides} lados`;
    ctx.font = '600 18px Nunito';
    ctx.fillStyle = '#5a6278';
    ctx.fillText(info, cx, cy + r + 80);
  }

  function resize() {
    ({ ctx, w, h } = setupCanvas(canvas));
    draw();
  }

  const stopResize = onResize(canvas, resize);
  resize();

  return { destroy: () => stopResize() };
}
