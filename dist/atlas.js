const byId = (id) => document.getElementById(id);
const canvas = byId('map');
const context = canvas.getContext('2d');
const stage = byId('map-stage');
const card = byId('country-card');
const backgroundDialog = byId('background-dialog');
const loadState = byId('load-state');
const hitTest = document.createElement('canvas').getContext('2d');
const terrainCanvas = document.createElement('canvas');
const terrainContext = terrainCanvas.getContext('2d');
const pointers = new Map();
const backgroundHeadings = new Set(['往昔', '现状', 'The Past', 'The Present', 'Past', 'Present', 'Current Situation']);
const copy = {
  zh: {
    title: '未有之梦 · 世界图志', map: '国家政治地图', settings: '地图设置', mode: '仅推荐国',
    help: '政治地图。拖动平移，滚轮或双指缩放，点击国家查看档案。键盘可用方向键平移，加减键缩放，回车查看画面中央的国家。',
    loading: '正在展开地图…', error: '地图加载失败，请通过本地预览服务器打开。',
    card: '国家档案', close: '关闭国家档案', capital: '首都', religion: '国教', culture: '主流文化',
    ruler: '统治者', age: '年龄', adm: '行政能力', dip: '外交能力', mil: '军事能力',
    diplomacy: '外交', independent: '独立国家', unknown: '暂无资料',
    recommended: '推荐国', notRecommended: '非推荐国',
    background: '背景介绍', detailedBackground: '详细背景', closeBackground: '关闭详细背景',
    subject: (name, type) => name + '的' + type,
  },
  en: {
    title: 'The Dream yet to be Dreamed · World Atlas', map: 'Political Map', settings: 'Map settings',
    mode: 'Recommended Only',
    help: 'Political map. Drag to pan, scroll or pinch to zoom, and select a country to open its dossier. Use the arrow keys to pan, plus and minus to zoom, and Enter to select the country at the center.',
    loading: 'Loading map…', error: 'The map could not load. Open it through the local preview server.',
    card: 'Country Dossier', close: 'Close country dossier', capital: 'Capital', religion: 'State Religion',
    culture: 'Primary Culture', ruler: 'Ruler', age: 'Age',
    adm: 'Administrative Skill', dip: 'Diplomatic Skill', mil: 'Military Skill',
    diplomacy: 'Diplomacy', independent: 'Independent Nation', unknown: 'No record',
    background: 'Background', detailedBackground: 'Detailed Background', closeBackground: 'Close detailed background',
    recommended: 'Recommended', notRecommended: 'Not Recommended',
    subject: (name, type) => type + ' of ' + name,
  },
};

let language = 'zh';
try {
  if (localStorage.getItem('dd-language') === 'en') language = 'en';
} catch {
  // Language switching still works when storage is unavailable.
}
let recommendedOnly = false;
let atlas, relief, land, neutralProvinces, activeCountries, countriesByArea, countriesByTag;
let canvasFont;
let labelFontsReady = false;
let selected = null;
let hovered = null;
let width = 0;
let height = 0;
let ratio = 1;
let view = { x: 0, y: 0, scale: 1 };
let frame = 0;
let gesture = null;
let terrainCache;

function loadImage(url) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error('无法读取地图资源 ' + url));
    image.src = url;
  });
}

function scheduleDraw() {
  if (!frame) frame = requestAnimationFrame(draw);
}

function localized(entity, field) {
  return (language === 'en' ? entity.en?.[field] : entity[field]) || entity[field];
}

