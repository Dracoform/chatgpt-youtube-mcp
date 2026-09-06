// Browser-generator tests: run with plain `node tests/test_web_generator.mjs`.
// No npm install, no dependencies, no DOM: the generator module exposes its
// pure helpers via module.exports when loaded outside a browser.
// Exit code 0 = all tests passed.

'use strict';
const assert = require('assert');
const { execFileSync, spawnSync } = require('child_process');
const path = require('path');
const fs = require('fs');

const REPO = path.resolve(__dirname, '..');
const gen = require(path.join(REPO, 'docs', 'assets', 'generator.js'));

// Test values, built at runtime so no complete secret appears verbatim here.
const YOUTUBE_KEY = 'AIza' + 'SyA1234567890' + 'abcdefghijklmnopqrstuv';
assert.strictEqual(YOUTUBE_KEY.length, 39);
const RUNTIME_KEY = 's' + 'k-' + 'proj-test1234567890123456789012345678';
const NON_STANDARD_RUNTIME = 'custom-opaque-token';
const SHORT_SECRET = 'abc';
const TUNNEL_ID = 'tunnel_0123456789abcdef0123456789abcdef';

let passed = 0;
const failures = [];

function test(name, fn) {
  try {
    fn();
    passed++;
    console.log(`ok   ${name}`);
  } catch (err) {
    // Never include secret values in diagnostics.
    const safe = String(err && err.message).replace(
      new RegExp(YOUTUBE_KEY + '|' + RUNTIME_KEY + '|' + NON_STANDARD_RUNTIME, 'g'),
      '***REDACTED***');
    failures.push(name);
    console.error(`FAIL ${name}\n     ${safe}`);
  }
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
  assert.ok(gen.secretHasControlCharsOrEdgeWhitespace(' ' + YOUTUBE_KEY + ' '));
  assert.ok(gen.secretHasControlCharsOrEdgeWhitespace('\t' + YOUTUBE_KEY));
});

test('control characters are rejected', () => {
  assert.ok(gen.secretHasControlCharsOrEdgeWhitespace('a\nb'));
  assert.ok(gen.secretHasControlCharsOrEdgeWhitespace('a\rb'));
  assert.ok(gen.secretHasControlCharsOrEdgeWhitespace('a\u0000b'));
  assert.ok(gen.secretHasControlCharsOrEdgeWhitespace('a\u001fb'));
  assert.ok(gen.secretHasControlCharsOrEdgeWhitespace('a\u007fb'));
});

test('clean secrets and inner whitespace are accepted', () => {
  assert.ok(!gen.secretHasControlCharsOrEdgeWhitespace(YOUTUBE_KEY));
  assert.ok(!gen.secretHasControlCharsOrEdgeWhitespace('a b'));
});

test('malformed Google key is detected', () => {
  assert.ok(!gen.GOOGLE_KEY_PATTERN.test('not-a-google-key'));
  assert.ok(!gen.GOOGLE_KEY_PATTERN.test(YOUTUBE_KEY.slice(0, 38))); // too short
  assert.ok(gen.GOOGLE_KEY_PATTERN.test(YOUTUBE_KEY));
});

test('non-sk runtime key is detected', () => {
  assert.ok(!gen.OPENAI_KEY_PREFIX.test(NON_STANDARD_RUNTIME));
  assert.ok(gen.OPENAI_KEY_PREFIX.test(RUNTIME_KEY));
});

test('invalid tunnel id and language list are detected', () => {
  assert.ok(!gen.TUNNEL_ID_PATTERN.test('not-a-tunnel'));
  assert.ok(gen.TUNNEL_ID_PATTERN.test(TUNNEL_ID));
  assert.ok(!gen.LANGUAGES_PATTERN.test('de;;en'));
  assert.ok(gen.LANGUAGES_PATTERN.test('de,en,fr'));
});

/* ---------------------------------------------------------------- YAML */

test('yaml single-quote escaping matches the shell generators', () => {
  assert.strictEqual(gen.yamlQuote("it's"), "'it''s'");
  assert.strictEqual(gen.yamlQuote('plain'), "'plain'");
});

