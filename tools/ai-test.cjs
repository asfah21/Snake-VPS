/* Verifikasi tanpa browser untuk mesin AI di engine.html.
   Pemakaian: node tools/ai-test.cjs
   Cek: (1) siklus Hamilton valid & berbentuk cincin bersarang,
        (2) beberapa ronde penuh tanpa tabrakan diri,
        (3) parser kecepatan ?speed / ?ms,
        (4) sintaks seluruh <script type="module">. */
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

const file = path.join(__dirname, '..', 'engine.html');
const src = fs.readFileSync(file, 'utf8');
let fails = 0;
const ok = (cond, msg) => { console.log((cond ? 'PASS ' : 'FAIL ') + msg); if (!cond) fails++; };

/* ---------- 1. siklus ---------- */
const a = src.indexOf('//LOGIC_START'), b = src.indexOf('//LOGIC_END');
if (a < 0 || b < 0) throw new Error('marker LOGIC tidak ditemukan');
const code = src.slice(a + '//LOGIC_START'.length, b);
const fly = new Set();
const api = new Function('QS', 'document', 'console', 'fly', code + `
  return { W,H,N,cyc,cycleOk,step,reset,nextMove,
    get snake(){return snake}, get apples(){return apples}, get occ(){return occ},
    get eaten(){return eaten}, get endReason(){return _endReason} };
`)(new URLSearchParams(''), { body: { classList: { add() {} } } }, { warn() {}, log() {} }, fly);

ok(api.cyc.length === api.N, 'siklus berisi ' + api.cyc.length + '/' + api.N + ' sel');
ok(api.cycleOk(api.cyc), 'siklus valid (semua sel unik, tiap langkah bersebelahan)');
const ringOf = c => {
  const x = c % api.W, y = (c / api.W) | 0;
  return Math.min(x, y, api.W - 1 - x, api.H - 1 - y);
};
const rings = api.cyc.map(ringOf);
let trans = 0;
for (let i = 1; i < rings.length; i++) if (rings[i] !== rings[i - 1]) trans++;
const sizes = {};
rings.forEach(r => sizes[r] = (sizes[r] || 0) + 1);
console.log('  ukuran cincin:', JSON.stringify(sizes), '| transisi cincin:', trans);
ok(trans <= 20, 'badan mengisi per cincin bersarang (spiral), bukan zig-zag per baris');

/* ---------- 2. beberapa ronde penuh ---------- */
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
let wins = 0, deaths = 0, collisions = 0, eats = 0;
const ROUNDS = 4;
for (let r = 0; r < ROUNDS; r++) {
  api.reset(); api.apples.clear(); fly.clear();
  let ended = false;
  for (let t = 0; t < 40000; t++) {
    while (api.apples.size < 2) { const c = pickFreeCell(); if (c < 0) break; api.apples.add(c); }
    const res = api.step();
    if (new Set(api.snake).size !== api.snake.length) { collisions++; ended = true; break; }
    if (res === 2) { if (api.snake.length >= api.N) wins++; else deaths++; ended = true; break; }
  }
  if (!ended) deaths++;
  eats += api.eaten;
}
ok(collisions === 0, 'tidak pernah menabrak badan sendiri dalam ' + ROUNDS + ' ronde');
ok(wins === ROUNDS, 'papan terisi penuh ' + wins + '/' + ROUNDS + ' ronde (buntu: ' + deaths + ')');
console.log('  total apel dimakan:', eats, '| rata-rata langkah/apel:', (160000 / Math.max(1, eats)).toFixed(1), '(kasar)');

