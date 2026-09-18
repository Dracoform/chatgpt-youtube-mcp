// Browser-generator tests (Stage 4 capability model). Run with plain
// `node tests/test_web_generator.cjs`. No npm, no DOM: generator.js exposes
// pure helpers via module.exports.
// Exit code 0 = all tests passed.

'use strict';
const assert = require('assert');
const { spawnSync } = require('child_process');
const path = require('path');
const fs = require('fs');

const REPO = path.resolve(__dirname, '..');
const gen = require(path.join(REPO, 'docs', 'assets', 'generator.js'));
const FIXTURES = path.join(REPO, 'tests', 'fixtures');
const SH_GENERATOR = path.join(REPO, 'generators',
  'generate_docker-compose_for_ChatGPT_MCP.sh');

// Test values, built at runtime so no complete secret appears verbatim here.
const YOUTUBE_KEY = 'AIza' + 'SyA1234567890' + 'abcdefghijklmnopqrstuv';
assert.strictEqual(YOUTUBE_KEY.length, 39);
const RUNTIME_KEY = 's' + 'k-' + 'proj-test1234567890123456789012345678';
const NON_STANDARD_RUNTIME = 'custom-opaque-token';
const SHORT_SECRET = 'abc';
const TUNNEL_ID = 'tunnel_0123456789abcdef0123456789abcdef';

const CASES = ['tunnel-only', 'static-public', 'oauth-public', 'static-oauth',
  'tunnel-static-oauth', 'local'];

let passed = 0;
const failures = [];
const SECRET_VALUES = [YOUTUBE_KEY, RUNTIME_KEY, NON_STANDARD_RUNTIME];

function _redact(text) {
  let out = String(text);
  for (const s of SECRET_VALUES) {
    if (s) { out = out.split(s).join('***REDACTED***'); }
  }
  return out;
}

function test(name, fn) {
  try {
    fn();
    passed++;
    console.log(`ok   ${name}`);
  } catch (err) {
    const safe = _redact(err && err.message);
    failures.push(name);
    console.error(`FAIL ${name}\n     ${safe}`);
  }
}

function oracleYaml(fixture) {
  // Render via generators/canonical_model.py (single source of truth).
  const r = spawnSync('uv', ['run', 'python', '-m', 'generators.canonical_model',
    path.join(FIXTURES, fixture + '.json')],
    { cwd: REPO, encoding: 'utf8', timeout: 30000 });
  assert.strictEqual(r.status, 0, `oracle failed for ${fixture}: ${_redact(r.stderr)}`);
  return r.stdout;
}

function fixtureObject(fixture) {
  return JSON.parse(fs.readFileSync(path.join(FIXTURES, fixture + '.json'), 'utf8'));
}

/* ------------------------------------------------------------- masking */
test('mask length equals value length and shows final four', () => {
  const mask = gen.maskSecret(YOUTUBE_KEY);
  assert.strictEqual(mask.length, YOUTUBE_KEY.length);
  assert.ok(mask.startsWith('X'.repeat(35)));
  assert.strictEqual(mask.slice(-4), YOUTUBE_KEY.slice(-4));
});

test('short secrets are fully masked', () => {
  assert.strictEqual(gen.maskSecret(SHORT_SECRET), 'XXX');
  assert.strictEqual(gen.maskSecret('ab'), 'XX');
  assert.strictEqual(gen.maskSecret(''), '');
  assert.strictEqual(gen.maskSecret('abcd'), 'XXXX');
});

test('five-character secret shows exactly the last character', () => {
  assert.strictEqual(gen.maskSecret('abcde'), 'Xbcde');
});

/* ---------------------------------------------------------- validation */
test('leading/trailing whitespace is rejected', () => {
  assert.ok(gen.secretHasControlCharsOrEdgeWhitespace(' ' + YOUTUBE_KEY));
  assert.ok(gen.secretHasControlCharsOrEdgeWhitespace(YOUTUBE_KEY + ' '));
  assert.ok(gen.secretHasControlCharsOrEdgeWhitespace('\t' + YOUTUBE_KEY));
});

test('control characters are rejected', () => {
  assert.ok(gen.secretHasControlCharsOrEdgeWhitespace('a\nb'));
  assert.ok(gen.secretHasControlCharsOrEdgeWhitespace('a\rb'));
  assert.ok(gen.secretHasControlCharsOrEdgeWhitespace('a\u0000b'));
  assert.ok(gen.secretHasControlCharsOrEdgeWhitespace('a\u001fb'));
});

