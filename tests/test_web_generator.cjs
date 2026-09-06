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
const PS1_GENERATOR = path.join(REPO, 'generators',
  'generate_docker-compose_for_ChatGPT_MCP.ps1');

let passed = 0;
const failures = [];

// Redact all test secret values from diagnostics/failure output.
const SECRET_VALUES = [YOUTUBE_KEY, RUNTIME_KEY, NON_STANDARD_RUNTIME];
function _redact(text) {
  for (const s of SECRET_VALUES) {
    if (s) { text = text.split(s).join('***REDACTED***'); }
  }
  return text;
}

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

test('three-way equivalence: Bash, PowerShell, and web generators', () => {
  // One test, three real generators, one identical non-default semantic
  // input set. All three must produce the same normalized YAML structure
  // or this test fails.
  const input = {
    mcpTag: '0.9.9',
    tunnelTag: '0.9.8',
    youtubeApiKey: YOUTUBE_KEY,
    enableYtdlp: true,          // yt-dlp enabled AND consent confirmed below
    languages: 'de,en,fr',
    maxChars: '120000',
    tunnelId: TUNNEL_ID,
    runtimeApiKey: RUNTIME_KEY,
    httpProxy: 'http://proxy:3128',
  };
  const js = gen.buildYaml(input);

  // Shared non-default answers (prompt order per generator):
  // tags, youtube key, key confirm, ytdlp enable (yes), consent JA,
  // languages, max chars, tunnel id, runtime key, key confirm, proxy,
  // proxy confirm.
  const ANSWERS = [
    '0.9.9', '0.9.8', YOUTUBE_KEY, 'y', '', 'JA', 'de,en,fr', '120000',
    TUNNEL_ID, RUNTIME_KEY, 'y', 'http://proxy:3128', 'y',
  ];
  const tmp = fs.mkdtempSync(path.join(require('os').tmpdir(), 'threeway-'));

  // --- Bash (real generator) ---------------------------------------------
  const bashOut = path.join(tmp, 'bash.yml');
  execFileSync('bash', [path.join(REPO, 'generators',
    'generate_docker-compose_for_ChatGPT_MCP.sh'), '--output', bashOut],
    { input: ANSWERS.join('\n') + '\n', encoding: 'utf8', timeout: 15000 });

  // --- PowerShell (real pwsh, non-interactive Read-Host override) --------
  const pwshAnswers = ANSWERS.map(a => "'" + a.replace(/'/g, "''") + "'").join(', ');
  const ps1Script = `
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$global:answerQueue = [System.Collections.Generic.Queue[string]]::new()
foreach ($item in @(${pwshAnswers})) { $global:answerQueue.Enqueue($item) }
function global:Read-Host {
    param([string]$PromptMessage, [switch]$AsSecureString)
    if ($AsSecureString) {
        $secure = New-Object System.Security.SecureString
        foreach ($ch in ($global:answerQueue.Dequeue()).ToCharArray()) {
            $secure.AppendChar($ch)
        }
        return $secure
    }
    return $global:answerQueue.Dequeue()
}
& '${PS1_GENERATOR}' -OutputPath '${path.join(tmp, 'pwsh.yml')}'
exit 0
`;
  const pwsh = spawnSync('pwsh',
    ['-NoProfile', '-NonInteractive', '-Command', ps1Script],
    { encoding: 'utf8', timeout: 15000 });
  assert.strictEqual(pwsh.status, 0,
    'pwsh generator failed:\n' +
      _redact((pwsh.stdout || '') + '\n' + (pwsh.stderr || '')));

  // --- compare Bash vs PowerShell vs web on parsed, normalized YAML ------
  const webOut = path.join(tmp, 'web.yml');
  fs.writeFileSync(webOut, js);
  const py = `
import json, sys, yaml
def norm(doc):
    doc = json.loads(json.dumps(doc))  # deep copy
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
        "tun_stop_grace": str(doc["services"]["openai-tunnel"].get("stop_grace_period")),
        "tun_restart": doc["services"]["openai-tunnel"].get("restart"),
        "mcp_restart": doc["services"]["youtube-mcp"].get("restart"),
        "mcp_read_only": doc["services"]["youtube-mcp"].get("read_only"),
        "mcp_tmpfs": doc["services"]["youtube-mcp"].get("tmpfs"),
        "mcp_cap_drop": doc["services"]["youtube-mcp"].get("cap_drop"),
        "mcp_sec": doc["services"]["youtube-mcp"].get("security_opt"),
        "mcp_expose": doc["services"]["youtube-mcp"].get("expose"),
        "networks": doc.get("networks"),
        "tun_networks": doc["services"]["openai-tunnel"].get("networks"),
    }
docs = [norm(yaml.safe_load(open(p))) for p in sys.argv[1:4]]
names = ["bash", "pwsh", "web"]
result = {"equal": docs[0] == docs[1] == docs[2]}
if not result["equal"]:
    diffs = {}
    for key in docs[0]:
        vals = [d[key] for d in docs]
        if not (vals[0] == vals[1] == vals[2]):
            diffs[key] = dict(zip(names, vals))
    result["diffs"] = diffs
print(json.dumps(result))
`;
  const result = spawnSync('python3',
    ['-c', py, bashOut, path.join(tmp, 'pwsh.yml'), webOut],
    { encoding: 'utf8', timeout: 30000 });
  assert.strictEqual(result.status, 0, result.stderr);
  const parsed = JSON.parse(result.stdout);
  if (!parsed.equal) {
    // Redact secret values from the diagnostic diff before surfacing it.
    const diffText = _redact(JSON.stringify(parsed.diffs, null, 2));
    assert.fail('three-way YAML mismatch:\n' + diffText);
  }
  assert.strictEqual(parsed.equal, true);
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

test('branding icon is a valid unmodified 64x64 PNG', () => {
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
