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

const html = fs.readFileSync(path.join(__dirname, 'static', 'index.html'), 'utf8');

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
     `Load test data fills the box from /api/default-list (got ${inputEl.value.split('\n').length} line(s))`);
  ok(/2 test photos loaded/.test(doc.getElementById('parseinfo').textContent),
     'the pill reports how many test photos were loaded');

  // ---- 7. the button affordance walks Check inputs -> Score them ---------------------------
  // The blue "primary" style is the only thing telling a first-time user which button is next, so it
  // must move with the stage: Check -> (list resolved) -> Score them -> (list edited) -> Check.
  const checkBtn = doc.getElementById('check'), runBtn = doc.getElementById('run');
  ok(checkBtn.classList.contains('primary') && runBtn.disabled,
     'before checking: Check inputs is primary and Score them is disabled');
  checkBtn.click();
  await new Promise((r) => setTimeout(r, 20));
  // earlier sections rendered job payloads, so a previous run exists and the confirm must appear
  const confirmEl = doc.getElementById('confirm-overlay');
  ok(!confirmEl.classList.contains('hidden'),
     'a check with results on screen asks before clearing them');
  doc.getElementById('confirm-yes').click();
  await new Promise((r) => setTimeout(r, 40));
  ok(!checkBtn.classList.contains('primary'),
     'after a successful check the blue leaves Check inputs');
  ok(runBtn.classList.contains('primary') && !runBtn.disabled,
     'after a successful check Score them is primary and enabled');
  doc.getElementById('input').dispatchEvent(new win.Event('input'));
  await new Promise((r) => setTimeout(r, 10));
  ok(checkBtn.classList.contains('primary') && runBtn.disabled,
     'editing the list puts the flow back to step 1 (Check inputs primary, Score them disabled)');

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
  // can assert the two rules that matter: a link may prefill and (with check=1) press Check inputs,
  // but it must NEVER cause a run, and it must never split on a comma.
  function withUrl(search) {
    const calls = [];
    const d2 = new JSDOM(html, {
      runScripts: 'dangerously', pretendToBeVisual: true,
      url: 'https://example.test/' + search,
      beforeParse(window) {
        window.fetch = async (url) => {
          calls.push(String(url));
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
    return { win: d2.window, doc: d2.window.document, calls,
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
  ok(checked.parsed(), '?check=1 presses Check inputs');
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

  const huge = withUrl('?list=' + 'A'.repeat(5000));
  ok(huge.box() === '', 'an over-long ?list= is refused outright, not prefilled');
  ok(/too long/.test(huge.doc.getElementById('parseinfo').textContent),
     'and it says why');

  finish();
})();