function updateLanguage() {
  const words = copy[language];
  document.documentElement.lang = language === 'en' ? 'en' : 'zh-CN';
  canvasFont = getComputedStyle(document.documentElement).getPropertyValue('--panel-font').trim();
  document.title = words.title;
  stage.setAttribute('aria-label', words.map);
  canvas.setAttribute('aria-label', words.help);
  card.setAttribute('aria-label', words.card);
  byId('map-options').setAttribute('aria-label', words.settings);
  const close = byId('close-card');
  close.setAttribute('aria-label', words.close);
  close.title = words.close;
  const closeBackground = byId('close-background');
  closeBackground.setAttribute('aria-label', words.closeBackground);
  closeBackground.title = words.closeBackground;
  byId('mode-toggle').textContent = words.mode;
  const languageButton = byId('language-toggle');
  languageButton.textContent = language === 'zh' ? 'EN' : '中文';
  languageButton.lang = language === 'zh' ? 'en' : 'zh-CN';
  languageButton.setAttribute('aria-label', language === 'zh' ? 'Switch to English' : '切换到中文');
  for (const node of document.querySelectorAll('[data-i18n]')) node.textContent = words[node.dataset.i18n];
  if (atlas) selectCountry(selected);
  else loadState.textContent = words.loading;
}

function applyMapMode() {
  activeCountries = recommendedOnly ? atlas.countries.filter((country) => country.recommended) : atlas.countries;
  countriesByArea = activeCountries.slice().sort((a, b) => b.area - a.area);
  byId('mode-toggle').setAttribute('aria-pressed', recommendedOnly);
  if (recommendedOnly && selected && !selected.recommended) selectCountry(null);
  setHover(null);
  scheduleDraw();
}

function draw() {
  frame = 0;
  const { scale } = view;
  const left = -Math.round(view.x * ratio);
  const top = -Math.round(view.y * ratio);
  const x = -left / ratio, y = -top / ratio;
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  const water = context.createLinearGradient(0, 0, width * .62, height);
  water.addColorStop(0, '#153542');
  water.addColorStop(.55, '#102b38');
  water.addColorStop(1, '#0b1f2b');
  context.fillStyle = water;
  context.fillRect(0, 0, width, height);
  drawCachedTerrain(left, top, scale);
  if (hovered || selected) {
    context.save();
    context.translate(x, y);
    context.scale(scale, scale);
    context.beginPath();
    context.rect(0, 0, atlas.width, atlas.height);
    context.clip();
    context.lineJoin = 'round';
    context.lineCap = 'round';
    if (hovered && hovered !== selected) paintCountry(hovered, '#fbe8ba1e', '#f0dfad99', 1);
    if (selected) paintCountry(selected, '#ffd48d20', '#f5d49a', 1.6);
    context.restore();
  }
  drawLabels(x, y);
}

function drawCachedTerrain(left, top, scale) {
  const { width: pixelWidth, height: pixelHeight } = canvas;
  const worldToPixel = scale * ratio;
  const worldWidth = Math.ceil(atlas.width * worldToPixel);
  const worldHeight = Math.ceil(atlas.height * worldToPixel);
  const right = Math.min(worldWidth, left + pixelWidth);
  const bottom = Math.min(worldHeight, top + pixelHeight);
  const cache = terrainCache;
  const sameMap = cache && cache.scale === scale && cache.ratio === ratio &&
    cache.pixelWidth === pixelWidth && cache.pixelHeight === pixelHeight && cache.countries === activeCountries;
  if (!sameMap || Math.max(0, left) < cache.left || Math.max(0, top) < cache.top ||
      right > cache.left + terrainCanvas.width || bottom > cache.top + terrainCanvas.height) {
    // Keep extra pixels around the viewport so a pan can reuse the same raster.
    const padX = Math.min(640, Math.ceil(pixelWidth * .3));
    const padY = Math.min(640, Math.ceil(pixelHeight * .3));
    const cacheWidth = Math.min(worldWidth, pixelWidth + padX * 2);
    const cacheHeight = Math.min(worldHeight, pixelHeight + padY * 2);
    const cacheLeft = Math.max(0, Math.min(left - padX, worldWidth - cacheWidth));
    const cacheTop = Math.max(0, Math.min(top - padY, worldHeight - cacheHeight));
    if (terrainCanvas.width !== cacheWidth) terrainCanvas.width = cacheWidth;
    if (terrainCanvas.height !== cacheHeight) terrainCanvas.height = cacheHeight;
    const shiftX = sameMap ? cache.left - cacheLeft : cacheWidth;
    const shiftY = sameMap ? cache.top - cacheTop : cacheHeight;
    updateTerrainCache(cacheLeft, cacheTop, shiftX, shiftY, scale);
    terrainCache = { left: cacheLeft, top: cacheTop, scale, ratio, pixelWidth, pixelHeight, countries: activeCountries };
  }

  context.save();
  context.resetTransform();
  context.drawImage(terrainCanvas, terrainCache.left - left, terrainCache.top - top);
  context.restore();
}

