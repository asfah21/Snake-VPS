/* Harness reproduksi: meniru LATENSI APEL milik engine.html di browser.

   Di browser, apel yang masih "terbang" (flying) BELUM masuk set `apples`
   (lihat engine.html: launch() + frame()), jadi tiap kali apel terakhir dimakan
   ada jendela beberapa langkah dengan apples.size === 0. Harness ai-bench.cjs
   tidak pernah membuat jendela itu (selalu memaksa 2 apel), sehingga kematian
   "PAPAN PENUH" pada panjang 50-70 tidak pernah terlihat di uji headless.

   Pemakaian:
     node tools/ai-stall.cjs [file] [ronde] [langkahMaks] [langkahTerbang] [query]
   Contoh:
     node tools/ai-stall.cjs engine.html 8 6000 5
     set TRACE=1 && node tools/ai-stall.cjs engine.html 4 6000 5
     set DUMP=1  && node tools/ai-stall.cjs engine.html 4 6000 5
*/
const fs = require('fs');
const path = require('path');

const file = process.argv[2] || path.join(__dirname, '..', 'engine.html');
const ROUNDS = +(process.argv[3] || 8);
const MAXT = +(process.argv[4] || 6000);
const FLY_TICKS = Math.max(0, +(process.argv[5] || 5));   // 0 = perilaku ai-bench lama
const QUERY = process.argv[6] || '';

const src = fs.readFileSync(file, 'utf8');
const a = src.indexOf('//LOGIC_START'), b = src.indexOf('//LOGIC_END');
if (a < 0 || b < 0) throw new Error('marker LOGIC tidak ditemukan di ' + file);
const code = src.slice(a + '//LOGIC_START'.length, b);

const flySet = new Set();
const api = new Function('QS', 'document', 'console', 'fly', code + `
  return {
    W,H,N,step,reset,setTrace:(a)=>{_aiTrace=a},nextMove,
    get snake(){return snake}, get apples(){return apples}, get occ(){return occ},
    get eaten(){return eaten}, get idx(){return idx}, get cyc(){return cyc},
    get endReason(){return _endReason}
  };
`)(new URLSearchParams(QUERY), { body: { classList: { add() {} } } }, { warn() {}, log() {} }, flySet);

const { W, H, N } = api;

/* Sel kosong yang masih terjangkau kepala (ekor dianggap bebas), tanpa apel & tanpa apel terbang.
   Sama seperti freeCell() di engine: kalau tak ada sel terjangkau, pakai sel kosong mana pun. */
function pickFreeCell(apples, flying) {
  const occ = api.occ, snake = api.snake;
  const tail = snake[snake.length - 1];
  const seen = new Uint8Array(N), q = [snake[0]]; seen[snake[0]] = 1;
  const free = [], any = [];
  for (let h = 0; h < q.length; h++) {
    const c = q[h], x = c % W, y = (c / W) | 0;
    for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
      const nx = x + dx, ny = y + dy;
      if (nx < 0 || ny < 0 || nx >= W || ny >= H) continue;
      const m = ny * W + nx;
      if (seen[m] || (occ[m] && m !== tail)) continue;
      seen[m] = 1; q.push(m);
      if (!occ[m] && !apples.has(m) && !flying.some(f => f.c === m)) free.push(m);
    }
  }
  for (let c = 0; c < N; c++) {
    if (!occ[c] && c !== tail && !apples.has(c) && !flying.some(f => f.c === c)) any.push(c);
  }
  if (free.length) return free[(Math.random() * free.length) | 0];
  return any.length ? any[(Math.random() * any.length) | 0] : -1;
}

let wins = 0, deaths = 0, collisions = 0, eats = 0, noAppleDeaths = 0, timeouts = 0;
const deathLens = [], endLens = [], stuckTicks = [], reasons = {};

