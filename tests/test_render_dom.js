#!/usr/bin/env node
/**
 * DOM test for the results list (jsdom, no browser, no network).
 *
 * The flicker fix is a claim about *element identity*: the row and its <img> must be the
 * same nodes across polls, and `src` must be assigned once. That is exactly what the DOM
 * lets us assert, so this runs the real static/index.html script against scripted job
 * payloads and checks what changed between renders.
 */
const fs = require('fs');
const path = require('path');
let JSDOM;
try {
  ({ JSDOM } = require('jsdom'));
} catch (e) {
  console.error('jsdom is not installed — run `npm install` in this directory first.');
  console.error('(the app itself needs no npm packages; this is dev-only tooling)');
  process.exit(2);
}

// this file lives in tests/; the DOM test runs the page's own script against the real markup
const html = fs.readFileSync(path.join(__dirname, '..', 'static', 'index.html'), 'utf8');

// Shape of /api/about (the server builds the real one from the live constants; the Python
// contract test is what proves the two agree).
const STATE = 'WikiPortraits photograph of a public figure, taken by a volunteer photographer at an event photo booth. Assess the photograph itself, not the person\'s fame.';
const ABOUT = {
  app_version: '0.3.0',
  title: 'About WikiPortraits Eval',
  sections: [
    { title: 'What this app does', html: '<p>Every photo is sent once to <b>llm-clef-flash</b>.</p>' },
    { title: 'The exact request sent to Clef',
      html: `<pre>{\n  "model": "llm-clef-flash",\n  "state": "${STATE}"\n}</pre>` },
    { title: 'The five questions, verbatim',
      html: '<table><tr><td><code>quality</code></td><td><code>score</code></td>'
          + '<td>Overall photographic quality for use in an encyclopedia article<br>'
          + '<code>0</code> — unusable (blurred/miscropped)<br><code>4</code> — excellent</td></tr></table>' },
  ],
  request: { state: STATE },
};

// jsdom has no fetch: stub it (and record src assignments per element, to prove the
// thumbnail URL is set once and never re-set on later renders).
const srcSets = new WeakMap();
const dom = new JSDOM(html, {
  runScripts: 'dangerously', pretendToBeVisual: true,
  beforeParse(window) {
    window.fetch = async (url) => ({
      ok: true, status: 200,
      json: async () => {
        const u = String(url);
        if (u.includes('/api/about')) return ABOUT;
        if (u.includes('/api/config')) {
          return { model: 'llm-clef-flash', thumb_width_requested: 640, spacing_s: 0.9, budget: {}, questions: {} };
        }
        if (u.includes('/api/default-list')) {
          return { text: 'File:Example A.jpg\nFile:Example B.jpg', count: 2, source: 'default_list.txt' };
        }
        if (u.includes('/api/parse')) {
          return { items: [{ raw: 'File:Example A.jpg', title: 'File:Example A.jpg', ok: true,
                             pageid: 1, orig_w: 100, orig_h: 200, license: 'CC0' },
                           { raw: 'File:Example B.jpg', title: 'File:Example B.jpg', ok: true,
                             pageid: 2, orig_w: 300, orig_h: 400, license: 'CC0' }],
                   warnings: [], n: 2 };
        }
        return { items: [], warnings: [], n: 0 };
      },
    });
    const proto = window.Element.prototype;
    const orig = proto.setAttribute;
    proto.setAttribute = function (name, value) {
      if (name === 'src') srcSets.set(this, (srcSets.get(this) || 0) + 1);
      return orig.call(this, name, value);
    };
  },
});
const win = dom.window;
const doc = win.document;

const failures = [];
const ok = (cond, label) => {
  console.log(`  ${cond ? 'ok  ' : 'FAIL'} ${label}`);
  if (!cond) failures.push(label);
};

const item = (status, extra = {}) => ({
  title: 'File:MKr377445 Susanne Wuest (In die Sonne schauen, Cannes 2025).jpg',
  raw: 'File:MKr377445 Susanne Wuest (In die Sonne schauen, Cannes 2025).jpg',
  wiki: 'commons.wikimedia.org', pageid: 165943740, orig_w: 3465, orig_h: 5197,
  license: 'CC BY-SA 4.0', sent_px: '960x1440',
  url: 'https://upload.wikimedia.org/wikipedia/commons/6/67/MKr377445_Susanne_Wuest.jpg',
  thumburl: 'https://thumb.wikimedia.org/wikipedia/commons/thumb/6/67/MKr377445_Susanne_Wuest_%28In_die_Sonne_schauen%2C_Cannes_2025%29.jpg/960px-MKr377445_Susanne_Wuest_%28In_die_Sonne_schauen%2C_Cannes_2025%29.jpg',
  status, fetch_s: 0.21, ...extra,
});
const job = (items, extra = {}) => ({
  id: 'test', status: 'running', progress: { done: 0, total: items.length }, items,
  stats: {}, log: ['12:00:00 test'], budget: { used_last_hour: 3, limit_per_hour: 100, blocked_for_s: 0 },
  latency: { calls: 1, baseline_s: 0.54, median_s: 28.6, min_s: 28.4, max_s: 28.7,
             median_ttfb_s: 28.5, factor: 53.0, verdict: 'service-side',
             note: 'model calls are 53x the measured warm baseline …' },
  pipeline: { images: 1, prefetch_depth: 2, download_s: 0.21, model_s: 28.6, scoring_s: 28.9,
              serial_estimate_s: 29.7, saved_s: 0.8 },
  ...extra,
});

const render = (payload) => { try { win.renderJob(payload); return null; } catch (e) { return e; } };

console.log('render path');