function updateTerrainCache(left, top, shiftX, shiftY, scale) {
  const { width: cacheWidth, height: cacheHeight } = terrainCanvas;
  const target = terrainContext;
  const worldToPixel = scale * ratio;
  target.save();
  if (Math.abs(shiftX) < cacheWidth && Math.abs(shiftY) < cacheHeight) {
    // Preserve the overlapping pixels and paint only the exposed strips.
    target.globalCompositeOperation = 'copy';
    target.drawImage(terrainCanvas, shiftX, shiftY);
    target.globalCompositeOperation = 'source-over';
    target.beginPath();
    if (shiftX) target.rect(shiftX > 0 ? 0 : cacheWidth + shiftX, 0, Math.abs(shiftX), cacheHeight);
    if (shiftY) target.rect(0, shiftY > 0 ? 0 : cacheHeight + shiftY, cacheWidth, Math.abs(shiftY));
    target.clip();
  } else {
    target.clearRect(0, 0, cacheWidth, cacheHeight);
  }
  target.setTransform(worldToPixel, 0, 0, worldToPixel, -left, -top);
  drawTerritories(target, scale, left / worldToPixel, top / worldToPixel,
    (left + cacheWidth) / worldToPixel, (top + cacheHeight) / worldToPixel);
  target.restore();
}

function drawProvinceLines(context, path, territory) {
  context.save();
  context.clip(territory, 'evenodd');
  context.stroke(path);
  context.restore();
}

function drawTerritories(context, scale, left, top, right, bottom) {
  const visible = activeCountries.filter(({ bounds: [px, py, w, h] }) =>
    px <= right && py <= bottom && px + w >= left && py + h >= top);

  const showProvinces = scale >= .38;
  context.save();
  context.beginPath();
  context.rect(0, 0, atlas.width, atlas.height);
  context.clip();
  context.lineJoin = 'round';
  context.lineCap = 'round';
  context.lineWidth = .7 / scale;
  context.strokeStyle = 'rgba(26, 36, 35, ' + Math.min(.56, .25 + scale * .1) + ')';

  context.fillStyle = '#5b594c';
  context.fill(land, 'evenodd');
  if (showProvinces && !recommendedOnly) drawProvinceLines(context, neutralProvinces, land);
  for (const country of visible) {
    context.fillStyle = country.fill;
    context.fill(country.path, 'evenodd');
  }

  context.save();
  context.clip(land, 'evenodd');
  context.globalAlpha = .85;
  context.drawImage(relief, 0, 0);
  context.restore();

  if (showProvinces) {
    for (const country of visible) {
      if (country.provincePath) drawProvinceLines(context, country.provincePath, country.path);
    }
  }
  context.lineWidth = (scale < .45 ? .62 : 1) / scale;
  context.strokeStyle = '#1c2a29';
  for (const country of visible) context.stroke(country.path);
  context.lineWidth = (scale < .45 ? .72 : 1.12) / scale;
  context.strokeStyle = '#80949cbb';
  context.stroke(land);
  context.restore();
}