for (let r = 0; r < ROUNDS; r++) {
  api.reset(); api.apples.clear(); flySet.clear();
  const flying = [];                 // {c,t} t = sisa langkah sebelum mendarat
  let ended = false, sinceEat = 0;
  const hist = [];

  for (let t = 0; t < MAXT; t++) {
    // 1) apel yang selesai terbang mendarat (hanya kalau selnya masih kosong)
    for (let i = flying.length - 1; i >= 0; i--) {
      if (--flying[i].t > 0) continue;
      const c = flying[i].c; flying.splice(i, 1);
      if (!api.occ[c] && !api.apples.has(c)) api.apples.add(c);
    }
    // 2) isi kuota apel seperti frame(): total apel + terbang = 2
    const BASE = 2, gap = 0;
    let guard = 0;
    while (api.apples.size + flying.length < BASE && guard++ < BASE) {
      const c = pickFreeCell(api.apples, flying);
      if (c < 0) break;
      if (FLY_TICKS <= 0) api.apples.add(c);
      else flying.push({ c, t: FLY_TICKS });
    }

    // trace SELALU dipasang: dipakai untuk membedakan sebab kematian,
    // bukan hanya untuk mencetak. Trace kosong = nextMove keluar lebih awal
    // (cabang apples.size === 0), artinya langkah buta tanpa validasi rute.
    const tr = [];
    api.setTrace(tr);
    const res = api.step();
    api.setTrace(null);

    // 3) deteksi state rusak (badan dobel) yang membuat occ desync
    if (new Set(api.snake).size !== api.snake.length) { collisions++; ended = true; break; }

    if (res === 2) {
      if (api.snake.length >= N) wins++;
      else {
        deaths++; deathLens.push(api.snake.length);
        const rs = api.endReason || 'unknown';
        reasons[rs] = (reasons[rs] || 0) + 1;
        const emptyTrace = tr.length === 0;
        if (emptyTrace) noAppleDeaths++;
        if (process.env.TRACE) {
          const pk = tr.find(x => x.pick !== undefined);
          console.log('  DEATH ronde', r + 1, 'langkah', t, 'len', api.snake.length,
            'apel', [...api.apples], 'terbang', flying.length,
            '| CABANG TANPA APEL:', emptyTrace, '| pick terakhir:', pk ? pk.pick : '?',
            '| kandidat terakhir:', tr.filter(x => x.k !== undefined).length);
          for (const h of hist.slice(-6)) console.log('   ', h);
        }
        if (process.env.DUMP) {
          const snake = api.snake, head = snake[0], tail = snake[snake.length - 1], occ = api.occ;
          console.log('  PAPAN ronde', r + 1, 'len', snake.length, 'kepala', head, 'ekor', tail, 'apel', [...api.apples]);
          for (let y = 0; y < H; y++) {
            let row = '   ';
            for (let x = 0; x < W; x++) {
              const c = y * W + x;
              row += (c === head ? 'H' : c === tail ? 'T' : api.apples.has(c) ? '*' : occ[c] ? '#' : '.') + ' ';
            }
            console.log(row);
          }
        }
      }
      ended = true; break;
    }
    if (process.env.TRACE) { hist.push('t=' + t + ' len=' + api.snake.length + ' apel=' + api.apples.size + ' terbang=' + flying.length + ' pick=' + ((tr.find(x => x.pick !== undefined) || {}).pick || '-')); if (hist.length > 12) hist.shift(); }
    if (res === 1) { eats++; sinceEat = 0; } else sinceEat++;
  }
  // Ronde yang kehabisan jatah langkah BUKAN kematian: bedakan supaya angka tidak menyesatkan.
  if (!ended) { timeouts++; endLens.push(api.snake.length); stuckTicks.push(sinceEat); }
}

const avg = deathLens.length ? (deathLens.reduce((s, v) => s + v, 0) / deathLens.length).toFixed(1) : '-';
console.log('ronde:', ROUNDS, '| langkahTerbang:', FLY_TICKS, '| query:', QUERY || '(default)');
console.log('papan penuh:', wins, '| kematian:', deaths, '| kematian tanpa apel (trace kosong):', noAppleDeaths,
  '| tabrakan/dobel:', collisions, '| habis langkah:', timeouts, '| apel dimakan:', eats);
if (deathLens.length) console.log('panjang saat mati:', deathLens.join(', '), '| rata-rata:', avg);
if (endLens.length) console.log('panjang saat habis langkah:', endLens.join(', '),
  '| langkah tanpa makan:', stuckTicks.join(', '));
if (Object.keys(reasons).length) console.log('alasan berhenti:', JSON.stringify(reasons));
/* Gagal hanya bila ada kematian/tabrakan. Ronde yang berhenti di 248-254 sel adalah normal di
   skenario ini (sisa sel terkurung badan), jadi tidak dihitung gagal. */
process.exit(deaths || collisions ? 1 : 0);