// ---- 1. element identity across polls --------------------------------------------
const err1 = render(job([item('downloading')]));
ok(err1 === null, `first render did not throw${err1 ? `: ${err1.message}` : ''}`);
const itemsEl = doc.getElementById('items');
const row0 = itemsEl.children[0];
const img0 = row0.querySelector('img.thumb');
for (const st of ['downloading', 'ready', 'scoring', 'scoring', 'done']) {
  render(job([item(st, st === 'done' ? { result: { wall_s: 28.6, ttfb_s: 28.5, usage: { input_tokens: 1245 },
    answers: { face_visible: { noul: 0.97 }, pose: { choice: 'head_shoulders', probabilities: { head_shoulders: 0.8 } },
               quality: { score: 2.6, confidence: 0.5, probabilities: { '2': 0.5 } },
               eyes_to_camera: { noul: 0.94 }, infobox_ready: { noul: 0.65 } } } } : {})]));
}
const rowAfter = itemsEl.children[0];
const imgAfter = rowAfter.querySelector('img.thumb');
ok(rowAfter === row0, 'the row element is the same node after 5 renders');
ok(imgAfter === img0, 'the <img> is the same node after 5 renders');
ok(srcSets.get(imgAfter) === 1,
   `the thumbnail URL was assigned exactly once across 6 renders (got ${srcSets.get(imgAfter)})`);
ok(doc.getElementById('items').innerHTML.indexOf('MKr377445') !== -1, 'the file name is shown');

// ---- 2. status labels -----------------------------------------------------------
const labelFor = (st, extra) => {
  render(job([item(st, extra)]));
  return itemsEl.children[0].querySelector('.status').textContent;
};
ok(labelFor('pending') === 'queued', 'pending → "queued"');
ok(labelFor('downloading') === 'downloading thumbnail', 'downloading → "downloading thumbnail"');
ok(labelFor('ready') === 'ready · queued for the model', 'ready → "ready · queued for the model"');
ok(labelFor('scoring') === 'scoring', 'scoring → "scoring"');
ok(labelFor('done', { result: { wall_s: 1, usage: {}, answers: { infobox_ready: { noul: 0.9 },
  face_visible: { noul: 0.9 }, eyes_to_camera: { noul: 0.9 }, pose: { choice: 'head_shoulders',
  probabilities: { head_shoulders: 0.9 } }, quality: { score: 3, confidence: 0.5,
  probabilities: { '3': 0.5 } } } } }) === 'done', 'done → "done"');
ok(labelFor('notfound', { error: 'not found on commons.wikimedia.org' }) === 'not found',
   'notfound → "not found"');

// ---- 3. the scoring clock -------------------------------------------------------
const started = Date.now() / 1000 - 42;
render(job([item('scoring', { scoring_started: started })]));
const clock = itemsEl.children[0].querySelector('.clock');
ok(!!clock, 'a scoring row renders a clock element');
const shown = parseFloat((clock.textContent || '').replace('s', ''));
ok(shown > 40 && shown < 45, `the clock shows the elapsed seconds (showed ${shown}s for a 42s-old call)`);
ok(/waiting for the model/.test(itemsEl.children[0].innerHTML), 'the row says it is waiting for the model');
ok(/53/.test(doc.getElementById('latency').innerHTML), 'the latency panel shows the slowdown factor');

// ---- 4. thumbnails are never re-fetched, and rows are added/removed --------------
const many = [0, 1, 2, 3, 4].map((n) => item('done', {
  pageid: 1000 + n, title: `File:Photo ${n}.jpg`, raw: `File:Photo ${n}.jpg`,
  thumburl: `https://thumb.wikimedia.org/wikipedia/commons/x/${n}.jpg/960px-${n}.jpg`,
  result: { wall_s: 0.5, usage: {}, answers: { infobox_ready: { noul: 0.5 }, face_visible: { noul: 0.9 },
    eyes_to_camera: { noul: 0.9 }, pose: { choice: 'head_shoulders', probabilities: { head_shoulders: 0.9 } },
    quality: { score: 2, confidence: 0.5, probabilities: { '2': 0.5 } } } },
}));
render(job(many));
ok(itemsEl.children.length === 5, `five rows rendered (got ${itemsEl.children.length})`);
const imgsBefore = [...itemsEl.querySelectorAll('img.thumb')];
render(job(many));
const imgsAfter = [...itemsEl.querySelectorAll('img.thumb')];
ok(imgsBefore.length === 5 && imgsAfter.every((n, i) => n === imgsBefore[i]),
   'all five <img> nodes survive a re-render unchanged');
render(job(many.slice(0, 3)));
ok(itemsEl.children.length === 3, `rows are removed when the job shrinks (got ${itemsEl.children.length})`);
render(job([]));
ok(itemsEl.children.length === 0, 'an empty job clears the list');

// ---- 4b. composite, P(publish), rank order and candidate spread ------------------
const fullAnswers = (ib, pub, face, eyes, pose) => ({
  infobox_ready: { noul: ib }, face_visible: { noul: face }, eyes_to_camera: { noul: eyes },
  pose: { choice: pose || 'head_shoulders', confidence: 0.7,
          probabilities: { head_shoulders: 0.7, three_quarter: 0.2, group: 0.1 } },
  quality: { score: 2.5, confidence: 0.5, probabilities: { '2': 0.5, '3': 0.5 } },
  publish_ready: { choice: pub >= 0.5 ? 'publish' : 'pass', confidence: pub,
                   probabilities: { publish: pub, pass: Number((1 - pub).toFixed(3)) } },
});
const comp = (a) => Math.min(a.face_visible.noul, a.eyes_to_camera.noul)
  * ({ head_shoulders: 1.0, three_quarter: 0.9, full_body: 0.6, candid: 0.5, group: 0.25 }[a.pose.choice] || 0.5);