test('clean secrets and inner whitespace are accepted', () => {
  assert.ok(!gen.secretHasControlCharsOrEdgeWhitespace(YOUTUBE_KEY));
  assert.ok(!gen.secretHasControlCharsOrEdgeWhitespace('a b'));
});

test('patterns detect malformed values', () => {
  assert.ok(!gen.GOOGLE_KEY_PATTERN.test('not-a-google-key'));
  assert.ok(gen.GOOGLE_KEY_PATTERN.test(YOUTUBE_KEY));
  assert.ok(!gen.OPENAI_KEY_PREFIX.test(NON_STANDARD_RUNTIME));
  assert.ok(gen.OPENAI_KEY_PREFIX.test(RUNTIME_KEY));
  assert.ok(!gen.TUNNEL_ID_PATTERN.test('not-a-tunnel'));
  assert.ok(gen.TUNNEL_ID_PATTERN.test(TUNNEL_ID));
  assert.ok(!gen.LANGUAGES_PATTERN.test('de;;en'));
  assert.ok(gen.LANGUAGES_PATTERN.test('de,en,fr'));
});

/* ---------------------------------------------------------------- YAML */
test('yaml quoter matches the reference scalar emitter', () => {
  assert.strictEqual(gen.yamlQuote("it's"), "'it''s'");
  assert.strictEqual(gen.yamlQuote('plain'), "'plain'");
  assert.strictEqual(gen.yamlScalar(true), "'true'");
  assert.strictEqual(gen.yamlScalar(false), "'false'");
  assert.strictEqual(gen.yamlScalar('...'), "'...'");
});

test('buildYaml === canonical_model.py for all six fixtures', () => {
  for (const fixture of CASES) {
    const obj = fixtureObject(fixture);
    const web = gen.buildYaml(obj);
    const expected = oracleYaml(fixture);
    assert.strictEqual(web, expected, `web != oracle for ${fixture}`);
  }
});

test('buildYaml round-trips through normalizeModel', () => {
  for (const fixture of ['static-oauth', 'tunnel-static-oauth']) {
    const obj = fixtureObject(fixture);
    const normalized = gen.normalizeModel(obj);
    const raw = gen.buildYaml(obj);
    const fromNorm = gen.buildYaml(normalized);
    assert.strictEqual(raw, fromNorm, `normalize changes output for ${fixture}`);
  }
});

/* ------------------------------------------------------- validation parity */
test('JS validation rejects the same combinations as the Python model', () => {
  const base = {
    mcp: { tag: 'latest' },
    access: ['static'],
    edge: { auth_modes: ['static'], tls: 'ingress',
            static_tokens: ['ytsk_ok'], oauth: {} },
  };
  const bad = [
    // no-auth public edge
    { ...base, edge: { ...base.edge, auth_modes: [] } },
    // oauth missing resource
    { ...base, access: ['oauth'],
      edge: { ...base.edge, auth_modes: ['oauth'], oauth: { issuer: 'x' } } },
    // half TLS
    { ...base, edge: { ...base.edge, tls: 'edge', cert_file: '/c', key_file: '' } },
    // static+oauth namespace ambiguity
    { ...base, access: ['static', 'oauth'],
      edge: { ...base.edge, auth_modes: ['static', 'oauth'],
              oauth: { issuer: 'https://as/r', resource: 'https://m/mcp' },
              static_tokens: ['noprefix'] } },
    // empty access
    { ...base, access: [] },
  ];
  for (const model of bad) {
    assert.throws(() => gen.validateModel(model), undefined,
      `expected rejection for ${JSON.stringify(model.access)}`);
  }
  // a valid model must not throw
  assert.doesNotThrow(() => gen.validateModel(base));
});

test('token parsing handles comma lists and empty', () => {
  assert.deepStrictEqual(gen.parseStaticTokens('a,b'), ['a', 'b']);
  assert.deepStrictEqual(gen.parseStaticTokens(''), []);
});

/* ------------------------------------------- three-way (real execution) */
test('three-way equivalence: Bash + Web vs canonical oracle', () => {
  // Run the real Bash generator (--input) and the real web buildYaml for each
  // capability case and compare both to the canonical-model oracle. pwsh is
  // exercised separately in the Python equivalence test (self-skips if absent).
  const tmp = fs.mkdtempSync(path.join(require('os').tmpdir(), 'threeway-cap-'));
  for (const fixture of CASES) {
    const inputJson = path.join(FIXTURES, fixture + '.json');
    const expected = oracleYaml(fixture);
    const bashOut = path.join(tmp, `bash-${fixture}.yml`);
    const bash = spawnSync('bash',
      [SH_GENERATOR, '--input', inputJson, '--output', bashOut],
      { encoding: 'utf8', timeout: 30000 });
    assert.strictEqual(bash.status, 0,
      `bash failed for ${fixture}: ${_redact(bash.stderr)}`);
    const bashYaml = fs.readFileSync(bashOut, 'utf8');
    assert.strictEqual(bashYaml, expected, `bash != oracle for ${fixture}`);
    const web = gen.buildYaml(fixtureObject(fixture));
    assert.strictEqual(web, expected, `web != oracle for ${fixture}`);
  }
  console.log('        (Bash + Web both byte-equal to canonical oracle for all six cases)');
});