/* ---------- 2b. jendela TANPA APEL (apel masih "terbang" di browser) ----------
   Regresi bug "ular mati / mengurung diri di panjang 50-70": dulu saat apples.size === 0
   nextMove() mengikuti siklus TANPA validasi rute, jadi bila sel siklus berikutnya ditempati
   badan sendiri hasilnya -1 -> ronde ditutup "PAPAN PENUH" padahal papan baru terisi ~25%.
   Harness ai-bench/ai-test biasa tidak pernah membuat jendela ini (selalu 2 apel). */
{
  const ROUNDS_NP = 3, TICKS = 3000;
  let bad = 0; const why = [];
  for (let r = 0; r < ROUNDS_NP; r++) {
    api.reset(); api.apples.clear(); fly.clear();
    for (let t = 0; t < TICKS; t++) {
      const phase = t % 9;
      if (phase === 0) api.apples.clear();                       // apel dimakan / hilang
      if (phase === 5 && api.apples.size === 0) {                 // baru mendarat lagi
        const c = pickFreeCell(); if (c >= 0) api.apples.add(c);
      }
      const res = api.step();
      if (new Set(api.snake).size !== api.snake.length) { bad++; why.push('badan dobel'); break; }
      if (res === 2) {
        if (api.snake.length < api.N) { bad++; why.push('len ' + api.snake.length + ' reason ' + api.endReason); }
        break;
      }
    }
  }
  ok(bad === 0, 'jendela tanpa apel tidak mengakhiri ronde dini' + (why.length ? ' (gagal: ' + why.join(' | ') + ')' : ' (' + ROUNDS_NP + ' ronde x ' + TICKS + ' langkah)'));
}

/* ---------- 3. parser kecepatan ---------- */
const sp = src.match(/const DEF_MS=170;[\s\S]*?const BUNTU_MS=[^;]+;/);
if (!sp) throw new Error('blok kecepatan tidak ditemukan');
const speedOf = q => {
  const qs = new URLSearchParams(q);
  const out = new Function('QS', 'N', 'clampV', sp[0].replace(/^/, '') + '\nreturn {STEP_MS,MAX_STEP_FRAME,BUNTU_MS,SPAWN_GAP,FLY_T};')(
    qs, 256, undefined);
  return out;
};
const s200 = speedOf('speed=200'), s2 = speedOf('speed=2'), sDef = speedOf(''), sMs = speedOf('ms=500'), sSlow = speedOf('speed=0.5');
ok(Math.abs(s200.STEP_MS - 5) < 0.01, '?speed=200 -> 5 ms per langkah (200 langkah/detik)');
ok(Math.abs(s2.STEP_MS - 85) < 0.01, '?speed=2 -> pengali 2x (85 ms)');
ok(Math.abs(sDef.STEP_MS - 170) < 0.01, 'default 170 ms per langkah');
ok(Math.abs(sMs.STEP_MS - 500) < 0.01, '?ms=500 -> 500 ms per langkah');
ok(Math.abs(sSlow.STEP_MS - 340) < 0.01, '?speed=0.5 -> 340 ms (setengah kecepatan)');
ok(s200.MAX_STEP_FRAME <= 48 && s200.MAX_STEP_FRAME >= 2, 'batas langkah/frame aman: ' + s200.MAX_STEP_FRAME);
ok(s200.BUNTU_MS >= 8000, 'BUNTU_MS punya lantai: ' + Math.round(s200.BUNTU_MS) + ' ms');

/* ---------- 4. sintaks seluruh module script ---------- */
const mods = [...src.matchAll(/<script type="module">([\s\S]*?)<\/script>/g)];
let syntaxOk = true;
for (const m of mods) {
  const body = m[1].replace(/^\s*import\s[\s\S]*?from\s+'[^']+';/m, '');
  const tmp = path.join(require('os').tmpdir(), 'snake-mod-' + Date.now() + '.mjs');
  fs.writeFileSync(tmp, body);
  try { execFileSync(process.execPath, ['--check', tmp], { stdio: 'pipe' }); }
  catch (e) { syntaxOk = false; console.log(String(e.stderr || e.message).slice(0, 800)); }
  finally { fs.unlinkSync(tmp); }
}
ok(syntaxOk && mods.length > 0, 'sintaks <script type="module"> valid (' + mods.length + ' blok)');

console.log('\n' + (fails ? fails + ' TES GAGAL' : 'SEMUA TES LULUS'));
process.exit(fails ? 1 : 0);
