import { setupCanvas, createControlGroup, createSlider, fillConcept, loop, onResize } from '../engine.js';

export const meta = {
  id: 'funcao-afim',
  title: 'Gráfico de Função Afim',
  description: 'Veja como y = ax + b forma uma reta no plano cartesiano.',
  icon: '📈',
  tags: ['fund2', 'matematica'],
  accent: '#4361ee',
};

export function init(canvas, controlsPanel, conceptBox) {
  fillConcept(conceptBox,
    'Função do 1º grau',
    'Uma função afim tem a forma y = ax + b. O coeficiente a inclina a reta; b desloca no eixo y.',
    'y = ax + b'
  );

  let a = 1;
  let b = 2;
  let t = 0;

  const sliderA = createSlider(-3, 3, 0.1, a, v => { a = v; });
  const sliderB = createSlider(-5, 5, 0.5, b, v => { b = v; });
  controlsPanel.appendChild(createControlGroup('Coeficiente a (inclinação)', sliderA.wrap));
  controlsPanel.appendChild(createControlGroup('Coeficiente b (intercepto)', sliderB.wrap));

  let ctx, w, h;
  let stopLoop;

  function drawAxes(originX, originY, scale) {
    ctx.strokeStyle = '#ccc';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(0, originY);
    ctx.lineTo(w, originY);
    ctx.moveTo(originX, 0);
    ctx.lineTo(originX, h);
    ctx.stroke();

    ctx.fillStyle = '#5a6278';
    ctx.font = '12px Nunito';
    for (let i = -10; i <= 10; i++) {
      if (i === 0) continue;
      const px = originX + i * scale;
      if (px > 0 && px < w) {
        ctx.fillText(String(i), px - 4, originY + 16);
        ctx.beginPath();
        ctx.moveTo(px, originY - 4);
        ctx.lineTo(px, originY + 4);
        ctx.stroke();
      }
      const py = originY - i * scale;
      if (py > 0 && py < h) {
        ctx.fillText(String(i), originX + 6, py + 4);
        ctx.beginPath();
        ctx.moveTo(originX - 4, py);
        ctx.lineTo(originX + 4, py);
        ctx.stroke();
      }
    }
    ctx.fillText('x', w - 20, originY + 16);
    ctx.fillText('y', originX + 8, 16);
  }

  function draw() {
    if (!ctx) return;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = '#f8f9ff';
    ctx.fillRect(0, 0, w, h);

    const originX = w / 2;
    const originY = h / 2 + 20;
    const scale = 28;
    drawAxes(originX, originY, scale);

    ctx.strokeStyle = '#4361ee';
    ctx.lineWidth = 3;
    ctx.beginPath();
    for (let px = 0; px <= w; px++) {
      const x = (px - originX) / scale;
      const y = a * x + b;
      const py = originY - y * scale;
      if (px === 0) ctx.moveTo(px, py);
      else ctx.lineTo(px, py);
    }
    ctx.stroke();

    const xPoint = Math.sin(t * 0.002) * 5;
    const yPoint = a * xPoint + b;
    const px = originX + xPoint * scale;
    const py = originY - yPoint * scale;
    ctx.fillStyle = '#f72585';
    ctx.beginPath();
    ctx.arc(px, py, 8, 0, Math.PI * 2);
    ctx.fill();

    ctx.fillStyle = '#1a1a2e';
    ctx.font = '800 22px Nunito';
    ctx.textAlign = 'center';
    const sign = b >= 0 ? '+' : '−';
    ctx.fillText(`y = ${a.toFixed(1)}x ${sign} ${Math.abs(b).toFixed(1)}`, w / 2, 30);
    ctx.font = '600 14px Nunito';
    ctx.fillStyle = '#f72585';
    ctx.fillText(`Ponto: (${xPoint.toFixed(1)}, ${yPoint.toFixed(1)})`, w / 2, 52);
  }

  function resize() {
    ({ ctx, w, h } = setupCanvas(canvas));
  }

  const stopResize = onResize(canvas, resize);
  resize();
  stopLoop = loop((time) => { t = time; draw(); });

  return { destroy: () => { stopLoop(); stopResize(); } };
}