const graded = [
  ['File:Best.jpg', 11, 0.68, 0.88, 0.97, 0.90],
  ['File:Mid.jpg', 12, 0.50, 0.60, 0.95, 0.55],
  ['File:Weak.jpg', 13, 0.30, 0.25, 0.40, 0.30],
].map(([title, pageid, ib, pub, face, eyes]) => {
  const a = fullAnswers(ib, pub, face, eyes);
  return item('done', { pageid, title, raw: title, result: { wall_s: 0.6, usage: {}, answers: a },
                        composite: Number(comp(a).toFixed(4)), publish_p: pub });
});
const spread = (vals) => ({ floor: 0.25, n: vals.length, n_candidates: vals.filter((v) => v >= 0.25).length,
  candidate_range: 0.4, candidate_median_gap: 0.2, dead_zone: 1, dead_zone_share: 0.33, top: ['best'] });
render(job(graded, { spread: { composite: spread([0.9, 0.5, 0.1]), publish_ready: spread([0.9, 0.6, 0.25]),
                                infobox_ready: spread([0.68, 0.5, 0.3]) },
                stats: { n_scored: 3, n_input: 3, composite_mean: 0.5, p_publish_mean: 0.577,
                         p_infobox_mean: 0.493, p_infobox_median: 0.5, p_infobox_max: 0.68,
                         n_above_half: 2, quality_mean: 2.5, tokens_total: 2715, wall_median_s: 0.6,
                         wall_total_s: 1.8, poses: { head_shoulders: 3 }, n_429: 0 } }));
const rowsNow = [...itemsEl.children];
ok(rowsNow.length === 3, `three graded rows (got ${rowsNow.length})`);
ok(/#1/.test(rowsNow[0].textContent) && /Best\.jpg/.test(rowsNow[0].textContent),
   'the best photo is ranked #1');
ok(/#3/.test(rowsNow[2].textContent) && /Weak\.jpg/.test(rowsNow[2].textContent),
   'the weakest photo is ranked last — the list is ordered by composite');
ok(/composite/.test(rowsNow[0].innerHTML), 'each row shows the composite');
ok(/publish/.test(rowsNow[0].innerHTML), 'each row shows the model\'s publish answer');
const spreadHtml = doc.getElementById('spread').innerHTML;
ok(/candidate spread/.test(spreadHtml), 'the candidate-spread panel is rendered');
ok(/P\(publish\)/.test(spreadHtml) && /P\(infobox\)/.test(spreadHtml),
   'spread is shown for composite, P(publish) and P(infobox)');
ok(/dead|0\.35/.test(spreadHtml), 'the dead-zone share is shown');
ok(imgsBefore.length === 5, 'previous section left its images intact');
ok(/composite/.test(doc.getElementById('stats').innerHTML || ''), 'stats grid carries the composite mean');

// ---- 4c. section 2 readability: strips, a collapsible spread, and the pose strata view --------
// This block renders a *done* job, because renderChart (and so the toggle) only draws on one.
const doneJob = job(graded, {
  status: 'done',
  spread: { composite: spread([0.9, 0.5, 0.1]), publish_ready: spread([0.9, 0.6, 0.25]),
            infobox_ready: spread([0.68, 0.5, 0.3]) },
  stats: { n_scored: 3, n_input: 3, composite_mean: 0.5, p_publish_mean: 0.577,
           p_infobox_mean: 0.493, p_infobox_median: 0.5, p_infobox_max: 0.68, n_above_half: 2,
           quality_mean: 2.5, tokens_total: 2715, wall_median_s: 0.6, wall_total_s: 1.8,
           poses: { head_shoulders: 3 }, n_429: 0 },
});
render(doneJob);
const statsHtml = doc.getElementById('stats').innerHTML;
const nStrips = (statsHtml.match(/class="strip"/g) || []).length;
ok(nStrips === 2, `run stats are two one-line strips, not a box grid (got ${nStrips})`);
ok(statsHtml.indexOf('statgroup') === -1, 'the old three-panel grid is gone');
ok(/mean quality/.test(statsHtml) && /mean composite/.test(statsHtml),
   'quality sits on the same strip as the score means');
ok(/Scores/.test(statsHtml) && /Time/.test(statsHtml), 'both strips are labelled');
const sd = doc.getElementById('spreaddetails');
ok(sd !== null, 'the candidate spread is a collapsible <details>');
ok(!sd.hasAttribute('open'), 'and it starts collapsed');
ok(/candidates at or above/.test(sd.querySelector('summary').textContent),
   'the collapsed one-liner still carries the headline counts');

doc.getElementById('cm-pose').click();
const poseHtml = doc.getElementById('chart').innerHTML;
const nLabels = (poseHtml.match(/class="plabel"/g) || []).length;
ok(nLabels === 5, `by-pose view draws one stratum per pose, empty ones included (got ${nLabels})`);
ok(/head &amp; shoulders/.test(poseHtml), 'the occupied stratum is labelled with the pose name');
ok(/class="cgrid"/.test(poseHtml), 'by-pose view adds vertical gridlines');
ok(/one stratum per pose/.test(poseHtml), 'the heading says which view is drawn');
ok(/class="cthumb"/.test(poseHtml), 'and the photos are still plotted');
doc.getElementById('cm-combined').click();
const combHtml = doc.getElementById('chart').innerHTML;
ok(!/class="plabel"/.test(combHtml), 'the default combined view has no pose strata');
ok(!/class="cgrid"/.test(combHtml), 'and no gridlines');
ok(/thumbs stack/.test(combHtml), 'the default view keeps its own caption');

