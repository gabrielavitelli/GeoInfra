import { setupCanvas, createControlGroup, createSlider, fillConcept, onResize } from '../engine.js';

export const meta = {
  id: 'pitagoras',
  title: 'Teorema de Pitágoras',
  description: 'Veja os quadrados construídos sobre os lados do triângulo retângulo.',
  icon: '📐',
  tags: ['fund2', 'matematica'],
  accent: '#7209b7',
};

export function init(canvas, controlsPanel, conceptBox) {
  fillConcept(conceptBox,
    'Teorema de Pitágoras',
    'Em todo triângulo retângulo, o quadrado da hipotenusa (lado maior) é igual à soma dos quadrados dos catetos.',
    'a² + b² = c²'
  );

  let catA = 3;
  let catB = 4;

  const sliderA = createSlider(2, 8, 0.5, catA, v => { catA = v; draw(); });
  const sliderB = createSlider(2, 8, 0.5, catB, v => { catB = v; draw(); });
  controlsPanel.appendChild(createControlGroup('Cateto a', sliderA.wrap));
  controlsPanel.appendChild(createControlGroup('Cateto b', sliderB.wrap));

  let ctx, w, h;

  function drawSquare(x, y, side, color, label, angle = 0) {
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(angle);
    const s = side * 35;
    ctx.fillStyle = color + '55';
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.fillRect(0, -s, s, s);
    ctx.strokeRect(0, -s, s, s);
    ctx.fillStyle = color;
    ctx.font = '700 14px Nunito';
    ctx.textAlign = 'center';
    ctx.fillText(label, s / 2, -s / 2 + 5);
    ctx.restore();
  }

  function draw() {
    if (!ctx) return;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = '#f8f9ff';
    ctx.fillRect(0, 0, w, h);

    const hip = Math.sqrt(catA * catA + catB * catB);
    const ox = w * 0.35;
    const oy = h * 0.55;
    const scale = 35;

    ctx.strokeStyle = '#1a1a2e';
    ctx.lineWidth = 3;
    ctx.fillStyle = 'rgba(67, 97, 238, 0.2)';
    ctx.beginPath();
    ctx.moveTo(ox, oy);
    ctx.lineTo(ox + catA * scale, oy);
    ctx.lineTo(ox + catA * scale, oy - catB * scale);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();

    drawSquare(ox, oy, catA, '#4361ee', `a²=${(catA * catA).toFixed(1)}`, 0);
    drawSquare(ox + catA * scale, oy, catB, '#f72585', `b²=${(catB * catB).toFixed(1)}`, -Math.PI / 2);

    const angle = Math.atan2(-catB * scale, catA * scale);
    drawSquare(ox, oy, hip, '#06d6a0', `c²=${(hip * hip).toFixed(1)}`, angle);

    ctx.fillStyle = '#1a1a2e';
    ctx.font = '800 20px Nunito';
    ctx.textAlign = 'center';
    ctx.fillText(
      `${catA.toFixed(1)}² + ${catB.toFixed(1)}² = ${(catA * catA).toFixed(1)} + ${(catB * catB).toFixed(1)} = ${(hip * hip).toFixed(1)}`,
      w / 2, h - 40
    );
    ctx.font = '600 16px Nunito';
    ctx.fillStyle = '#5a6278';
    ctx.fillText(`c = ${hip.toFixed(2)}`, w / 2, h - 15);
  }

  function resize() {
    ({ ctx, w, h } = setupCanvas(canvas));
    draw();
  }

  const stopResize = onResize(canvas, resize);
  resize();

  return { destroy: () => stopResize() };
}
