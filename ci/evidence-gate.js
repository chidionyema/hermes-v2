#!/usr/bin/env node
/**
 * Evidence gate.
 *
 * The rule: if a pull request asserts a result, the output that proves it must
 * be in the body. Not a link to it, not a promise of it. The text.
 *
 * This is ours and we revise it every time WORK finds a way around it. Each
 * bypass that gets through becomes a case in ci/tests/test_evidence_gate_gaming.py.
 */
'use strict';

// Words that turn a sentence into an assertion about the world.
const CLAIM_WORDS = [
  'verified', 'confirmed', 'works', 'working', 'fixed', 'passes', 'passing',
  'green', 'deployed', 'tested', 'no longer', 'resolved', 'succeeds',
];

// Things people paste when they have nothing.
const PLACEHOLDERS = [
  '<paste>', '<output>', 'output here', 'todo', 'tbd', 'n/a', '...', '…',
  'see above', 'see below', 'same as before', 'as expected', 'looks good',
  'all good', 'lgtm',
];

function fencedBlocks(text) {
  const out = [];
  const re = /```[^\n]*\n([\s\S]*?)```/g;
  let m;
  while ((m = re.exec(text)) !== null) out.push(m[1]);
  return out;
}

function isRealOutput(block, claimLines) {
  const lines = block.split('\n').map((l) => l.trim()).filter(Boolean);
  if (lines.length === 0) return { ok: false, why: 'the block is empty' };

  const joined = lines.join(' ').toLowerCase();
  for (const p of PLACEHOLDERS) {
    if (joined === p || joined.replace(/[.\s]/g, '') === p.replace(/[.\s]/g, '')) {
      return { ok: false, why: `the block is a placeholder (${p})` };
    }
  }

  // A block that only restates the claim in prose is not output.
  for (const c of claimLines) {
    const norm = (s) => s.toLowerCase().replace(/[^a-z0-9 ]/g, '').trim();
    if (norm(joined) === norm(c)) {
      return { ok: false, why: 'the block just repeats the claim' };
    }
  }

  // Real command output carries at least one of: a command, a path, a number,
  // a status code, a symbol. Prose alone does not.
  const hasSignal =
    /(^|\n)\s*[$#>]\s*\S/.test(block) ||       // a prompt and a command
    /\b\d{3}\b/.test(joined) ||                // an HTTP code
    /\b\d+\s*(passed|failed|error|warning|ok)\b/.test(joined) ||
    /[/\\][\w.-]+[/\\]/.test(joined) ||        // a path
    /\b\w+\.(py|js|ts|cs|yml|yaml|toml|json|md)\b/.test(joined) ||
    /\b(exit|status|sha|commit|release|machine|version)\b/.test(joined);
  if (!hasSignal) {
    return { ok: false, why: 'the block reads as prose, not command output' };
  }

  if (joined.length < 12) return { ok: false, why: 'the block is too short to be output' };
  return { ok: true };
}

function findClaims(text) {
  // Explicit "Claim:" lines always count. Otherwise any non-quoted line that
  // asserts a result.
  const claims = [];
  const lines = text.split('\n');
  let inFence = false;
  for (const raw of lines) {
    const line = raw.trim();
    if (line.startsWith('```')) { inFence = !inFence; continue; }
    if (inFence) continue;
    if (line.startsWith('>')) continue;                 // quoting someone else
    if (/^claim:/i.test(line)) { claims.push(line.replace(/^claim:/i, '').trim()); continue; }
    const low = line.toLowerCase();
    if (CLAIM_WORDS.some((w) => low.includes(w))) claims.push(line);
  }
  return claims;
}

function main() {
  const body = process.env.PR_BODY || '';
  const title = process.env.PR_TITLE || '';
  const failures = [];

  if (!body.trim()) {
    console.error('FAIL: the pull request body is empty. State what changed and show the evidence.');
    process.exit(1);
  }

  const claims = findClaims(`${title}\n${body}`);
  const blocks = fencedBlocks(body);

  if (claims.length === 0) {
    console.log('No result claimed. Nothing to prove. PASS.');
    process.exit(0);
  }

  const good = blocks.filter((b) => isRealOutput(b, claims).ok);

  console.log(`Claims found: ${claims.length}`);
  claims.forEach((c) => console.log(`  Claim: ${c}`));
  console.log(`Fenced blocks: ${blocks.length}, usable as output: ${good.length}`);

  blocks.forEach((b, i) => {
    const r = isRealOutput(b, claims);
    if (!r.ok) console.log(`  block ${i + 1} rejected: ${r.why}`);
  });

  if (good.length === 0) {
    failures.push('claims a result and shows no command output that supports it');
  }

  // One block cannot carry an unbounded number of separate claims.
  if (claims.length > good.length * 3) {
    failures.push(`makes ${claims.length} claims backed by ${good.length} output block(s)`);
  }

  if (!/##\s*evidence/i.test(body)) {
    failures.push('has no "## Evidence" section');
  }

  if (failures.length) {
    console.error('\nFAIL: this pull request ' + failures.join('; and ') + '.');
    console.error('Put the command and what it printed in the body, under "## Evidence".');
    process.exit(1);
  }

  console.log('\nPASS: every claim has output behind it.');
}

if (require.main === module) main();
module.exports = { findClaims, fencedBlocks, isRealOutput };
