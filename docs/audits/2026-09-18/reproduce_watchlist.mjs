// Execute the actual useMemo body with synthetic responses; no browser/server.
import fs from 'node:fs';
import assert from 'node:assert/strict';
const source = fs.readFileSync(new URL('../../../frontend/src/pages/WatchlistPage.jsx', import.meta.url), 'utf8');
const body = source.match(/const watchlist = useMemo\(\(\) => \{([\s\S]*?)\}, \[results\]\);/);
assert.ok(body, 'Component changed: update reproduction explicitly');
const derive = new Function('results', body[1]);
const feed = Array.from({length: 500}, (_, i) => ({score_final: 1000-i, school_id: null}));
feed.push({score_final: 1, school_id: 'synthetic-school'});
assert.equal(derive({data: feed.slice(0, 500)}).length, 0);
assert.equal(derive({data: feed}).length, 1);
assert.equal(derive(undefined).length, 0);
assert.ok(source.includes('limit: 500, offset: 0'));
console.log(JSON.stringify({school_at_rank_501: 'invisible', missing_data: 'empty_list'}));