test('generated YAML matches the Bash generator structurally', () => {
  const input = {
    mcpTag: '0.9.9',
    tunnelTag: '0.9.8',
    youtubeApiKey: YOUTUBE_KEY,
    enableYtdlp: false,   // bash answers below disable yt-dlp (no consent prompt)
    languages: 'de,en,fr',
    maxChars: '120000',
    tunnelId: TUNNEL_ID,
    runtimeApiKey: RUNTIME_KEY,
    httpProxy: 'http://proxy:3128',
  };
  const js = gen.buildYaml(input);

  // Generate the same stack with the Bash generator (answers in prompt order).
  const bashAnswers = [
    '0.9.9', '0.9.8', YOUTUBE_KEY, 'y', 'n', 'de,en,fr', '120000',
    TUNNEL_ID, RUNTIME_KEY, 'y', 'http://proxy:3128', 'y',
  ].join('\n') + '\n';
  const out = path.join(fs.mkdtempSync(path.join(require('os').tmpdir(), 'webgen-')), 'stack.yml');
  execFileSync('bash', [path.join(REPO, 'generators',
    'generate_docker-compose_for_ChatGPT_MCP.sh'), '--output', out],
    { input: bashAnswers, encoding: 'utf8' });
  const bash = fs.readFileSync(out, 'utf8');

  // Semantic comparison on parsed YAML (PyYAML is the repo's canonical parser;
  // both YAML documents are passed as files to avoid argv length limits).
  const jsFile = out + '.web.yml';
  fs.writeFileSync(jsFile, js);
  const py = `
import json, sys, yaml
js = yaml.safe_load(open(sys.argv[1]))
bash = yaml.safe_load(open(sys.argv[2]))
def norm(doc):
    tun = doc["services"]["openai-tunnel"]
    tun["depends_on"] = json.dumps(tun.get("depends_on"), sort_keys=True)
    for svc in doc["services"].values():
        svc.pop("image", None)
    return {
        "service_names": sorted(doc["services"]),
        "mcp_env": doc["services"]["youtube-mcp"]["environment"],
        "tun_env": doc["services"]["openai-tunnel"]["environment"],
        "tun_depends": doc["services"]["openai-tunnel"]["depends_on"],
        "tun_read_only": doc["services"]["openai-tunnel"].get("read_only"),
        "tun_tmpfs": doc["services"]["openai-tunnel"].get("tmpfs"),
        "tun_cap_drop": doc["services"]["openai-tunnel"].get("cap_drop"),
        "tun_sec": doc["services"]["openai-tunnel"].get("security_opt"),
        "tun_restart": doc["services"]["openai-tunnel"].get("restart"),
        "mcp_restart": doc["services"]["youtube-mcp"].get("restart"),
        "networks": doc.get("networks"),
        "tun_networks": doc["services"]["openai-tunnel"].get("networks"),
    }
print(json.dumps({"equal": norm(js) == norm(bash)}))
`;
  const result = spawnSync('python3', ['-c', py, jsFile, out], { encoding: 'utf8' });
  assert.strictEqual(result.status, 0, result.stderr);
  assert.strictEqual(JSON.parse(result.stdout).equal, true,
    'web and bash YAML differ semantically');
});

test('generated YAML contains exact confirmed secret values', () => {
  const input = {
    mcpTag: 'latest', tunnelTag: '0.1.0',
    youtubeApiKey: YOUTUBE_KEY, enableYtdlp: true,
    languages: 'de,en', maxChars: '60000',
    tunnelId: TUNNEL_ID, runtimeApiKey: RUNTIME_KEY, httpProxy: '',
  };
  const yaml = gen.buildYaml(input);
  assert.ok(yaml.includes(`YOUTUBE_API_KEY: '${YOUTUBE_KEY}'`));
  assert.ok(yaml.includes(`CONTROL_PLANE_API_KEY: '${RUNTIME_KEY}'`));
});

test('proxy omitted when empty; NO_PROXY set when present', () => {
  const base = {
    mcpTag: 'latest', tunnelTag: '0.1.0', enableYtdlp: false,
    languages: 'de,en', maxChars: '60000', tunnelId: TUNNEL_ID,
    runtimeApiKey: RUNTIME_KEY,
  };
  const without = gen.buildYaml({ ...base, youtubeApiKey: '', httpProxy: '' });
  assert.ok(!without.includes('HTTPS_PROXY'));
  const withProxy = gen.buildYaml({ ...base, youtubeApiKey: '', httpProxy: 'http://proxy:3128' });
  assert.ok(withProxy.includes("HTTPS_PROXY: 'http://proxy:3128'"));
  assert.ok(withProxy.includes("NO_PROXY: 'youtube-mcp,localhost,127.0.0.1'"));
});

test('no host ports and no tunnel-config volume', () => {
  const yaml = gen.buildYaml({
    mcpTag: 'latest', tunnelTag: '0.1.0', youtubeApiKey: '',
    enableYtdlp: false, languages: 'de,en', maxChars: '60000',
    tunnelId: TUNNEL_ID, runtimeApiKey: RUNTIME_KEY, httpProxy: '',
  });
  assert.ok(!yaml.includes('ports:'));
  assert.ok(!yaml.includes('tunnel-config'));
  assert.ok(yaml.includes('        condition: service_healthy'));
});

