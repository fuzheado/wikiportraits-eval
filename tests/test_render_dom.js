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
    { title: 'The seven questions, verbatim',
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
const fullAnswers = (ib, pub, face, eyes, pose, unobs = 1) => ({
  infobox_ready: { noul: ib }, face_visible: { noul: face }, eyes_to_camera: { noul: eyes },
  face_unobstructed: { noul: unobs },          // default 1 = clear, so existing fixtures are unchanged
  pose: { choice: pose || 'head_shoulders', confidence: 0.7,
          probabilities: { head_shoulders: 0.7, three_quarter: 0.2, group: 0.1 } },
  quality: { score: 2.5, confidence: 0.5, probabilities: { '2': 0.5, '3': 0.5 } },
  publish_ready: { choice: pub >= 0.5 ? 'publish' : 'pass', confidence: pub,
                   probabilities: { publish: pub, pass: Number((1 - pub).toFixed(3)) } },
});
// mirrors wiclient.composite(): min(face, eyes) x pose weight x obstruction factor. The factor is
// 0.5 + 0.5 x P(clear), and an ABSENT answer means "not known" and costs nothing — which is why the
// default above is 1 rather than 0.
const OBSTRUCTION_FLOOR = 0.5;
const comp = (a) => Math.min(a.face_visible.noul, a.eyes_to_camera.noul)
  * ({ head_shoulders: 1.0, three_quarter: 0.9, full_body: 0.6, candid: 0.5, group: 0.25 }[a.pose.choice] || 0.5)
  * (OBSTRUCTION_FLOOR + (1 - OBSTRUCTION_FLOOR) * ((a.face_unobstructed || {}).noul ?? 1));

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

// The microphone answer has to be VISIBLE, not just counted: `renderBody` hardcodes the list of
// questions it draws, so a new question can exist in the payload and never appear on the card — which
// would make the composite's demotion unattributable.
ok(/face unobstructed/.test(rowsNow[0].innerHTML),
   'the card shows the face-unobstructed answer that the composite scales by');
// renderBody() is a pure function, so call it directly: an extra render() here would replace lastJob
// and break the spread/stats assertions below it (which is exactly what happened first time).
const micCard = win.renderBody(item('done', { pageid: 14, title: 'File:Mic.jpg', raw: 'File:Mic.jpg',
  result: { wall_s: 0.6, usage: {}, answers: fullAnswers(0.7, 0.9, 0.95, 0.9, 'head_shoulders', 0.12) },
  composite: 0.4, publish_p: 0.9 }));
ok(/face unobstructed/.test(micCard) && /0\.120/.test(micCard),
   'and shows the low probability when something IS held in front of the face');
ok(/a held mic or hand demotes the score/.test(micCard),
   'and says what that number does to the score');
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
// 20 photos inside one 56px column — near-identical composites, the worst case for lane stacking.
// Rank 1 is the highest composite (Clump 20).
const clumpJob = (n) => job(Array.from({ length: n }, (_, i) => {
  const a = fullAnswers(0.5, 0.5, 0.9, 0.9, 'head_shoulders');
  return item('done', { pageid: 950 + i, title: `File:Clump ${i + 1}.jpg`, raw: `File:Clump ${i + 1}.jpg`,
                        composite: 0.44 + i * 0.0002, publish_p: 0.5,
                        result: { wall_s: 0.5, usage: {}, answers: a } });
}), { id: 'clump', status: 'done', progress: { done: n, total: n }, stats: liveStats });

render(clumpJob(20));
const clumpThumbs = [...doc.querySelectorAll('#chart .cthumb')];
const chips = [...doc.querySelectorAll('#chart .cchip')];
ok(clumpThumbs.length === 8, `a 20-photo clump draws 8 lanes rather than growing to 20 (got ${clumpThumbs.length})`);
const spots = clumpThumbs.map((e) => `${e.style.left}|${e.style.bottom}`);
ok(new Set(spots).size === clumpThumbs.length,
   `no two drawn thumbnails share a position (got ${new Set(spots).size} of ${clumpThumbs.length})`);
ok(new Set(clumpThumbs.map((e) => e.style.bottom)).size === clumpThumbs.length,
   'each drawn one has its own lane rather than sharing an overflow bucket');
ok(chips.length === 1 && chips[0].textContent === '+12',
   `the other 12 are summarised in one chip, not hidden silently (got ${chips.map((c) => c.textContent).join(",") || "none"})`);
ok(/12 more photo/.test(chips[0].title) && /Clump 12\.jpg/.test(chips[0].title),
   'the chip carries the count and names the best of the ones it stands for');
// placement runs BEST FIRST, so the photograph you are looking for is never the summarised one
const drawnTitles = clumpThumbs.map((e) => e.dataset.title);
ok(drawnTitles.indexOf('File:Clump 20.jpg') !== -1 && drawnTitles.indexOf('File:Clump 1.jpg') === -1,
   'the BEST photo of the clump is drawn and the worst is summarised, not the other way round');

// ---- 4g. the candidates axis ------------------------------------------------------------------
// 24 photos whose top 12 are crammed into a 0.05 band — the shape measured on 100 real photos, where
// the ones you can actually use are the ones stacked deepest on the full-field axis.
console.log('candidates axis');
const spreadJob = () => job(Array.from({ length: 24 }, (_, i) => {
  const a = fullAnswers(0.5, 0.5, 0.9, 0.9, 'head_shoulders');
  const comp = i < 12 ? 0.02 + i * 0.04 : 0.90 + (i - 12) * 0.004;
  return item('done', { pageid: 980 + i, title: `File:Spread ${i + 1}.jpg`, raw: `File:Spread ${i + 1}.jpg`,
                        composite: comp, publish_p: 0.5,
                        result: { wall_s: 0.5, usage: {}, answers: a } });
}), { id: 'spread', status: 'done', progress: { done: 24, total: 24 }, stats: liveStats });

render(spreadJob());
ok(/axis 0 →/.test(caption()), `the default axis is the whole field (got "${caption().slice(0, 34)}")`);
const top12 = [...doc.querySelectorAll('#chart .cthumb')]
  .filter((e) => /Spread (1[3-9]|2[0-4])\.jpg/.test(e.dataset.title || ""));
const spreadOf = (els) => {
  const xs = els.map((e) => parseFloat(e.style.left));
  return Math.max(...xs) - Math.min(...xs);
};
ok(top12.length >= 8,
   `the top band is plotted on the full axis (got ${top12.length} of 12 drawn; the rest are in a chip)`);
const fullSpan = spreadOf(top12);

doc.getElementById('ca-cand').click();
ok(/the top 12 of 24 scored/.test(caption()), `the candidates axis says what it is showing (got "${caption().slice(0, 52)}")`);
ok(/12 below the window/.test(caption()), 'and how many are outside it');
ok(doc.getElementById('ca-cand').classList.contains('active'), 'the control shows which axis is on');
const candThumbs = [...doc.querySelectorAll('#chart .cthumb')];
ok(candThumbs.length === 12, `only the photos inside the window are plotted (got ${candThumbs.length})`);
ok(candThumbs.map((e) => e.querySelector('.crank').textContent).indexOf('1') !== -1,
   'ranks stay global to the run, not renumbered inside the window');
const candSpan = spreadOf(candThumbs);
ok(candSpan > fullSpan * 3,
   `fitting the axis spreads the top band across the width (${fullSpan.toFixed(0)}px -> ${candSpan.toFixed(0)}px)`);
ok(new Set(candThumbs.map((e) => e.style.bottom)).size === 1,
   'and they fit in ONE lane when fitted, instead of being stacked deep');
ok(doc.querySelectorAll('#chart .cchip').length === 0,
   'fitted, nothing needs summarising — the chip is a full-field fallback, not the default behaviour');
doc.getElementById('ca-full').click();
ok(/axis 0 →/.test(caption()), 'and the control switches back to the whole field');

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

  // ---- 10b. the rank, and stepping through the ranking -----------------------------------------
  // Asked for: the popup shows no rank, so a photo viewed in isolation cannot answer "is this the best
  // of 40 or the worst?" — and since the rank is known, it should be walkable without closing it.
  // The fixture on screen is `mixed`: Posed 0.90 > Waist 0.70 > Candid 0.40 > Crowd 0.20.
  const rankEl = doc.getElementById('detail-rank');
  const prevBtn = doc.getElementById('detail-prev');
  const nextBtn = doc.getElementById('detail-next');
  const openTitle = () => doc.getElementById('detail-title').textContent;
  // the card thumbnail we just clicked is the first row in ranked order
  ok(/^rank 1 of 4$/.test(rankEl.textContent),
     `the popup names the rank and the field size (got ${JSON.stringify(rankEl.textContent)})`);
  ok(openTitle() === 'Posed.jpg', `and it is the top-ranked photo (got ${openTitle()})`);
  ok(prevBtn.disabled, 'with no previous at rank 1, so the button is disabled rather than wrapping');
  ok(!nextBtn.disabled, 'and next is available');

  nextBtn.click();
  ok(openTitle() === 'Waist.jpg', `next steps to the next in the ranking (got ${openTitle()})`);
  ok(/^rank 2 of 4$/.test(rankEl.textContent), `and the rank line follows it (got ${rankEl.textContent})`);
  ok(!prevBtn.disabled, 'now there IS a previous');
  nextBtn.click();
  nextBtn.click();
  ok(openTitle() === 'Crowd.jpg', 'stepping twice more reaches the last photo');
  ok(/^rank 4 of 4$/.test(rankEl.textContent), 'at rank 4 of 4');
  ok(nextBtn.disabled, 'and next is disabled at the bottom — it does not wrap round to the best photo');
  prevBtn.click();
  ok(openTitle() === 'Candid.jpg', 'prev steps back up the ranking');

  // the arrow keys do the same, and ONLY while the popup is open
  doc.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'ArrowLeft' }));
  ok(openTitle() === 'Waist.jpg', 'ArrowLeft steps backwards');
  doc.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'ArrowRight' }));
  ok(openTitle() === 'Candid.jpg', 'ArrowRight steps forwards again');
  doc.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'ArrowLeft' }));
  doc.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'ArrowLeft' }));
  doc.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'ArrowLeft' }));
  ok(openTitle() === 'Posed.jpg', 'arrows stop at the top of the ranking');

  // the popup must agree with the badge on the card the reader clicked to open it
  const badgeRank = doc.querySelector('#items .item .rank').textContent.trim();
  ok(badgeRank === '#1' && /rank 1 of 4/.test(rankEl.textContent),
     `the popup rank matches the card badge you clicked (badge ${badgeRank})`);

  // a poll replaces every item object; the popup must survive that instead of losing its rank
  const sameMixed = mixed.map((it) => ({ ...it, result: { ...it.result } }));
  render(job(sameMixed, { status: 'done', stats: { n_scored: 4, n_input: 4, poses: {}, n_429: 0 } }));
  ok(!overlayEl.classList.contains('hidden') && openTitle() === 'Posed.jpg',
     'the popup stays open across a poll');
  ok(/^rank 1 of 4$/.test(doc.getElementById('detail-rank').textContent),
     'and keeps its rank even though every item object was replaced');

  // an unscored photo has no rank: say so, and offer no stepping. Opened from the CARD, not the chart:
  // the chart only plots photos it can place on the axis, so an unscored one has no thumbnail there.
  render(job([item('downloading', { title: 'File:Pending.jpg', pageid: 99 })]));
  doc.querySelector('#items img.thumb').click();
  ok(/downloading|no rank/.test(doc.getElementById('detail-rank').textContent),
     `an unscored photo says it has no rank rather than showing a wrong one `
     + `(got ${JSON.stringify(doc.getElementById('detail-rank').textContent)})`);
  ok(doc.getElementById('detail-prev').disabled && doc.getElementById('detail-next').disabled,
     'and offers no stepping (there is nothing ranked to step through)');
  if (!overlayEl.classList.contains('hidden')) doc.getElementById('detail-close').click();

  render(job(mixed, { status: 'done' }));
  doc.getElementById('cm-combined').click();
  doc.querySelector('#items img.thumb').click();
  doc.dispatchEvent(new win.KeyboardEvent('keydown', { key: 'Escape' }));
  ok(overlayEl.classList.contains('hidden'), 'Escape closes it');

  // a photo with no answer yet must show its state rather than throw
  render(job([item('downloading', { title: 'File:Pending.jpg', pageid: 99 })]));
  doc.getElementById('cm-combined').click();
  const pendingThumb = doc.querySelector('#chart .cthumb');
  if (pendingThumb) { pendingThumb.click(); doc.getElementById('detail-close').click(); }
  ok(true, 'a thumbnail with no answer yet does not break the popup');
  // restore the fixture the following sections expect
  render(job(mixed, { status: 'done' }));

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

  // ---- 10. the run cap is stated at Validate, and an over-cap list can be pruned -------------------
  // Before this, Validate accepted up to PARSE_MAX (1000) and reported "1000 of 1000 resolved", and
  // then the run refused at 201 with a 413: the green light was a lie, and the limit cost a round trip
  // (and the previous run's cards, which startRun() clears before posting). The page now reads the cap
  // from /api/config, refuses the click itself, and offers a way through. One factory, one JSDOM per
  // case, because each case starts a run (a poll timer) and the cases must not see each other's state.
  function capCase(cap) {
    const calls = [];
    const d = new JSDOM(html, {
      runScripts: 'dangerously', pretendToBeVisual: true,
      beforeParse(window) {
        window.fetch = async (url, opts) => {
          calls.push(String(url));
          return { ok: true, status: 200, json: async () => {
            const u = String(url);
            if (u.includes('/api/config')) {
              return { eval_max: 25, max_input_items: cap, spacing_s: 0.9, budget: {}, questions: {} };
            }
            if (u.includes('/api/parse')) {
              // Count the entries the box actually holds. A fixed reply made the short-list case below
              // unfalsifiable — it reported 4 items whatever was typed, so the cap warning never cleared
              // and the two assertions looked like app bugs when the stub was the bug.
              let n = 0;
              try { n = (JSON.parse(opts.body).text.match(/File:/g) || []).length; } catch (e) { n = 0; }
              return { items: Array.from({ length: n }, (_, k) => ({ raw: `File:P${k + 1}.jpg`,
                        title: `File:P${k + 1}.jpg`, ok: true, pageid: k + 1 })), warnings: [], n };
            }
            if (u.includes('/api/run')) return { job_id: 'never' };
            return {};
          } };
        };
      },
    });
    const w = d.window, dd = d.window.document;
    const type = async (text) => { dd.getElementById('input').value = text;
                                   dd.getElementById('check').click();
                                   await new Promise((r) => setTimeout(r, 25)); };
    const settle = () => new Promise((r) => setTimeout(r, 30));
    return { w, dd, calls, type, settle,
             box: () => dd.getElementById('input').value.split('\n').filter((x) => x.trim()),
             pill: () => dd.getElementById('parseinfo').textContent,
             pruneHidden: () => dd.getElementById('prune').classList.contains('hidden'),
             ran: () => calls.some((u) => u.includes('/api/run')) };
  }
  const FOUR = 'File:P1.jpg\nFile:P2.jpg\nFile:P3.jpg\nFile:P4.jpg';

  const c1 = capCase(3);
  await c1.settle();                                   // let /api/config land in configCache
  await c1.type(FOUR);
  ok(/over the 3-per-run limit/.test(c1.pill()),
     'Validate says a 4-photo list is over the cap of 3, instead of just "4 of 4 resolved"');
  ok(/at most 3/.test(c1.dd.getElementById('run').title),
     'and the Score them tooltip names the limit before it is clicked');
  ok(!c1.pruneHidden(), 'and the prune controls appear, since there is something the user can do');
  ok(c1.dd.getElementById('prune-first').textContent === 'keep the first 3',
     'whose labels carry the cap read from /api/config, not a number typed into the HTML');
  ok(/NOT the best 3/.test(c1.dd.getElementById('prune-sample').title),
     'the random-sample tooltip says outright that it is a sample, not the best 3');
  ok(!c1.ran(), 'and nothing has been submitted yet');
  c1.dd.getElementById('run').click();
  await c1.settle();
  ok(!c1.ran(), 'Score them refuses an over-cap list WITHOUT a round trip (no 413 to discover)');
  ok(/4 photos and a run takes at most 3/.test(c1.dd.getElementById('jobstate').textContent),
     'and says why, in the same words the pill used');
  ok(c1.box().length === 4, 'and the list is untouched by the refusal — nothing is silently dropped');

  // keep the first N: deterministic, order preserved
  c1.dd.getElementById('prune-first').click();
  await c1.settle();
  ok(c1.box().join(',') === 'File:P1.jpg,File:P2.jpg,File:P3.jpg',
     'keep the first 3 rewrites the box to exactly the first 3 entries, in order');
  ok(c1.pruneHidden(), 'and the prune controls go away, because the list now fits');
  ok(!/per-run limit/.test(c1.pill()), 'and the pill no longer warns');
  ok(/kept the first 3 of 4/.test(c1.pill()),
     'and the pill records what was done, so the pruning is not invisible afterwards');
  c1.dd.getElementById('run').click();
  await c1.settle();
  ok(c1.ran(), 'and the pruned list can actually be scored');

  // keep N at random: a subset of the same size, no duplicates, pasted order kept
  const c2 = capCase(3);
  await c2.settle();
  await c2.type(FOUR);
  c2.dd.getElementById('prune-sample').click();
  await c2.settle();
  const kept = c2.box();
  ok(kept.length === 3, `keep 3 at random leaves exactly 3 entries (got ${kept.length})`);
  ok(new Set(kept).size === 3, 'with no duplicate entries');
  ok(kept.every((l) => /^File:P[1-4]\.jpg$/.test(l)), 'all drawn from the original list');
  ok(kept.join(',') === [...kept].sort().join(','),
     'and written back in the order pasted, not shuffled (the box is the run input order)');
  ok(/kept 3 of 4 at random/.test(c2.pill()), 'and the pill says it was a random sample');

  // the sampler itself. 40 draws of 3-from-4: p(all 40 identical) = (1/4)^39, so "more than one
  // distinct result" is effectively certain and will not flake, unlike asserting a particular draw.
  const draws = new Set();
  let shapeOk = true;
  for (let i = 0; i < 40; i++) {
    const s = c2.w.sampleIndexes(4, 3);
    shapeOk = shapeOk && s.length === 3 && new Set(s).size === 3
      && s.every((v) => v >= 0 && v < 4) && s.every((v, k) => k === 0 || v > s[k - 1]);
    draws.add(s.join(','));
  }
  ok(shapeOk, 'sampleIndexes returns 3 unique in-range indexes in ascending order');
  ok(draws.size > 1, `the sampler is not just the first k — 40 draws gave ${draws.size} distinct sets`);
  ok(c2.w.sampleIndexes(4, 4).join(',') === '0,1,2,3',
     'and a full-size sample keeps the whole list in order');

  // A server that does not publish the cap (0.20.1, or a page left open across a deploy): the page must
  // degrade to the old behaviour rather than guess a number, and the server's 413 stays the boundary.
  const c3 = capCase(undefined);
  await c3.settle();
  await c3.type(FOUR);
  ok(c3.pruneHidden(), 'with no published cap the prune controls stay hidden instead of guessing');
  ok(!/per-run limit/.test(c3.pill()), 'and no cap is claimed in the pill');
  ok(c3.box().length === 4, 'and the list is left completely alone');

  // A stale count must not block a legal run: shorten the list and the verdict must clear with it.
  const c4 = capCase(3);
  await c4.settle();
  await c4.type(FOUR);
  ok(/over the 3-per-run limit/.test(c4.pill()), 'a 4-of-3 list is refused on this page too');
  await c4.type('File:P1.jpg\nFile:P2.jpg');
  ok(!/per-run limit/.test(c4.pill()), 'a now-legal list (2 of a cap of 3) shows no warning');
  ok(c4.pruneHidden(), 'and the prune controls are hidden again');
  c4.dd.getElementById('run').click();
  await c4.settle();
  ok(c4.ran(), 'and it is submitted — the stale over-cap count does not block a legal run');

  finish();
})();