function paintCountry(country, fill, stroke, lineWidth) {
  const { scale } = view;
  context.fillStyle = fill;
  context.strokeStyle = stroke;
  context.lineWidth = lineWidth / scale;
  if (country.radius * scale >= 2) {
    context.fill(country.path, 'evenodd');
    context.stroke(country.path);
  } else {
    context.beginPath();
    context.arc(...country.center, 4 / scale, 0, Math.PI * 2);
    context.stroke();
  }
}

function drawLabels(x, y) {
  if (!labelFontsReady) return;
  const { scale } = view;
  context.textAlign = 'center';
  context.textBaseline = 'middle';
  context.lineJoin = 'round';
  const labels = [];
  const candidates = selected ? [selected, ...countriesByArea.filter((country) => country !== selected)] : countriesByArea;
  for (const country of candidates) {
    const px = x + country.center[0] * scale;
    const py = y + country.center[1] * scale;
    const room = country.radius * scale;
    if (px < 12 || px > width - 12 || py < 14 || py > height - 14) continue;
    if (room < 12 && country !== selected) continue;
    const size = Math.min(22, Math.max(11, 9 + Math.sqrt(room) * .88));
    const labelY = py - (room < 12 ? 13 : 0);
    context.font = size + 'px ' + canvasFont;
    const name = localized(country, 'name');
    const halfWidth = context.measureText(name).width / 2;
    if (halfWidth > Math.max(room * 1.42, 23) && country !== selected) continue;
    const box = { left: px - halfWidth - 7, right: px + halfWidth + 7, top: labelY - size * .75, bottom: labelY + size * .75 };
    if (labels.some((label) => label.left < box.right && label.right > box.left && label.top < box.bottom && label.bottom > box.top)) continue;
    labels.push(box);
    context.lineWidth = size > 15 ? 2.3 : 1.9;
    context.strokeStyle = '#152221db';
    context.fillStyle = country === selected ? '#ffdda0' : '#f7f0db';
    context.strokeText(name, px, labelY);
    context.fillText(name, px, labelY);
  }
}

function minScale() {
  return Math.min(width / atlas.width, height / atlas.height) * .94;
}

function keepMapInView() {
  const mapWidth = atlas.width * view.scale;
  const mapHeight = atlas.height * view.scale;
  const marginX = Math.min(width * .45, mapWidth * .5);
  const marginY = Math.min(height * .45, mapHeight * .5);
  view.x = Math.min(width - marginX, Math.max(marginX - mapWidth, view.x));
  view.y = Math.min(height - marginY, Math.max(marginY - mapHeight, view.y));
}

function centerOn(px, py, scale, targetX = width / 2, targetY = height / 2) {
  view = { x: targetX - px * scale, y: targetY - py * scale, scale };
  keepMapInView();
  setHover(null);
  scheduleDraw();
}

function resetView() {
  const scale = Math.min(Math.max(width / 1150, .51), height / 560, 1.9);
  centerOn(3017, 527, scale);
}

function resize() {
  const nextWidth = canvas.clientWidth;
  const nextHeight = canvas.clientHeight;
  const nextRatio = Math.min(window.devicePixelRatio || 1, 2);
  if (!nextWidth || !nextHeight || (width === nextWidth && height === nextHeight && ratio === nextRatio)) return;
  const oldWidth = width;
  const centerX = (width / 2 - view.x) / view.scale;
  const centerY = (height / 2 - view.y) / view.scale;
  width = nextWidth;
  height = nextHeight;
  ratio = nextRatio;
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  if (oldWidth) centerOn(centerX, centerY, Math.max(minScale(), view.scale));
  else resetView();
}

function zoomFrom(origin, factor, startX, startY, targetX = startX, targetY = startY) {
  const minimum = minScale();
  const scale = Math.min(4, Math.max(minimum, origin.scale * factor));
  if (scale === minimum) centerOn(atlas.width / 2, atlas.height / 2, scale);
  else centerOn((startX - origin.x) / origin.scale, (startY - origin.y) / origin.scale, scale, targetX, targetY);
}

function zoom(factor, x = width / 2, y = height / 2) {
  zoomFrom(view, factor, x, y);
}

