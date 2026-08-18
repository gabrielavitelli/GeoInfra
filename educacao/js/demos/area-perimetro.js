import { setupCanvas, createControlGroup, createSlider, fillConcept, onResize } from '../engine.js';

export const meta = {
  id: 'area-perimetro',
  title: 'Área e Perímetro',
  description: 'Ajuste largura e altura e veja o perímetro e a área mudarem.',
  icon: '▭',
  tags: ['fund2', 'matematica'],
  accent: '#4361ee',
};

export function init(canvas, controlsPanel, conceptBox) {
  fillConcept(conceptBox,
    'Área e perímetro',
    'O perímetro é a soma de todos os lados (contorno). A área é o espaço dentro da figura, medido em quadrados.',
    'P = 2×(L + h) · A = L × h'
  );

  let largura = 6;
  let altura = 4;
  const escala = 28;

  const sliderL = createSlider(1, 12, 1, largura, v => { largura = v; draw(); });
  const sliderA = createSlider(1, 10, 1, altura, v => { altura = v; draw(); });
  controlsPanel.appendChild(createControlGroup('Largura (u)', sliderL.wrap));
  controlsPanel.appendChild(createControlGroup('Altura (u)', sliderA.wrap));

  let ctx, w, h;

  function draw() {
    if (!ctx) return;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = '#f8f9ff';
    ctx.fillRect(0, 0, w, h);

    const rectW = largura * escala;
    const rectH = altura * escala;
    const x = (w - rectW) / 2;
    const y = (h - rectH) / 2 - 30;

    ctx.fillStyle = 'rgba(67, 97, 238, 0.15)';
    ctx.strokeStyle = '#4361ee';
    ctx.lineWidth = 3;
    ctx.fillRect(x, y, rectW, rectH);
    ctx.strokeRect(x, y, rectW, rectH);

    ctx.strokeStyle = 'rgba(26, 26, 46, 0.15)';
    ctx.lineWidth = 1;
    for (let i = 1; i < largura; i++) {
      ctx.beginPath();
      ctx.moveTo(x + i * escala, y);
      ctx.lineTo(x + i * escala, y + rectH);
      ctx.stroke();
    }
    for (let j = 1; j < altura; j++) {
      ctx.beginPath();
      ctx.moveTo(x, y + j * escala);
      ctx.lineTo(x + rectW, y + j * escala);
      ctx.stroke();
    }

    const perimetro = 2 * (largura + altura);
    const area = largura * altura;

    ctx.fillStyle = '#1a1a2e';
    ctx.font = '700 16px Nunito';
    ctx.textAlign = 'center';
    ctx.fillText(`${largura} u`, x + rectW / 2, y - 12);
    ctx.save();
    ctx.translate(x - 16, y + rectH / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.fillText(`${altura} u`, 0, 0);
    ctx.restore();

    ctx.font = '800 22px Nunito';
    ctx.fillStyle = '#4361ee';
    ctx.fillText(`Perímetro = ${perimetro} u`, w / 2, h - 70);
    ctx.fillStyle = '#06d6a0';
    ctx.fillText(`Área = ${area} u² (${largura}×${altura} quadradinhos)`, w / 2, h - 40);
  }

  function resize() {
    ({ ctx, w, h } = setupCanvas(canvas));
    draw();
  }

  const stopResize = onResize(canvas, resize);
  resize();

  return { destroy: () => stopResize() };
}
