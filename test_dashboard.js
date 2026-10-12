// Regression checks for the phone countdown. Run with: node test_dashboard.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const page = fs.readFileSync(path.join(__dirname, 'docs', 'index.html'), 'utf8');
const sections = [
 ['const esc=', 'const P='],
 ['const fmtN=', 'async function getKey()'],
 ['function grassHTML(', 'function connectHTML()'],
 ['function tickCount()', 'async function poll()'],
].map(([start, end]) => {
 const first = page.indexOf(start), last = page.indexOf(end, first);
 assert.ok(first >= 0 && last > first, start);
 return page.slice(first, last);
}).join('\n');

const start = Date.parse('2026-01-01T10:00:00Z') / 1000;
let now = start, element;
const context = vm.createContext({
 Date: class extends Date { static now() { return now * 1000; } },
 document: {querySelectorAll: () => [element]},
});
vm.runInContext(sections, context);

const block = {reset: start + 3600, tok: 100, ratio: 0.5};
const expiredBlock = {...block, reset: start - 1};
const weekly = {reset: start + 86400, ratio: 0.9};
const cases = [
 {block, wk: weekly, reset: block.reset},
 {block: null, wk: weekly, reset: weekly.reset},
 {block: expiredBlock, wk: weekly, reset: weekly.reset},
 {block: null, wk: {...weekly, ratio: 0.5}, reset: 0},
 {block: expiredBlock, wk: {...weekly, reset: start - 1}, reset: 0},
];

for (const sample of cases) {
 now = start;
 context.agents = [{id: 'claude-code', name: 'Claude Code',
                    today: 100, week: 100, block: sample.block, wk: sample.wk}];
 const html = vm.runInContext('agentCardsHTML(agents)', context);
 const count = html.match(/class="count"[^>]*data-reset="([^"]*)">([^<]*)<\/div>/);
 assert.ok(count);
 assert.equal(count[1], sample.reset ? String(sample.reset) : '');
 context.remaining = sample.reset - now;
 assert.equal(count[2], sample.reset ? vm.runInContext('dur(remaining)', context) : 'Refueled');

 element = {dataset: {reset: count[1]}, textContent: count[2]};
 now += 1;
 vm.runInContext('tickCount()', context);
 context.remaining = sample.reset - now;
 assert.equal(element.textContent,
              sample.reset ? vm.runInContext('dur(remaining)', context) : 'Refueled');
}