test('yt-dlp disabled path omits nothing and sets false', () => {
  const yaml = gen.buildYaml({
    mcpTag: 'latest', tunnelTag: '0.1.0', youtubeApiKey: '',
    enableYtdlp: false, languages: 'de,en', maxChars: '60000',
    tunnelId: TUNNEL_ID, runtimeApiKey: RUNTIME_KEY, httpProxy: '',
  });
  assert.ok(yaml.includes("YOUTUBE_ENABLE_YTDLP: 'false'"));
  const yaml2 = gen.buildYaml({
    mcpTag: 'latest', tunnelTag: '0.1.0', youtubeApiKey: '',
    enableYtdlp: true, languages: 'de,en', maxChars: '60000',
    tunnelId: TUNNEL_ID, runtimeApiKey: RUNTIME_KEY, httpProxy: '',
  });
  assert.ok(yaml2.includes("YOUTUBE_ENABLE_YTDLP: 'true'"));
});

/* ------------------------------------------------- static security scan */

test('web sources contain no network/storage/telemetry primitives', () => {
  const files = [
    path.join(REPO, 'docs', 'index.html'),
    path.join(REPO, 'docs', 'assets', 'generator.js'),
    path.join(REPO, 'docs', 'assets', 'styles.css'),
  ];
  // Occurrences inside comments that describe the *prohibition* are fine;
  // we scan for actual API usage patterns with word boundaries.
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
    // Strip comments (// and /* */ for js, <!-- --> for html/css) to avoid
    // flagging the prohibition documentation itself.
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

test('web sources contain no external URLs that would load at runtime', () => {
  const html = fs.readFileSync(path.join(REPO, 'docs', 'index.html'), 'utf8');
  // Only https? src/href references to github.com docs pages are allowed
  // (navigational links); no CDN/jsdelivr/unpkg/google fonts/anything fetched.
  const srcs = [...html.matchAll(/(?:src|href)=["']([^"']+)["']/g)].map(m => m[1]);
  for (const src of srcs) {
    if (/^https?:/.test(src)) {
      assert.ok(src.startsWith('https://github.com/Dracoform/chatgpt-youtube-mcp') ||
                src.startsWith('https://platform.openai.com/'),
        `unexpected external reference: ${src}`);
    }
    // Local asset references must resolve to real files in docs/.
    if (src.startsWith('assets/') || src.endsWith('.md')) {
      const target = path.join(REPO, 'docs', src.split('#')[0]);
      assert.ok(fs.existsSync(target), `missing local reference target: ${src}`);
    }
  }
  const js = fs.readFileSync(path.join(REPO, 'docs', 'assets', 'generator.js'), 'utf8');
  // The only URL literal allowed is the internal MCP_SERVER_URL target.
  const urls = js.match(/https?:\/\/[^'"\s]*/g) || [];
  for (const u of urls) {
    assert.ok(u.startsWith('http://youtube-mcp:'),
      `generator.js contains an unexpected URL: ${u}`);
  }
});

test('branding icon is a valid unmodified PNG (skipped until the file is provided)', () => {
  const icon = path.join(REPO, 'docs', 'assets', 'youtube-mcp-icon.png');
  if (!fs.existsSync(icon)) {
    console.log('     SKIP: docs/assets/youtube-mcp-icon.png not yet provided');
    return;
  }
  const buf = fs.readFileSync(icon);
  // PNG magic + IHDR dimensions must be 64x64 and the file must not be empty.
  assert.ok(buf.length > 33, 'icon file too small');
  assert.ok(buf.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])),
    'icon is not a PNG');
  assert.strictEqual(buf.readUInt32BE(16), 64, 'icon width must be 64');
  assert.strictEqual(buf.readUInt32BE(20), 64, 'icon height must be 64');
});

test('CSP is present and strict', () => {
  const html = fs.readFileSync(path.join(REPO, 'docs', 'index.html'), 'utf8');
  for (const directive of [
    "default-src 'self'", "connect-src 'none'", "script-src 'self'",
    "style-src 'self'", "object-src 'none'", "base-uri 'none'", "form-action 'none'",
  ]) {
    assert.ok(html.includes(directive), `CSP missing: ${directive}`);
  }
});

test('secret-form inputs disable autocomplete and spellcheck', () => {
  const html = fs.readFileSync(path.join(REPO, 'docs', 'index.html'), 'utf8');
  for (const id of ['youtube_api_key', 'runtime_api_key']) {
    const tag = html.slice(html.indexOf(`id="${id}"`) - 200,
                           html.indexOf(`id="${id}"`) + 400);
    assert.ok(tag.includes('autocomplete="off"'), `${id}: autocomplete`);
    assert.ok(tag.includes('spellcheck="false"'), `${id}: spellcheck`);
  }
});

/* ----------------------------------------------------------------- end */

console.log(`\n${passed} passed, ${failures.length} failed`);
if (failures.length) {
  console.error('failed tests: ' + failures.join(', '));
  process.exit(1);
}