function countryAt(x, y) {
  const col = (x - view.x) / view.scale;
  const row = (y - view.y) / view.scale;
  return activeCountries.find(({ bounds: [px, py, w, h], path }) =>
    col >= px && col <= px + w && row >= py && row <= py + h &&
    hitTest.isPointInPath(path, col, row, 'evenodd')) ?? null;
}

function setHover(country) {
  if (hovered === country) return;
  hovered = country;
  canvas.classList.toggle('pannable-country', Boolean(country));
  scheduleDraw();
}

function renderDiplomacy(groups) {
  const rows = byId('diplomacy-rows');
  byId('diplomacy').hidden = !groups.length;
  rows.replaceChildren(...groups.map(({ icon, label, en, contacts }) => {
    const relation = language === 'en' ? en || label : label;
    const row = document.createElement('div');
    row.className = 'diplomacy-row';
    const symbol = document.createElement('img');
    symbol.className = 'relation-icon';
    symbol.src = 'data/panel/' + icon + '.webp';
    symbol.alt = relation;
    symbol.title = relation;

    const shields = document.createElement('div');
    shields.className = 'relation-shields';
    for (const contact of contacts) {
      const { tag, detail } = contact;
      const country = countriesByTag.get(tag);
      const name = country ? localized(country, 'name') : tag;
      const shield = document.createElement('span');
      shield.className = 'relation-shield';
      shield.title = [name, relation, language === 'en' ? contact.en : detail].filter(Boolean).join(' · ');
      if (country?.flag) {
        const image = document.createElement('img');
        image.src = 'data/flags/mini/' + tag + '.webp';
        image.alt = name;
        shield.append(image);
      } else {
        shield.classList.add('relation-code');
        shield.textContent = tag;
      }
      shields.append(shield);
    }
    row.append(symbol, shields);
    return row;
  }));
}

function rulerFor(country) {
  const visited = new Set();
  while (country && !visited.has(country.tag)) {
    if (country.ruler) return country.ruler;
    const overlordTag = country.overlord?.tag;
    const union = country.diplomacy?.find((group) => group.icon === 'union-overlord');
    if (!union?.contacts.some(({ tag }) => tag === overlordTag)) break;
    visited.add(country.tag);
    country = countriesByTag.get(overlordTag);
  }
  return null;
}

function renderRuler(ruler) {
  byId('ruler-details').hidden = !ruler;
  byId('ruler-missing').hidden = Boolean(ruler);
  if (!ruler) return;
  if (typeof ruler === 'string') ruler = { name: ruler };
  const words = copy[language];
  const gender = byId('ruler-gender');
  gender.hidden = !ruler.gender;
  if (ruler.gender) gender.src = 'data/panel/' + ruler.gender + '.webp';
  const fullName = localized(ruler, 'fullName') ||
    [localized(ruler, 'name'), localized(ruler, 'dynasty')].filter(Boolean).join(' ');
  byId('ruler-name').textContent = [localized(ruler, 'title'), fullName].filter(Boolean).join(' ');
  byId('ruler-age').hidden = ruler.age == null;
  byId('ruler-years').textContent = ruler.age ?? '';
  for (const stat of ['adm', 'dip', 'mil']) {
    const value = ruler.stats?.[stat] ?? '—';
    byId(stat).textContent = value;
    byId('ruler-' + stat).title = words[stat] + ' · ' + value;
    byId('ruler-' + stat).setAttribute('aria-label', words[stat] + ' ' + value);
  }
}

function openBackground() {
  const background = selected && localized(selected, 'background');
  if (!background?.length || backgroundDialog.open) return;
  byId('reader-title').textContent = localized(selected, 'name') + ' · ' + copy[language].background;
  const paragraphs = background.map((paragraph) => {
    const node = document.createElement(backgroundHeadings.has(paragraph) ? 'h3' : 'p');
    node.textContent = paragraph;
    return node;
  });
  const chapters = byId('background-chapters');
  chapters.lang = byId('background-text').lang;
  chapters.replaceChildren(...paragraphs);
  backgroundDialog.showModal();
  byId('reader-scroll').scrollTop = 0;
}