// ---- 4d. each photo must sit in ITS OWN pose band -------------------------------------------
// Regression: labels are positioned down from the top of the plot while CSS positions a thumbnail
// up from the bottom, so mixing the two coordinate systems flipped the whole axis — every
// head-and-shoulders photo was drawn under the "candid" label. Counts and labels looked fine, which
// is why the earlier assertions missed it. This checks the correspondence a reader actually relies on.
const mixed = [
  ['File:Posed.jpg', 21, 'head_shoulders', 0.90],
  ['File:Waist.jpg', 22, 'three_quarter', 0.70],
  ['File:Candid.jpg', 23, 'candid', 0.40],
  ['File:Crowd.jpg', 24, 'group', 0.20],
].map(([title, pageid, pose, comp]) => {
  const a = fullAnswers(0.5, 0.5, 0.9, 0.9, pose);
  return item('done', { pageid, title, raw: title, composite: comp, publish_p: 0.5,
                        result: { wall_s: 0.6, usage: {}, answers: a } });
});
render(job(mixed, { status: 'done',
  stats: { n_scored: 4, n_input: 4, composite_mean: 0.55, p_publish_mean: 0.5, p_infobox_mean: 0.5,
           p_infobox_median: 0.5, p_infobox_max: 0.5, n_above_half: 2, quality_mean: 2.5,
           tokens_total: 100, wall_median_s: 0.5, wall_total_s: 2, poses: {}, n_429: 0 } }));
doc.getElementById('cm-pose').click();
const chartEl = doc.getElementById('chart');
const plotH = parseFloat(chartEl.querySelector('.chartbox').style.height);
const bands = [...chartEl.querySelectorAll('.cposes .plabel')].map((e) => ({
  name: e.textContent.replace(/[0-9—]/g, '').trim(), top: parseFloat(e.style.top) }));
const bandAt = (yTop) => {
  for (let i = 0; i < bands.length; i++) {
    const next = i + 1 < bands.length ? bands[i + 1].top : plotH;
    if (yTop >= bands[i].top && yTop < next) return bands[i].name;
  }
  return 'OUTSIDE';
};
const WANT = { Posed: 'head & shoulders', Waist: 'three-quarter', Candid: 'candid', Crowd: 'group' };
const wrongBand = [...chartEl.querySelectorAll('.cthumb')].map((e) => {
  const title = e.getAttribute('title') || '';
  const who = Object.keys(WANT).find((n) => title.indexOf(n) !== -1);
  // the thumb's bottom edge, expressed as a distance down from the top of the plot
  const edge = plotH - parseFloat(e.style.bottom);
  return { who, want: WANT[who], got: bandAt(edge) };
}).filter((r) => r.want !== r.got);
ok(wrongBand.length === 0,
   `every photo is drawn inside its own pose band (wrong: ${JSON.stringify(wrongBand)})`);
ok(bands.length === 5 && bands.map((b) => b.name).join(',') ===
   'head & shoulders,three-quarter,full body,group,candid',
   `the bands are labelled and ordered as the poses are (got ${bands.map((b) => b.name).join(',')})`);

// ---- 4e. the chart is LIVE: it plots as results land, and it does not rebuild itself -------------
// The chart used to be blanked until the job finished, so nothing appeared until the very end. It now
// draws whatever has landed, which also makes it the progress display. The risk in redrawing on every
// poll is the one invariant 3 describes — re-creating the nodes would re-fetch every <img> and flash
// the chart — so this asserts both halves: that it draws early, and that the nodes and their src
// survive the redraws.
console.log('live chart');
const liveItem = (n, comp) => {
  const a = fullAnswers(0.5, 0.5, 0.9, 0.9, 'head_shoulders');
  return item('done', { pageid: 700 + n, title: `File:Live ${n}.jpg`, raw: `File:Live ${n}.jpg`,
                        composite: comp, publish_p: 0.5,
                        result: { wall_s: 0.6, usage: {}, answers: a } });
};
const pendingItem = (n) => item('scoring', { pageid: 800 + n, title: `File:Pending ${n}.jpg`,
                                             raw: `File:Pending ${n}.jpg` });
const liveStats = { n_scored: 3, n_input: 3, composite_mean: 0.59, p_publish_mean: 0.5,
                    p_infobox_mean: 0.5, p_infobox_median: 0.5, p_infobox_max: 0.5, n_above_half: 3,
                    quality_mean: 2.5, tokens_total: 100, wall_median_s: 0.6, wall_total_s: 1.8,
                    poses: {}, n_429: 0 };
const liveJob = (items, extra) => job(items, { id: 'live', stats: liveStats, ...extra });
const liveThumbs = () => [...doc.querySelectorAll('#chart .cthumb')];
const caption = () => doc.querySelector('#chart .meter').textContent;
const axisOf = () => {
  const m = /axis 0 → ([\d.]+)/.exec(caption());
  return m ? parseFloat(m[1]) : NaN;
};

// one photo in, two still running
const lerr = render(liveJob([liveItem(1, 0.40), pendingItem(2), pendingItem(3)],
                            { progress: { done: 1, total: 3 } }));
ok(lerr === null, `a running job renders without throwing${lerr ? `: ${lerr.message}` : ''}`);
ok(liveThumbs().length === 1,
   `the chart plots the photo that has landed while the run is still going (got ${liveThumbs().length})`);
ok(/1 of 3 scored/.test(caption()), `the caption doubles as the progress display (got "${caption().slice(0, 48)}")`);
const firstThumb = liveThumbs()[0];
const firstImg = firstThumb.querySelector('img');
const axis1 = axisOf();

