export function setupCanvas(canvas) {
  const wrap = canvas.parentElement;
  const dpr = window.devicePixelRatio || 1;
  const rect = wrap.getBoundingClientRect();
  const w = Math.max(rect.width, 320);
  const h = Math.max(rect.height, 400);
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  canvas.style.width = w + 'px';
  canvas.style.height = h + 'px';
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, w, h, dpr };
}

export function createControlGroup(label, content) {
  const group = document.createElement('div');
  group.className = 'control-group';
  const lbl = document.createElement('label');
  lbl.textContent = label;
  group.appendChild(lbl);
  group.appendChild(content);
  return group;
}

export function createSlider(min, max, step, value, onInput) {
  const wrap = document.createElement('div');
  const input = document.createElement('input');
  input.type = 'range';
  input.min = min;
  input.max = max;
  input.step = step;
  input.value = value;
  const valSpan = document.createElement('span');
  valSpan.className = 'control-value';
  valSpan.textContent = value;
  input.addEventListener('input', () => {
    valSpan.textContent = input.value;
    onInput(parseFloat(input.value));
  });
  wrap.appendChild(input);
  wrap.appendChild(valSpan);
  return { wrap, input, valSpan };
}

export function createButton(text, className, onClick) {
  const btn = document.createElement('button');
  btn.className = 'btn ' + className;
  btn.textContent = text;
  btn.addEventListener('click', onClick);
  return btn;
}

export function createButtonRow(...buttons) {
  const row = document.createElement('div');
  row.className = 'btn-row';
  buttons.forEach(b => row.appendChild(b));
  return row;
}

export function fillConcept(conceptBox, title, text, formula) {
  conceptBox.innerHTML = `
    <h3>${title}</h3>
    <p>${text}</p>
    ${formula ? `<div class="formula">${formula}</div>` : ''}
  `;
}

export function loop(callback) {
  let id = null;
  let running = true;
  function frame(t) {
    if (!running) return;
    callback(t);
    id = requestAnimationFrame(frame);
  }
  id = requestAnimationFrame(frame);
  return () => {
    running = false;
    if (id) cancelAnimationFrame(id);
  };
}

export function onResize(canvas, callback) {
  const observer = new ResizeObserver(() => callback());
  observer.observe(canvas.parentElement);
  return () => observer.disconnect();
}