function selectCountry(country) {
  const changed = selected !== country;
  selected = country;
  card.hidden = !country;
  if (country) {
    const words = copy[language];
    byId('country-name').textContent = localized(country, 'name');
    byId('government').textContent = localized(country, 'government');
    const subject = country.overlord;
    const overlord = subject && countriesByTag.get(subject.tag);
    byId('country-status').textContent = overlord
      ? words.subject(localized(overlord, 'name'), language === 'en' ? subject.en : subject.label)
      : words.independent;
    for (const key of ['capital', 'religion', 'culture']) {
      byId(key).textContent = localized(country, key) || words.unknown;
    }
    const religionIcon = byId('religion-icon');
    religionIcon.hidden = !country.religionIcon;
    if (country.religionIcon) religionIcon.src = 'data/panel/religions/' + country.religionIcon + '.webp';
    renderRuler(rulerFor(country));
    const flag = byId('flag');
    flag.hidden = !country.flag;
    if (country.flag) flag.src = 'data/flags/' + country.tag + '.webp?v=shield';
    const recommended = country.recommended;
    const recommendation = byId('recommendation');
    recommendation.classList.toggle('is-recommended', recommended);
    recommendation.classList.toggle('is-unrecommended', !recommended);
    byId('recommendation-icon').src = 'data/panel/' + (recommended ? 'yes' : 'no') + '.webp';
    byId('recommendation-text').textContent = recommended ? words.recommended : words.notRecommended;
    renderDiplomacy(country.diplomacy || []);
    const introduction = localized(country, 'background')?.find((paragraph) => !backgroundHeadings.has(paragraph));
    byId('country-background').hidden = !introduction;
    const backgroundText = byId('background-text');
    backgroundText.lang = language === 'en' && country.en?.background ? 'en' : 'zh-CN';
    backgroundText.textContent = introduction || '';
    if (changed) card.querySelector('.card-scroll').scrollTop = 0;
  }
  setHover(null);
  scheduleDraw();
}

function pointerCenter() {
  const [first, second] = pointers.values();
  if (!second) return { ...first, distance: 0 };
  return {
    x: (first.x + second.x) / 2,
    y: (first.y + second.y) / 2,
    distance: Math.max(1, Math.hypot(first.x - second.x, first.y - second.y)),
  };
}

function startGesture(moved) {
  gesture = { start: pointerCenter(), origin: { ...view }, moved };
  setHover(null);
}

function movePointer(event) {
  const x = event.offsetX, y = event.offsetY;
  if (!pointers.has(event.pointerId)) {
    if (!gesture && event.pointerType === 'mouse') setHover(countryAt(x, y));
    return;
  }

  pointers.set(event.pointerId, { x, y });
  const center = pointerCenter();
  const { start, origin } = gesture;
  if (center.distance) {
    zoomFrom(origin, center.distance / start.distance, start.x, start.y, center.x, center.y);
    return;
  }
  const dx = center.x - start.x, dy = center.y - start.y;
  if (!gesture.moved && Math.hypot(dx, dy) < 4) return;
  gesture.moved = true;
  view.x = origin.x + dx;
  view.y = origin.y + dy;
  keepMapInView();
  canvas.classList.add('dragging');
  scheduleDraw();
}

function endPointer(event) {
  if (!pointers.delete(event.pointerId)) return;
  if (pointers.size) {
    startGesture(true);
    return;
  }
  if (event.type === 'pointerup' && !gesture.moved) {
    selectCountry(countryAt(event.offsetX, event.offsetY));
  }
  gesture = null;
  canvas.classList.remove('dragging');
}