// the other two land, one of them a new best — so the axis steps up and every thumbnail slides left
render(liveJob([liveItem(1, 0.40), liveItem(2, 0.75), pendingItem(3)],
               { progress: { done: 2, total: 3 } }));
const axis2 = axisOf();
render(liveJob([liveItem(1, 0.40), liveItem(2, 0.75), liveItem(3, 0.62)],
               { status: 'done', progress: { done: 3, total: 3 } }));
const axis3 = axisOf();

ok(liveThumbs().length === 3, `all three are plotted by the end (got ${liveThumbs().length})`);
ok(liveThumbs()[0] === firstThumb, 'the first thumbnail is the same ELEMENT after two more polls');
ok(liveThumbs()[0].querySelector('img') === firstImg, 'and its <img> is the same node');
ok(srcSets.get(firstImg) === 1,
   `the chart thumbnail's src was assigned exactly once across 3 renders (got ${srcSets.get(firstImg)})`);
ok(axis1 <= axis2 && axis2 <= axis3,
   `the axis only ever grows as results land (${axis1} -> ${axis2} -> ${axis3})`);
ok(axis3 === 0.9, `and settles on the exact value for the best photo (got ${axis3})`);
ok(/3 of 3 scored/.test(caption()), 'the caption reports the finished count');

// a fresh run must start the chart over rather than inherit the previous run's nodes
const lerr2 = render(job([pendingItem(9)], { id: 'live2', progress: { done: 0, total: 1 } }));
ok(lerr2 === null, 'a second run renders without throwing');
ok(liveThumbs().length === 0, `a new run starts with an empty chart (got ${liveThumbs().length})`);
ok(/first result lands here/.test(caption()), 'and says what it is waiting for');

// ---- 4f. a dense clump must not hide photos ---------------------------------------------------
// Measured 2026-10-14: the lane search was bounded at LANES_MAX, and the item that fell out of the
// bound was written into `lanes[LANES_MAX]` as an overflow bucket — so the 7th photo in a column was
// drawn at exactly the same left/bottom as the 6th, and a 20-photo clump hid 14 of them behind others.
// Identical composites are the worst case, so that is what this uses.
console.log('dense clump');
const clumpJob = (n, comp) => job(Array.from({ length: n }, (_, i) => {
  const a = fullAnswers(0.5, 0.5, 0.9, 0.9, 'head_shoulders');
  return item('done', { pageid: 950 + i, title: `File:Clump ${i + 1}.jpg`, raw: `File:Clump ${i + 1}.jpg`,
                        composite: comp, publish_p: 0.5,
                        result: { wall_s: 0.5, usage: {}, answers: a } });
}), { id: 'clump', status: 'done', progress: { done: n, total: n }, stats: liveStats });

render(clumpJob(20, 0.44));
const clumpThumbs = [...doc.querySelectorAll('#chart .cthumb')];
ok(clumpThumbs.length === 20, `every photo in a 20-photo clump is drawn (got ${clumpThumbs.length})`);
const spots = clumpThumbs.map((e) => `${e.style.left}|${e.style.bottom}`);
ok(new Set(spots).size === 20,
   `all 20 sit in distinct positions — a hidden photo is worse than a tall band (got ${new Set(spots).size})`);
const distinctBottom = new Set(clumpThumbs.map((e) => e.style.bottom));
ok(distinctBottom.size === 20,
   `each one gets its own lane rather than sharing an overflow bucket (got ${distinctBottom.size} lanes)`);

// ---- 5. the About panel (async: the panel fetches its content) --------------------
function finish() {
  console.log(`\nFAILURES: ${failures.length}`);
  failures.forEach((f) => console.log('  -', f));
  process.exit(failures.length ? 1 : 0);
}

