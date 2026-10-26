/* Benchmark headless: bandingkan AI pada file engine mana pun (tanpa browser).
   Pemakaian: node tools/ai-bench.cjs <file.html> [ronde] [langkahMaks] */
const fs = require('fs');
const path = require('path');

const file = process.argv[2] || path.join(__dirname, '..', 'engine.html');
const ROUNDS = +(process.argv[3] || 8), MAXT = +(process.argv[4] || 6000);
const QUERY = process.argv[5] || '';
const src = fs.readFileSync(file, 'utf8');
const a = src.indexOf('//LOGIC_START'), b = src.indexOf('//LOGIC_END');
const code = src.slice(a + '//LOGIC_START'.length, b);
const fly = new Set();
const api = new Function('QS', 'document', 'console', 'fly', code + `
  return {
    W,H,N,step,reset,setTrace:(a)=>{_aiTrace=a},
    get snake(){return snake}, get apples(){return apples}, get occ(){return occ},
    get eaten(){return eaten}
  };
`)(new URLSearchParams(QUERY), { body: { classList: { add() {} } } }, { warn() {}, log() {} }, fly);

function pickFreeCell() {
  const { W, H, N } = api, occ = api.occ, snake = api.snake;
  const tail = snake[snake.length - 1];
  const seen = new Uint8Array(N), q = [snake[0]]; seen[snake[0]] = 1;
  const free = [];
  for (let h = 0; h < q.length; h++) {
    const c = q[h], x = c % W, y = (c / W) | 0;
    for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
      const nx = x + dx, ny = y + dy;
      if (nx < 0 || ny < 0 || nx >= W || ny >= H) continue;
      const m = ny * W + nx;
      if (seen[m] || (occ[m] && m !== tail)) continue;
      seen[m] = 1; q.push(m);
      if (!occ[m] && !api.apples.has(m)) free.push(m);
    }
  }
  return free.length ? free[(Math.random() * free.length) | 0] : -1;
}

/* jarak BFS terdekat ke apel (ekor dianggap bebas) */
function dmin() {
  const { W, H, N } = api, occ = api.occ, snake = api.snake;
  const tail = snake[snake.length - 1];
  const dist = new Int16Array(N).fill(-1);
  const q = [snake[0]]; dist[snake[0]] = 0;
  let best = Infinity;
  for (let h = 0; h < q.length; h++) {
    const c = q[h];
    if (api.apples.has(c)) { best = Math.min(best, dist[c]); continue; }
    if (dist[c] >= best) continue;
    const x = c % W, y = (c / W) | 0;
    for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
      const nx = x + dx, ny = y + dy;
      if (nx < 0 || ny < 0 || nx >= W || ny >= H) continue;
      const m = ny * W + nx;
      if (dist[m] >= 0 || (occ[m] && m !== tail)) continue;
      dist[m] = dist[c] + 1; q.push(m);
    }
  }
  return best;
}

let wins = 0, deaths = 0, collisions = 0, lenSum = 0, eats = 0, closeRuns = 0, closeEarly = 0;
const hist = [];
const deathLens = [], lat = [], latEarly = [];
const EARLY = 90;   // "fase awal" = panjang < 35% papan (256)
for (let r = 0; r < ROUNDS; r++) {
  api.reset(); api.apples.clear(); fly.clear();
  let ended = false, lastLen = api.snake.length, since = 0, run = 0;
  for (let t = 0; t < MAXT; t++) {
    while (api.apples.size < 2) { const c = pickFreeCell(); if (c < 0) break; api.apples.add(c); }
    const d = dmin();
    const tr = process.env.TRACE ? [] : null;
    if (tr) api.setTrace(tr);
    const res = api.step();
    if (tr) api.setTrace(null);
    if (tr) hist.push({ t, len: api.snake.length, tr });
    if (tr && hist.length > 40) hist.shift();
    if (tr && process.env.HEAD && t < 45) {
      const pk = tr.find(x => x.pick !== undefined);
      const best3 = tr.filter(x => x.k !== undefined).sort((a, b) => a.sc - b.sc).slice(0, 3)
        .map(x => x.k + ':m' + x.m + ':sc' + x.sc).join(' | ');
      console.log('  t=' + t, 'len=' + api.snake.length, 'dmin=' + d, 'mv=' + res,
        'pick=' + (pk ? pk.pick : '?'), '||', best3);
    }
    since++;
    if (new Set(api.snake).size !== api.snake.length) { collisions++; ended = true; break; }
    if (api.snake.length > lastLen) {
      lat.push(since); if (lastLen < EARLY) latEarly.push(since);
      since = 0; lastLen = api.snake.length; eats++; run = 0;
    }
    else if (d <= 2 && api.snake.length / api.N < .6) {
      if (++run > 4) { (api.snake.length < EARLY ? (closeEarly++) : (closeRuns++)); run = 0; }
    }
    else run = 0;
    if (res === 2) {
      if (api.snake.length >= api.N) wins++; else {
        deaths++; deathLens.push(api.snake.length);
        if (process.env.TRACE) {
          for (const h of hist.slice(-8)) {
            const candsTxt = h.tr.filter(x => x.k !== undefined).length;
            const pick = h.tr.find(x => x.pick !== undefined);
            console.log('   t=' + h.t, 'len=' + h.len, 'cands=' + candsTxt, 'pick=' + (pick ? pick.pick : '?'),
              h.tr.filter(x => x.k !== undefined).sort((a, b) => a.sc - b.sc).slice(0, 2)
                .map(x => x.k + ':m' + x.m + ':sc' + x.sc).join(' | '));
          }
        }
        if (process.env.DUMP) {
          const { W, H } = api, occ = api.occ, snake = api.snake;
          const head = snake[0], tail = snake[snake.length - 1];
          console.log('  DEATH ronde', r + 1, 'langkah', t, 'len', snake.length, 'kepala', head, 'ekor', tail, 'apel', [...api.apples]);
          for (let y = 0; y < H; y++) {
            let row = '   ';
            for (let x = 0; x < W; x++) {
              const c = y * W + x;
              row += (c === head ? 'H' : c === tail ? 'T' : api.apples.has(c) ? '*' : occ[c] ? '#' : '.') + ' ';
            }
            console.log(row);
          }
          const nb = [[1, 0], [-1, 0], [0, 1], [0, -1]].map(([dx, dy]) => (head % W) + dx + ',' + (((head / W) | 0) + dy));
          console.log('  tetangga kepala (x,y):', nb.join(' | '));
        }
      }
      ended = true; break;
    }
  }
  if (!ended) { deaths++; deathLens.push(api.snake.length); }
  lenSum += api.snake.length;
}
deathLens.sort((x, y) => x - y);
const avgLat = lat.reduce((s, v) => s + v, 0) / (lat.length || 1);
console.log(path.basename(file), '| ronde', ROUNDS);
console.log('  papan penuh :', wins, '| berhenti:', deaths, '| tabrakan:', collisions);
console.log('  panjang akhir:', deathLens.join(', '), '| rata2', (lenSum / ROUNDS).toFixed(1), '/', api.N);
const avgEarly = latEarly.reduce((s, v) => s + v, 0) / (latEarly.length || 1);
console.log('  rata2 langkah/makan:', avgLat.toFixed(2), '| total makan', eats);
console.log('  FASE AWAL (len<' + EARLY + '): rata2', avgEarly.toFixed(2), 'langkah/apel dari', latEarly.length,
  'makan | apel <=2 diabaikan:', closeEarly);
console.log('  apel <=2 diabaikan >4 langkah (fill<60%):', closeRuns);