function bind() {
  const modeButton = byId('mode-toggle');
  modeButton.disabled = false;
  modeButton.addEventListener('click', () => {
    recommendedOnly = !recommendedOnly;
    applyMapMode();
  });
  const languageButton = byId('language-toggle');
  languageButton.disabled = false;
  languageButton.addEventListener('click', () => {
    language = language === 'zh' ? 'en' : 'zh';
    try {
      localStorage.setItem('dd-language', language);
    } catch {
      // Storage is optional.
    }
    updateLanguage();
  });
  canvas.addEventListener('wheel', (event) => {
    event.preventDefault();
    zoom(Math.exp(-Math.max(-160, Math.min(160, event.deltaY)) * .0022), event.offsetX, event.offsetY);
  }, { passive: false });
  canvas.addEventListener('pointerdown', (event) => {
    if (event.button !== 0 || pointers.size >= 2) return;
    canvas.setPointerCapture(event.pointerId);
    pointers.set(event.pointerId, { x: event.offsetX, y: event.offsetY });
    startGesture(pointers.size > 1);
  });
  canvas.addEventListener('pointermove', movePointer);
  for (const event of ['pointerup', 'pointercancel', 'lostpointercapture']) {
    canvas.addEventListener(event, endPointer);
  }
  canvas.addEventListener('pointerleave', () => setHover(null));

  const closeCard = () => {
    selectCountry(null);
    canvas.focus({ preventScroll: true });
  };
  byId('close-card').addEventListener('click', closeCard);
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && selected && !backgroundDialog.open) closeCard();
  });
  canvas.addEventListener('keydown', (event) => {
    const directions = { ArrowLeft: [45, 0], ArrowRight: [-45, 0], ArrowUp: [0, 45], ArrowDown: [0, -45] };
    if (event.key in directions) {
      event.preventDefault();
      const [dx, dy] = directions[event.key];
      view.x += dx;
      view.y += dy;
      keepMapInView();
      setHover(null);
      scheduleDraw();
    } else if (event.key === '+' || event.key === '=') zoom(1.3);
    else if (event.key === '-') zoom(1 / 1.3);
    else if (event.key === '0') resetView();
    else if (event.key === 'Enter') selectCountry(countryAt(width / 2, height / 2));
  });
}

async function start() {
  const [data, terrainImage] = await Promise.all([
    fetch('data/atlas.json', { cache: 'no-cache' }).then((response) => {
      if (!response.ok) throw new Error('无法读取国家资料');
      return response.json();
    }),
    loadImage('data/relief.webp'),
  ]);
  atlas = data;
  relief = terrainImage;
  land = new Path2D(atlas.land);
  neutralProvinces = new Path2D(atlas.neutralProvinces || '');
  const neutral = [129, 122, 101];
  for (const country of atlas.countries) {
    country.path = new Path2D(country.outline);
    if (country.provinces) country.provincePath = new Path2D(country.provinces);
    country.fill = 'rgb(' + country.color.map((value, i) => Math.round(value * .78 + neutral[i] * .22)).join(',') + ')';
  }
  countriesByTag = new Map(atlas.countries.map((country) => [country.tag, country]));
  applyMapMode();
  bind();
  resize();
  cancelAnimationFrame(frame);
  draw();
  new ResizeObserver(resize).observe(stage);
  loadState.hidden = true;
}

updateLanguage();
const backgroundButton = byId('open-background');
backgroundButton.addEventListener('click', openBackground);
byId('close-background').addEventListener('click', () => backgroundDialog.close());
backgroundDialog.addEventListener('close', () => {
  if (!backgroundDialog.open && !card.hidden && !backgroundButton.hidden) {
    backgroundButton.focus({ preventScroll: true });
  }
});
Promise.allSettled(['DD Panel Chinese', 'DD Panel English'].map((font) =>
  document.fonts.load('16px "' + font + '"')
)).then(() => {
  labelFontsReady = true;
  if (atlas) scheduleDraw();
});
start().catch((error) => {
  loadState.textContent = copy[language].error;
  console.error(error);
});