(async () => {
  // ---- 6. the input box is never pre-loaded with something runnable -----------------------
  // The example formats live in the placeholder (grey, not content); the real starter list arrives
  // only when the user asks for it. This is the assertion that keeps "press Score them" from ever
  // scoring the examples by accident.
  const inputEl = doc.getElementById('input');
  ok(inputEl.value === '', 'the input box starts empty — the examples are a placeholder, not content');
  ok((inputEl.getAttribute('placeholder') || '').indexOf('Foo.jpg') !== -1,
     'the placeholder shows the example formats');
  doc.getElementById('load-test').click();
  await new Promise((r) => setTimeout(r, 30));
  ok(inputEl.value.split('\n').filter((s) => s.trim()).length === 2,
     `Load sample list fills the box from /api/default-list (got ${inputEl.value.split('\n').length} line(s))`);
  ok(/2 test photos loaded/.test(doc.getElementById('parseinfo').textContent),
     'the pill reports how many test photos were loaded');

  // ---- 7. the button affordance walks Validate files -> Score them ---------------------------
  // The blue "primary" style is the only thing telling a first-time user which button is next, so it
  // must move with the stage: Check -> (list resolved) -> Score them -> (list edited) -> Check.
  const checkBtn = doc.getElementById('check'), runBtn = doc.getElementById('run');
  ok(checkBtn.classList.contains('primary') && runBtn.disabled,
     'before validating: Validate files is primary and Score them is disabled');
  checkBtn.click();
  await new Promise((r) => setTimeout(r, 20));
  // earlier sections rendered job payloads, so a previous run exists and the confirm must appear
  const confirmEl = doc.getElementById('confirm-overlay');
  ok(!confirmEl.classList.contains('hidden'),
     'a check with results on screen asks before clearing them');
  doc.getElementById('confirm-yes').click();
  await new Promise((r) => setTimeout(r, 40));
  ok(!checkBtn.classList.contains('primary'),
     'after a successful validation the blue leaves Validate files');
  ok(runBtn.classList.contains('primary') && !runBtn.disabled,
     'after a successful check Score them is primary and enabled');
  doc.getElementById('input').dispatchEvent(new win.Event('input'));
  await new Promise((r) => setTimeout(r, 10));
  ok(checkBtn.classList.contains('primary') && runBtn.disabled,
     'editing the list puts the flow back to step 1 (Validate files primary, Score them disabled)');

  const overlay = doc.getElementById('about-overlay');
  ok(overlay.classList.contains('hidden'), 'the About panel starts hidden');
  doc.getElementById('about-open').click();
  await new Promise((r) => setTimeout(r, 30));      // let the stubbed fetch settle
  ok(!overlay.classList.contains('hidden'), 'clicking About opens the panel');
  const aboutText = doc.getElementById('about-body').textContent;
  ok(/llm-clef-flash/.test(aboutText), 'the About panel names the model');
  ok(/unusable \(blurred\/miscropped\)/.test(aboutText), 'the About panel shows the quality legend');
  ok(/WikiPortraits photograph of a public figure/.test(aboutText),
     'the About panel quotes the live state string');
  ok(doc.querySelectorAll('#about-toc a').length === ABOUT.sections.length,
     `the table of contents lists every section (got ${doc.querySelectorAll('#about-toc a').length})`);
  ok(doc.querySelector('#about-body pre') !== null, 'the request body is shown as literal JSON');
  doc.getElementById('about-close').click();
  ok(overlay.classList.contains('hidden'), 'the Close button hides the panel');
  doc.getElementById('about-open').click();
  await new Promise((r) => setTimeout(r, 10));
  ok(!overlay.classList.contains('hidden'), 'reopening uses the cached payload');
  ok(srcSets.get(imgAfter) === 1, 'opening About did not touch the results list');

  // ---- 8. deep links (?list= / ?check=1) --------------------------------------------------
  // A fresh JSDOM per case, because the URL is read once at load. The stub records every fetch so we
  // can assert the two rules that matter: a link may prefill and (with check=1) press Validate files,
  // but it must NEVER cause a run, and it must never split on a comma.
  function withUrl(search) {
    const calls = [], bodies = [];
    const d2 = new JSDOM(html, {
      runScripts: 'dangerously', pretendToBeVisual: true,
      url: 'https://example.test/' + search,
      beforeParse(window) {
        window.fetch = async (url, opts) => {
          calls.push(String(url));
          if (opts && opts.body) bodies.push(String(opts.body));
          return {
            ok: true, status: 200,
            json: async () => {
              const u = String(url);
              if (u.includes('/api/default-list')) {
                return { text: 'File:A.jpg\nFile:B.jpg', count: 2, source: 'default_list.txt' };
              }
              if (u.includes('/api/parse')) {
                return { items: [{ raw: 'File:A.jpg', title: 'File:A.jpg', ok: true, pageid: 1 }],
                         warnings: [], n: 1 };
              }
              if (u.includes('/api/run')) return { job_id: 'should-never-happen' };
              return {};
            },
          };
        };
      },
    });
    return { win: d2.window, doc: d2.window.document, calls, bodies,
             ran: () => calls.some((u) => u.includes('/api/run')),
             parsed: () => calls.some((u) => u.includes('/api/parse')),
             box: () => d2.window.document.getElementById('input').value };
  }
  const lines = (s) => s.split('\n').filter((x) => x.trim()).length;

  const plain = withUrl('');
  ok(plain.box() === '', 'no params: the box stays empty');
  ok(!plain.parsed() && !plain.ran(), 'no params: nothing is fetched');

  const two = withUrl('?list=' + encodeURIComponent('File:A.jpg\nFile:B.jpg'));
  ok(lines(two.box()) === 2, '?list= prefills two entries');
  ok(!two.parsed(), '?list= alone does not check anything (check=1 is required)');
  ok(!two.ran(), '?list= never starts a run');

  const commaName = 'File:MKr377445 Susanne Wuest (In die Sonne schauen, Cannes 2025).jpg';
  const one = withUrl('?list=' + encodeURIComponent(commaName));
  ok(lines(one.box()) === 1,
     'a file name containing a comma stays ONE entry — the URL layer adds no comma rule');
  ok(one.box() === commaName, 'the value arrives verbatim (no comma/paren mangling)');

  const wikitext = '[[File:Foo.jpg|thumb|caption]]';
  const wt = withUrl('?list=' + encodeURIComponent(wikitext));
  ok(wt.box() === wikitext,
     'wikitext pipes survive (a | shorthand separator would have corrupted this form)');

  const checked = withUrl('?list=' + encodeURIComponent('File:A.jpg\nFile:B.jpg') + '&check=1');
  await new Promise((r) => setTimeout(r, 40));
  ok(checked.parsed(), '?check=1 presses Validate files');
  ok(!checked.ran(), '?check=1 still never starts a run');
  ok(checked.win.location.search === '', 'the params are stripped so a reload cannot replay them');

  const inert = withUrl('?check=1');
  await new Promise((r) => setTimeout(r, 20));
  ok(!inert.parsed() && inert.box() === '', '?check=1 without ?list= does nothing at all');

  const many = Array.from({ length: 60 }, (_, i) => `File:P${i}.jpg`).join('\n');
  const capped = withUrl('?list=' + encodeURIComponent(many) + '&check=1');
  await new Promise((r) => setTimeout(r, 40));
  ok(lines(capped.box()) === 60, 'a 60-entry link still prefills (only the auto-check is capped)');
  ok(!capped.parsed(), 'more than 50 entries is not auto-checked');

  // ?run=1 is the share-link case: it must show the graph at once and still be unable to spend
  const replay = withUrl('?list=' + encodeURIComponent('File:A.jpg\nFile:B.jpg') + '&run=1');
  await new Promise((r) => setTimeout(r, 40));
  ok(replay.ran(), '?run=1 starts a run, so the graph appears without anyone clicking');
  ok(replay.bodies.some((b) => b.indexOf('"cached_only":true') !== -1),
     'and that run is cache-only — opening a shared link cannot spend a model call');
  ok(!replay.bodies.some((b) => b.indexOf('"no_cache":true') !== -1),
     'a replay never asks to re-score anything');
  ok(!replay.parsed(), '?run=1 does not also spend an extra validate round-trip');

  const posedLink = withUrl('?list=' + encodeURIComponent('File:A.jpg') + '&view=pose');
  ok(posedLink.doc.getElementById('cm-pose').classList.contains('active'),
     '?view=pose reproduces the by-pose chart');
  const plainLink = withUrl('?list=' + encodeURIComponent('File:A.jpg'));
  ok(plainLink.doc.getElementById('cm-combined').classList.contains('active'),
     'a plain ?list= link stays on the combined chart (the default)');

  const huge = withUrl('?list=' + 'A'.repeat(5000));
  ok(huge.box() === '', 'an over-long ?list= is refused outright, not prefilled');
  ok(/too long/.test(huge.doc.getElementById('parseinfo').textContent),
     'and it says why');

  // ---- 9. the share link reproduces this run ------------------------------------------------
  // Re-render first: the validate flow in section 7 clears the previous run (including
  // lastJob), so the share button would have nothing to describe.
  render(job(mixed, { status: 'done',
    stats: { n_scored: 4, n_input: 4, composite_mean: 0.55, p_publish_mean: 0.5,
             p_infobox_mean: 0.5, p_infobox_median: 0.5, p_infobox_max: 0.5,
             n_above_half: 2, quality_mean: 2.5, tokens_total: 100,
             wall_median_s: 0.5, wall_total_s: 2, poses: {}, n_429: 0 } }));
  doc.getElementById('cm-pose').click();
  // Asked for: one URL that shows someone the same files and immediately draws the graph, with a
  // flag for combined vs by pose. The load-bearing part is that it replays from cache only.
  doc.getElementById('share').click();
  await new Promise((r) => setTimeout(r, 10));
  const shareBox = doc.getElementById('shareurl');
  ok(/[?&]list=/.test(shareBox.value), 'the share link carries the list');
  ok(/[?&]run=1/.test(shareBox.value), 'and asks the receiving page to replay it at once');
  ok(decodeURIComponent(shareBox.value).indexOf('File:Posed.jpg') !== -1,
     "the list is the run's own titles, so the recipient sees the same photos");
  ok(/[?&]view=pose/.test(shareBox.value), 'from the by-pose view the link carries view=pose');
  doc.getElementById('cm-combined').click();
  doc.getElementById('share').click();
  await new Promise((r) => setTimeout(r, 10));
  ok(!/[?&]view=pose/.test(shareBox.value), 'and from the combined view it does not');
  ok(/Copied|Press/.test(doc.getElementById('share').textContent),
     'the button says whether it managed to copy to the clipboard');

  // ---- 10. the detail popup: see a photo's determinations without leaving the page ---------
  // Opened from a chart thumbnail or a card thumbnail, by delegation (both are rebuilt on every
  // poll). The default href would open Commons in a new tab, so preventDefault matters.
  doc.getElementById('cm-combined').click();
  const overlayEl = doc.getElementById('detail-overlay');
  ok(overlayEl.classList.contains('hidden'), 'the detail popup starts hidden');
  const thumb = doc.querySelector('#chart .cthumb');
  ok(!!thumb, 'the chart draws clickable thumbnails');
  thumb.click();
  ok(!overlayEl.classList.contains('hidden'), 'clicking a chart thumbnail opens the popup in-app');
  const detail = doc.getElementById('detail-body').innerHTML;
  ok(/composite/.test(detail), 'the popup shows the same determinations as a card');
  ok(/commons\.wikimedia\.org\/wiki\/File:/.test(detail),
     'and a link out to the Commons page');
  ok(/full-resolution file/.test(detail), 'and a link to the full-size file');
  ok(/exact image sent to the/.test(detail), 'and says which image it is showing');
  doc.getElementById('detail-close').click();
  ok(overlayEl.classList.contains('hidden'), 'Close hides it');
  doc.querySelector('#items img.thumb').click();
  ok(!overlayEl.classList.contains('hidden'), 'a card thumbnail opens it too');
  doc.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Escape' }));
  ok(overlayEl.classList.contains('hidden'), 'Escape closes it');

  // a photo with no answer yet must show its state rather than throw
  render(job([item('downloading', { title: 'File:Pending.jpg', pageid: 99 })]));
  doc.getElementById('cm-combined').click();
  const pendingThumb = doc.querySelector('#chart .cthumb');
  if (pendingThumb) { pendingThumb.click(); doc.getElementById('detail-close').click(); }
  ok(true, 'a thumbnail with no answer yet does not break the popup');

  // ---- 11. row order: ranked (the default) or as pasted --------------------------------------
  // The fixture is deliberately one whose paste order differs from its ranking (the last line is
  // the best photo), or the toggle would prove nothing.
  const shuffled = [['File:First.jpg', 31, 0.20], ['File:Second.jpg', 32, 0.90],
                    ['File:Third.jpg', 33, 0.50]].map(([title, pageid, comp]) => item('done', {
    pageid, title, raw: title, composite: comp, publish_p: 0.5,
    result: { wall_s: 0.4, usage: {}, answers: fullAnswers(0.5, 0.5, 0.9, 0.9, 'head_shoulders') } }));
  render(job(shuffled, { status: 'done',
    stats: { n_scored: 3, n_input: 3, composite_mean: 0.53, p_publish_mean: 0.5, p_infobox_mean: 0.5,
             p_infobox_median: 0.5, p_infobox_max: 0.5, n_above_half: 1, quality_mean: 2.5,
             tokens_total: 100, wall_median_s: 0.4, wall_total_s: 1.2, poses: {}, n_429: 0 } }));
  const firstRowText = () => itemsEl.children[0].textContent;
  ok(/Second/.test(firstRowText()), 'ranked order (the default) puts the best photo first');
  const firstNode = itemsEl.children[0], firstImg = firstNode.querySelector('img.thumb');
  const srcBefore = srcSets.get(firstImg);
  doc.getElementById('ord-input').click();
  ok(/First/.test(firstRowText()), 'as pasted puts the first line pasted first');
  ok(/composite rank 2 of 3/.test(itemsEl.children[1].textContent),
     'in list order the composite rank is named, so the badge cannot be misread as a rank');
  ok(srcSets.get(firstImg) === srcBefore,
     `re-ordering did not re-assign any thumbnail (still ${srcBefore} assignment(s))`);
  ok([...itemsEl.querySelectorAll('img.thumb')].indexOf(firstImg) !== -1,
     'and the same <img> node is still on the page');
  doc.getElementById('ord-ranked').click();
  ok(/Second/.test(firstRowText()), 'switching back restores the ranking');

  // ---- 12. Copy API link: the same run as a ready-to-paste curl ----------------------------
  const doneJobFor = (items) => job(items, { status: 'done',
    stats: { n_scored: items.length, n_input: items.length, composite_mean: 0.5, p_publish_mean: 0.5,
             p_infobox_mean: 0.5, p_infobox_median: 0.5, p_infobox_max: 0.5, n_above_half: 1,
             quality_mean: 2.5, tokens_total: 100, wall_median_s: 0.4, wall_total_s: 1.2,
             poses: {}, n_429: 0 } });
  render(doneJobFor(shuffled));
  doc.getElementById('apilink').click();
  await new Promise((r) => setTimeout(r, 10));
  const apiBox = doc.getElementById('shareurl').value;
  ok(/curl -sS -X POST/.test(apiBox) && /\/api\/eval/.test(apiBox),
     'Copy API link writes a curl command against /api/eval');
  ok(apiBox.indexOf('File:First.jpg') !== -1 && apiBox.indexOf('"files":') !== -1,
     "carrying this run's own files as a POST body");
  ok(apiBox.indexOf('curl') !== -1 && apiBox.indexOf('GET') === -1,
     'and it is a POST to run, not a URL that would spend on a GET');

  // more files than EVAL_MAX in one call: split, and say why
  const bulk = Array.from({ length: 26 }, (_, n) => item('done', {
    pageid: 500 + n, title: `File:Bulk ${n}.jpg`, raw: `File:Bulk ${n}.jpg`, composite: 0.1 + n * 0.01,
    result: { wall_s: 0.3, usage: {}, answers: fullAnswers(0.5, 0.5, 0.9, 0.9, 'head_shoulders') } }));
  render(doneJobFor(bulk));
  doc.getElementById('apilink').click();
  await new Promise((r) => setTimeout(r, 10));
  const bulkBox = doc.getElementById('shareurl').value;
  const nCurls = (bulkBox.match(/curl -sS -X POST/g) || []).length;
  ok(nCurls === 2, `26 files become two calls, not one over-long one (got ${nCurls})`);
  ok(/at most 25 files per call/.test(bulkBox), 'and the command explains the split');
  ok(/call 1 of 2/.test(bulkBox) && /call 2 of 2/.test(bulkBox), 'labelling each call');

  // ---- 13. the title starts a new session, but only after asking ----------------------------
  const resetEl = doc.getElementById('reset');
  ok(resetEl !== null && resetEl.getAttribute('role') === 'button',
     'the title is a button for resetting');
  render(doneJobFor(shuffled));                       // something to lose
  const before = itemsEl.children.length;
  resetEl.click();
  await new Promise((r) => setTimeout(r, 10));
  ok(!doc.getElementById('confirm-overlay').classList.contains('hidden'),
     'clicking the title asks before wiping the session');
  doc.getElementById('confirm-cancel').click();
  await new Promise((r) => setTimeout(r, 10));
  ok(itemsEl.children.length === before, 'declining leaves the run on screen');

  resetEl.click();
  await new Promise((r) => setTimeout(r, 10));
  doc.getElementById('confirm-yes').click();
  await new Promise((r) => setTimeout(r, 20));
  ok(itemsEl.children.length === 0, 'accepting clears the results');
  ok(doc.getElementById('input').value === '', 'and the file list, ready for a new one');
  ok(doc.getElementById('run').disabled, 'so Score them is disabled again');
  ok(doc.getElementById('check').classList.contains('primary'),
     'and Validate files is the next step again');
  // with nothing left to lose it must not nag
  resetEl.click();
  await new Promise((r) => setTimeout(r, 10));
  ok(doc.getElementById('confirm-overlay').classList.contains('hidden'),
     'with an empty page it does not open a dialog');
  // and it is keyboard-reachable, being a span rather than a button
  doc.getElementById('input').value = 'File:Something.jpg';
  resetEl.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Enter' }));
  await new Promise((r) => setTimeout(r, 10));
  ok(!doc.getElementById('confirm-overlay').classList.contains('hidden'),
     'Enter opens it for keyboard users');
  doc.getElementById('confirm-cancel').click();

  finish();
})();