/* ------------------------------------------------- static security scan */
test('web sources contain no network/storage/telemetry primitives', () => {
  const files = [
    path.join(REPO, 'docs', 'index.html'),
    path.join(REPO, 'docs', 'assets', 'generator.js'),
    path.join(REPO, 'docs', 'assets', 'styles.css'),
  ];
  const forbidden = [
    /\bfetch\s*\(/,
    /\bXMLHttpRequest\b/,
    /\bWebSocket\b/,
    /\bEventSource\b/,
    /\bsendBeacon\b/,
    /\blocalStorage\b/,
    /\bsessionStorage\b/,
    /\bindexedDB\b/,
    /document\.cookie/,
    /\banalytics\b/i,
    /\btelemetry\b/i,
    /<script\s+[^>]*src=["']https?:/,
    /<link\s+[^>]*href=["']https?:/,
    /\baction\s*=\s*["'][^"']/,
  ];
  for (const file of files) {
    const text = fs.readFileSync(file, 'utf8');
    const stripped = text
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/^\s*\/\/.*$/gm, '')
      .replace(/<!--[\s\S]*?-->/g, '');
    for (const pattern of forbidden) {
      assert.ok(!pattern.test(stripped),
        `${path.basename(file)} contains forbidden primitive: ${pattern}`);
    }
  }
});

test('web sources contain no external runtime URLs', () => {
  const html = fs.readFileSync(path.join(REPO, 'docs', 'index.html'), 'utf8');
  const srcs = [...html.matchAll(/(?:src|href)=["']([^"']+)["']/g)].map(m => m[1]);
  for (const src of srcs) {
    if (/^https?:/.test(src)) {
      assert.ok(src.startsWith('https://github.com/Dracoform/chatgpt-youtube-mcp') ||
                src.startsWith('https://platform.openai.com/'),
        `unexpected external reference: ${src}`);
    }
    if (src.startsWith('assets/') || src.endsWith('.md')) {
      const target = path.join(REPO, 'docs', src.split('#')[0]);
      assert.ok(fs.existsSync(target), `missing local reference target: ${src}`);
    }
  }
  const js = fs.readFileSync(path.join(REPO, 'docs', 'assets', 'generator.js'), 'utf8');
  const urls = js.match(/https?:\/\/[^'"\s]*/g) || [];
  for (const u of urls) {
    assert.ok(u.startsWith('http://youtube-mcp:'),
      `generator.js contains an unexpected URL: ${u}`);
  }
});

test('CSP is present and strict', () => {
  const html = fs.readFileSync(path.join(REPO, 'docs', 'index.html'), 'utf8');
  for (const directive of [
    "default-src 'self'", "connect-src 'none'", "script-src 'self'",
    "style-src 'self'", "object-src 'none'", "base-uri 'none'", "form-action 'none'",
    "frame-src 'none'", "font-src 'self'",
  ]) {
    assert.ok(html.includes(directive), `CSP missing: ${directive}`);
  }
});

test('capability form exposes local/tunnel/static/oauth controls', () => {
  const html = fs.readFileSync(path.join(REPO, 'docs', 'index.html'), 'utf8');
  for (const needle of ['access_local', 'access_tunnel', 'access_static', 'access_oauth']) {
    assert.ok(html.includes(needle), `missing access control: ${needle}`);
  }
});

test('secret-form inputs disable autocomplete', () => {
  const html = fs.readFileSync(path.join(REPO, 'docs', 'index.html'), 'utf8');
  for (const id of ['youtube_api_key', 'runtime_api_key']) {
    if (html.indexOf(`id="${id}"`) === -1) { continue; }
    const tag = html.slice(html.indexOf(`id="${id}"`) - 200,
                           html.indexOf(`id="${id}"`) + 400);
    assert.ok(tag.includes('autocomplete="off"'), `${id}: autocomplete`);
  }
});

/* ----------------------------------------------------------------- end */
console.log(`\n${passed} passed, ${failures.length} failed`);
if (failures.length) {
  console.error('failed tests: ' + failures.join(', '));
  process.exit(1);
}
