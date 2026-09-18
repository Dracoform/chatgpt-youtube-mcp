'use strict';
/*
 * Client-side Portainer stack generator for chatgpt-youtube-mcp (Stage 4,
 * capability-oriented model).
 *
 * Privacy contract: everything runs in this page. No fetch, no XHR, no
 * WebSocket, no storage APIs, no cookies, no URL parameters for values,
 * no telemetry. Secrets and the generated YAML exist only in page memory
 * and the visible DOM fields. See the CSP in index.html.
 *
 * The generated YAML is byte-for-byte equivalent to
 * generators/canonical_model.py:render() (the single source of truth) for
 * every valid canonical input; equivalence with the Bash/PowerShell
 * generators is enforced by tests/test_generator_equivalence.py.
 */

// Fixed images (parity with the Bash/PowerShell generators).
var MCP_IMAGE_BASE = 'ghcr.io/dracoform/chatgpt-youtube-mcp';
var TUNNEL_IMAGE_BASE = 'ghcr.io/dracoform/openai-mcp-tunnel';

// Current Google API key format: AIza + 35 chars from [A-Za-z0-9_-].
var GOOGLE_KEY_PATTERN = /^AIza[A-Za-z0-9_-]{35}$/;
// OpenAI secret keys currently start with sk- (sk-proj-, sk-svcacct-, ...).
var OPENAI_KEY_PREFIX = /^sk-/;
var TUNNEL_ID_PATTERN = /^tunnel_[A-Za-z0-9_-]+$/;
var LANGUAGES_PATTERN = /^[A-Za-z0-9_-]+(,[A-Za-z0-9_-]+)*$/;

// Canonical access methods (order matters for the assembled model).
var ACCESS_METHODS = ['local', 'tunnel', 'static', 'oauth'];

/* ------------------------------------- canonical model port ----------- */
/* Direct port of generators/canonical_model.py: normalize() + validate().
 * The validation error messages are kept byte-identical to the Python
 * ValueError texts so all three generators reject the same combinations. */

var MCP_TAG_DEFAULT = 'latest';
var TUNNEL_TAG_DEFAULT = '0.1.0';

function defaultModel() {
  return {
    mcp: { tag: MCP_TAG_DEFAULT, youtube_api_key: '', enable_ytdlp: true,
           languages: 'de,en', max_chars: '60000' },
    access: ['tunnel'],
    tunnel: { tag: TUNNEL_TAG_DEFAULT, tunnel_id: '', runtime_api_key: '',
              http_proxy: '' },
    edge: {
      enabled: false,
      auth_modes: [],
      tls: 'ingress',
      public_port: '8443',
      cert_file: '',
      key_file: '',
      static_token_prefix: 'ytsk_',
      static_tokens: [],
      oauth: { issuer: '', resource: '', audience: '', required_scope: '',
               jwks_url: '', authorization_servers: '', scopes_supported: '' },
    },
  };
}

function normalizeModel(model) {
  // Fill unset/default fields and coerce types; does not mutate input.
  var def = defaultModel();
  var out = JSON.parse(JSON.stringify(model || {}));
  var mcp = {};
  Object.keys(def.mcp).forEach(function (k) { mcp[k] = def.mcp[k]; });
  if (out.mcp) {
    Object.keys(out.mcp).forEach(function (k) { mcp[k] = out.mcp[k]; });
  }
  out.mcp = mcp;

  out.access = (out.access || []).slice();

  var tun = {};
  Object.keys(def.tunnel).forEach(function (k) { tun[k] = def.tunnel[k]; });
  if (out.tunnel) {
    Object.keys(out.tunnel).forEach(function (k) { tun[k] = out.tunnel[k]; });
  }
  out.tunnel = tun;

  var edge = JSON.parse(JSON.stringify(def.edge));
  var given = out.edge || {};
  ['tls', 'public_port', 'static_token_prefix', 'cert_file', 'key_file']
    .forEach(function (k) {
      if (given.hasOwnProperty(k)) { edge[k] = given[k]; }
    });
  edge.auth_modes = (given.auth_modes || []).slice();
  edge.static_tokens = (given.static_tokens || []).slice();
  var oauth = {};
  Object.keys(def.edge.oauth).forEach(function (k) { oauth[k] = def.edge.oauth[k]; });
  if (given.oauth) {
    Object.keys(given.oauth).forEach(function (k) { oauth[k] = given.oauth[k]; });
  }
  edge.oauth = oauth;
  out.edge = edge;

  // Derived: the public edge exists iff static or oauth access is enabled.
  out.edge.enabled =
    (out.access.indexOf('static') !== -1) || (out.access.indexOf('oauth') !== -1);
  return out;
}

function validateModel(model) {
  // Validate a canonical input; returns the normalized model or throws an
  // Error with the exact message canonical_model.py raises.
  var m = normalizeModel(model);
  var access = m.access;
  var unknown = access.filter(function (a) { return ACCESS_METHODS.indexOf(a) === -1; });
  if (unknown.length) {
    throw new Error('Unknown access method(s): ' + unknown.join(', '));
  }
  if (!access.length) {
    throw new Error('At least one access method must be selected.');
  }

  var staticOn = access.indexOf('static') !== -1;
  var oauthOn = access.indexOf('oauth') !== -1;
  var edge = m.edge;

  if (edge.enabled !== (staticOn || oauthOn)) {
    throw new Error('edge.enabled must match the presence of static/oauth access.');
  }
  if (edge.enabled && !edge.auth_modes.length) {
    throw new Error('A public edge requires at least one auth mode (static and/or oauth).');
  }
  if (edge.enabled &&
      ((staticOn !== (edge.auth_modes.indexOf('static') !== -1)) ||
       (oauthOn !== (edge.auth_modes.indexOf('oauth') !== -1)))) {
    throw new Error('edge.auth_modes must equal access \u2229 {static, oauth}.');
  }

  // OAuth fail-closed.
  if (oauthOn) {
    var o = edge.oauth;
    if (!o.issuer || !o.resource) {
      throw new Error('OAuth access requires edge.oauth.issuer and edge.oauth.resource.');
    }
  }
  if (!oauthOn) {
    var o2 = edge.oauth;
    if (o2.issuer || o2.resource) {
      throw new Error('OAuth issuer/resource set but OAuth is not an enabled access method.');
    }
  }

  // TLS: edge-terminated requires BOTH cert+key; ingress requires neither.
  var tls = edge.tls;
  var cert = edge.cert_file || '';
  var key = edge.key_file || '';
  if (tls === 'edge') {
    if (!!cert !== !!key) {
      throw new Error("TLS mode 'edge' requires BOTH edge.cert_file and edge.key_file.");
    }
  } else if (tls === 'ingress') {
    if (cert || key) {
      throw new Error("TLS mode 'ingress' must not set edge.cert_file/key_file (external TLS).");
    }
  } else {
    throw new Error("edge.tls must be 'edge' or 'ingress'; got '" + tls + "'");
  }

  // static+oauth namespace ambiguity: every static token must carry the
  // prefix when OAuth is also enabled.
  if (staticOn && oauthOn) {
    var prefix = edge.static_token_prefix || 'ytsk_';
    for (var i = 0; i < edge.static_tokens.length; i++) {
      var tok = edge.static_tokens[i];
      if (tok.slice(0, prefix.length) !== prefix) {
        throw new Error(
          'Static token ' + JSON.stringify(tok) + ' does not begin with the reserved ' +
          'static namespace prefix \'' + prefix + '\'; with static+OAuth on one edge ' +
          'this would be ambiguous (rejected).');
      }
    }
  }

  // Never a public no-auth edge.
  if (edge.enabled && staticOn && !edge.static_tokens.length) {
    throw new Error('Static access requires at least one static token.');
  }
  return m;
}

/* ---------------------------------------------------------- masking ---- */

// Length-preserving mask: every character X except the final four; values
// of four characters or fewer are fully masked.
function maskSecret(value) {
  var length = value.length;
  if (length <= 4) {
    return new Array(length + 1).join('X');
  }
  // Array(n + 1).join('X') yields exactly n 'X' characters.
  return new Array(length - 4 + 1).join('X') + value.slice(-4);
}

// Reject control characters (CR, LF, NUL, C0/C1) and edge whitespace.
function secretHasControlCharsOrEdgeWhitespace(value) {
  if (value !== value.trim()) { return true; }
  for (var i = 0; i < value.length; i++) {
    var code = value.charCodeAt(i);
    if (code < 32 || (code >= 127 && code <= 159)) { return true; }
  }
  return false;
}

/* ------------------------------------------------------------ YAML ----- */

function yamlQuote(value) {
  // Single-quote a string, escaping ' as ''.
  return "'" + String(value).replace(/'/g, "''") + "'";
}

// Portable scalar quoting (port of canonical_model._yaml_scalar): booleans
// become the quoted strings 'true'/'false', numbers are literal, strings
// are single-quoted with '' escaping.
function yamlScalar(value) {
  if (typeof value === 'boolean') { return value ? "'true'" : "'false'"; }
  if (typeof value === 'number') { return String(value); }
  return "'" + String(value).replace(/'/g, "''") + "'";
}

// Byte-for-byte port of canonical_model.render(): takes a canonical input
// object (same shape as the fixture JSON), normalizes + validates it and
// returns the exact Compose YAML string. Throws on invalid combinations.
function buildYaml(input) {
  var m = validateModel(input);
  var lines = [];
  lines.push('# Generated by the chatgpt-youtube-mcp multi-client generator. Contains secrets; restrict access.');
  lines.push('services:');
  var mcp = m.mcp;
  var access = m.access;
  lines.push('  youtube-mcp:');
  lines.push('    image: ' + yamlScalar(MCP_IMAGE_BASE + ':' + mcp.tag));
  lines.push('    restart: unless-stopped');
  lines.push('    environment:');
  lines.push("      MCP_TRANSPORT: 'streamable-http'");
  lines.push("      MCP_HOST: '0.0.0.0'");
  lines.push("      MCP_PORT: '8765'");
  lines.push('      YOUTUBE_API_KEY: ' + yamlScalar(mcp.youtube_api_key || ''));
  lines.push('      YOUTUBE_ENABLE_YTDLP: ' + yamlScalar(
    mcp.enable_ytdlp ? 'true' : 'false'));
  lines.push('      YOUTUBE_TRANSCRIPT_MAX_CHARS: ' + yamlScalar(mcp.max_chars || '60000'));
  lines.push('      YOUTUBE_DEFAULT_LANGUAGES: ' + yamlScalar(mcp.languages || 'de,en'));
  // Local access: the core publishes a LOOPBACK host port so host clients can
  // reach it. It is NEVER published to a public interface.
  if (access.indexOf('local') !== -1) {
    lines.push('    ports:');
    lines.push("      - '127.0.0.1:8765:8765'");
  }
  lines.push('    expose:');
  lines.push("      - '8765'");
  lines.push('    read_only: true');
  lines.push('    tmpfs:');
  lines.push('      - /tmp:size=64m');
  lines.push('    cap_drop:');
  lines.push('      - ALL');
  lines.push('    security_opt:');
  lines.push('      - no-new-privileges:true');
  lines.push('    networks:');
  lines.push('      - youtube-mcp-internal');
  lines.push('');

  var tunnelOn = access.indexOf('tunnel') !== -1;
  if (tunnelOn) {
    var tun = m.tunnel;
    lines.push('  openai-tunnel:');
    lines.push('    image: ' + yamlScalar(TUNNEL_IMAGE_BASE + ':' + tun.tag));
    lines.push('    restart: unless-stopped');
    lines.push('    environment:');
    lines.push('      CONTROL_PLANE_TUNNEL_ID: ' + yamlScalar(tun.tunnel_id || ''));
    lines.push('      CONTROL_PLANE_API_KEY: ' + yamlScalar(tun.runtime_api_key || ''));
    lines.push("      MCP_SERVER_URL: 'http://youtube-mcp:8765/mcp'");
    var proxy = tun.http_proxy || '';
    if (proxy) {
      lines.push('      HTTPS_PROXY: ' + yamlScalar(proxy));
      lines.push("      NO_PROXY: 'youtube-mcp,localhost,127.0.0.1'");
    }
    lines.push('    depends_on:');
    lines.push('      youtube-mcp:');
    lines.push('        condition: service_healthy');
    lines.push('    read_only: true');
    lines.push('    tmpfs:');
    lines.push('      - /tmp:size=16m');
    lines.push('    cap_drop:');
    lines.push('      - ALL');
    lines.push('    security_opt:');
    lines.push('      - no-new-privileges:true');
    lines.push('    stop_grace_period: 30s');
    lines.push('    networks:');
    lines.push('      - youtube-mcp-internal');
    lines.push('');
  }

  var edge = m.edge;
  if (edge.enabled) {
    lines.push('  youtube-mcp-edge:');
    lines.push('    image: ' + yamlScalar(MCP_IMAGE_BASE + ':' + mcp.tag));
    lines.push('    restart: unless-stopped');
    lines.push("    entrypoint: ['youtube-mcp-edge']");
    lines.push('    depends_on:');
    lines.push('      youtube-mcp:');
    lines.push('        condition: service_healthy');
    lines.push('    environment:');
    lines.push("      EDGE_MCP_UPSTREAM_URL: 'http://youtube-mcp:8765/mcp'");
    lines.push("      EDGE_HOST: '0.0.0.0'");
    lines.push("      EDGE_PORT: '8766'");
    var staticOnE = edge.auth_modes.indexOf('static') !== -1;
    var oauthOnE = edge.auth_modes.indexOf('oauth') !== -1;
    lines.push('      EDGE_STATIC_AUTH_ENABLED: ' + yamlScalar(staticOnE));
    if (staticOnE) {
      lines.push('      EDGE_STATIC_TOKENS: ' +
                 yamlScalar(edge.static_tokens.join(',')));
      lines.push('      EDGE_STATIC_TOKEN_PREFIX: ' +
                 yamlScalar(edge.static_token_prefix || 'ytsk_'));
    }
    lines.push('      EDGE_OAUTH_ENABLED: ' + yamlScalar(oauthOnE));
    if (oauthOnE) {
      var o = edge.oauth;
      lines.push('      EDGE_OAUTH_ISSUER: ' + yamlScalar(o.issuer || ''));
      lines.push('      EDGE_OAUTH_RESOURCE_IDENTIFIER: ' +
                 yamlScalar(o.resource || ''));
      if (o.audience) {
        lines.push('      EDGE_OAUTH_AUDIENCE: ' + yamlScalar(o.audience));
      }
      if (o.required_scope) {
        lines.push('      EDGE_OAUTH_REQUIRED_SCOPE: ' + yamlScalar(o.required_scope));
      }
      if (o.jwks_url) {
        lines.push('      EDGE_OAUTH_JWKS_URL: ' + yamlScalar(o.jwks_url));
      }
      if (o.authorization_servers) {
        lines.push('      EDGE_OAUTH_AUTHORIZATION_SERVERS: ' +
                   yamlScalar(o.authorization_servers));
      }
      if (o.scopes_supported) {
        lines.push('      EDGE_OAUTH_SCOPES_SUPPORTED: ' +
                   yamlScalar(o.scopes_supported));
      }
    }
    lines.push("      EDGE_MAX_REQUEST_BYTES: '2097152'");
    lines.push("      EDGE_UPSTREAM_TIMEOUT_SECONDS: '120'");
    var tls = edge.tls;
    if (tls === 'edge') {
      // NOTE: canonical_model.py writes cert/key with plain single quotes
      // (no '' escaping) — replicated exactly for byte parity.
      lines.push("      EDGE_TLS_CERT_FILE: '" + (edge.cert_file || '') + "'");
      lines.push("      EDGE_TLS_KEY_FILE: '" + (edge.key_file || '') + "'");
      // Mount an operator-provided cert dir read-only (byte-parity with the
      // canonical model). Host ./edge-certs must hold tls.crt + tls.key.
      lines.push('    volumes:');
      lines.push("      - './edge-certs:/certs:ro'");
    }
    lines.push('    read_only: true');
    lines.push('    tmpfs:');
    lines.push('      - /tmp:size=16m');
    lines.push('    cap_drop:');
    lines.push('      - ALL');
    lines.push('    security_opt:');
    lines.push('      - no-new-privileges:true');
    if (tls === 'edge') {
      // Edge-terminated TLS: publish the public port, loopback-gated by
      // default (operator widens to expose publicly).
      lines.push('    ports:');
      lines.push('      - ' + yamlScalar(
        '127.0.0.1:' + (edge.public_port || '8443') + ':8766'));
    }
    lines.push('    networks:');
    lines.push('      - youtube-mcp-internal');
    lines.push('');
  }

  lines.push('networks:');
  lines.push('  youtube-mcp-internal:');
  lines.push('    driver: bridge');
  return lines.join('\n') + '\n';
}

/* --------------------------------------------------------- review UI --- */

function $(id) { return document.getElementById(id); }

function showReview(reviewId, maskId, lengthId, value, label) {
  var review = $(reviewId);
  review.classList.remove('hidden');
  $(maskId).textContent = maskSecret(value);
  $(lengthId).textContent = String(value.length);
  return label + ' erhalten:\n' + maskSecret(value) + '\nLänge: ' + value.length + ' Zeichen';
}

function hideReview(reviewId) {
  $(reviewId).classList.add('hidden');
}

/* ----------------------------------------------------- secret state ---- */

// Confirmed secret values live only in these variables (page memory).
var confirmed = { youtubeApiKey: null, runtimeApiKey: null,
                  staticTokens: [], oauthIssuer: '', oauthResource: '' };

function confirmYouTubeKey(onDone) {
  var value = $('youtube_api_key').value;
  if (value === '') {
    showReview('yt-review', 'yt-mask', 'yt-length', '', '');
    if (window.confirm(
        'Kein YouTube-API-Schlüssel eingegeben.\n\n' +
        'Ohne YouTube-API-Schlüssel fortfahren?')) {
      confirmed.youtubeApiKey = '';
      hideReview('yt-review');
      onDone();
    } else {
      hideReview('yt-review');
      $('youtube_api_key').focus();
    }
    return;
  }
  if (secretHasControlCharsOrEdgeWhitespace(value)) {
    window.alert(
      'Der Wert enthält Steuerzeichen oder Leerzeichen am Anfang/Ende.\n' +
      'Bitte erneut eingeben.');
    hideReview('yt-review');
    $('youtube_api_key').focus();
    return;
  }
  showReview('yt-review', 'yt-mask', 'yt-length', value, 'YouTube-API-Schlüssel');
  var length = value.length;
  var messages = [
    'YouTube-API-Schlüssel erhalten:\n' + maskSecret(value) +
      '\nLänge: ' + length + ' Zeichen\n\nDiesen Wert verwenden?'
  ];
  if (!GOOGLE_KEY_PATTERN.test(value)) {
    messages.push(
      'Warnung: Dieser Wert entspricht nicht dem erwarteten Google-Key-Format ' +
      '(AIza + 35 Zeichen).\n\n' +
      'Mögliche Ursache: Der Schlüssel wurde doppelt eingefügt — Länge prüfen.');
  }
  if (window.confirm(messages.join('\n\n'))) {
    if (!GOOGLE_KEY_PATTERN.test(value)) {
      if (!window.confirm(
          'Trotzdem diesen nicht-standardisierten Wert verwenden?\n' +
          '(Nur bestätigen, wenn das Format des Schlüssels bekannt ist.)')) {
        $('youtube_api_key').focus();
        return;
      }
    }
    confirmed.youtubeApiKey = value;
    hideReview('yt-review');
    onDone();
  } else {
    hideReview('yt-review');
    $('youtube_api_key').focus();
  }
}

function confirmRuntimeKey(onDone) {
  var value = $('runtime_api_key').value;
  if (value === '' || secretHasControlCharsOrEdgeWhitespace(value)) {
    window.alert(
      value === ''
        ? 'Ein OpenAI-Runtime-API-Schlüssel ist erforderlich.'
        : 'Der Wert enthält Steuerzeichen oder Leerzeichen am Anfang/Ende.\n' +
          'Bitte erneut eingeben.');
    hideReview('rt-review');
    $('runtime_api_key').focus();
    return;
  }
  showReview('rt-review', 'rt-mask', 'rt-length', value, 'OpenAI-Runtime-API-Schlüssel');
  var messages = [
    'OpenAI-Runtime-API-Schlüssel erhalten:\n' + maskSecret(value) +
      '\nLänge: ' + value.length + ' Zeichen\n\nDiesen Wert verwenden?'
  ];
  if (!OPENAI_KEY_PREFIX.test(value)) {
    messages.push(
      'Warnung: Dieser Wert beginnt nicht mit einem bekannten OpenAI-Key-Präfix (sk-).');
  }
  if (window.confirm(messages.join('\n\n'))) {
    if (!OPENAI_KEY_PREFIX.test(value)) {
      if (!window.confirm(
          'Trotzdem diesen Wert ohne bekanntes Präfix verwenden?\n' +
          '(Nur bestätigen, wenn das Format des Schlüssels bekannt ist.)')) {
        $('runtime_api_key').focus();
        return;
      }
    }
    confirmed.runtimeApiKey = value;
    hideReview('rt-review');
    onDone();
  } else {
    hideReview('rt-review');
    $('runtime_api_key').focus();
  }
}

// Static tokens are entered comma-separated in one field.
function parseStaticTokens(raw) {
  return String(raw || '').split(',').map(function (t) { return t.trim(); })
    .filter(function (t) { return t !== ''; });
}

function confirmStaticTokens(onDone) {
  var tokens = parseStaticTokens($('static_tokens').value);
  if (tokens.length === 0) {
    window.alert('Mindestens ein statisches Token ist erforderlich.');
    hideReview('st-review');
    $('static_tokens').focus();
    return;
  }
  for (var i = 0; i < tokens.length; i++) {
    if (secretHasControlCharsOrEdgeWhitespace(tokens[i])) {
      window.alert(
        'Ein Token enthält Steuerzeichen oder Leerzeichen am Anfang/Ende.\n' +
        'Bitte erneut eingeben.');
      hideReview('st-review');
      $('static_tokens').focus();
      return;
    }
  }
  var review = $('st-review');
  review.classList.remove('hidden');
  $('st-mask').textContent = tokens.map(maskSecret).join('\n');
  $('st-length').textContent = String(tokens.length) + ' Token';
  if (window.confirm(
      tokens.length + ' statische Tokens erhalten:\n' +
      tokens.map(maskSecret).join('\n') + '\n\nDiese Token verwenden?')) {
    confirmed.staticTokens = tokens.slice();
    hideReview('st-review');
    onDone();
  } else {
    hideReview('st-review');
    $('static_tokens').focus();
  }
}

function confirmOAuthConfig(onDone) {
  var issuer = $('oauth_issuer').value.trim();
  var resource = $('oauth_resource').value.trim();
  if (!issuer || !resource) {
    window.alert('OAuth-Issuer und -Resource sind erforderlich.');
    hideReview('oa-review');
    $('oauth_issuer').focus();
    return;
  }
  var review = $('oa-review');
  review.classList.remove('hidden');
  $('oa-mask').textContent = maskSecret(issuer) + '\n' + maskSecret(resource);
  $('oa-length').textContent = 'Issuer: ' + issuer.length + ', Resource: ' + resource.length;
  if (window.confirm(
      'OAuth-Konfiguration erhalten:\n' +
      'Issuer: ' + maskSecret(issuer) + '\nResource: ' + maskSecret(resource) +
      '\n\nDiese Werte verwenden?')) {
    confirmed.oauthIssuer = issuer;
    confirmed.oauthResource = resource;
    hideReview('oa-review');
    onDone();
  } else {
    hideReview('oa-review');
    $('oauth_issuer').focus();
  }
}

/* -------------------------------------------------------- main flow ---- */

function showError(message) {
  var el = $('form-error');
  el.textContent = message;
  el.classList.remove('hidden');
}

function clearError() {
  var el = $('form-error');
  el.textContent = '';
  el.classList.add('hidden');
}

// Read the form into a canonical model object (secrets excluded — they are
// merged in from `confirmed` after the confirmation dialogs).
function assembleModel() {
  var access = [];
  if ($('access_local').checked) { access.push('local'); }
  if ($('access_tunnel').checked) { access.push('tunnel'); }
  if ($('access_static').checked) { access.push('static'); }
  if ($('access_oauth').checked) { access.push('oauth'); }
  return {
    mcp: {
      tag: $('mcp_tag').value.trim(),
      youtube_api_key: '',
      enable_ytdlp: $('enable_ytdlp_true').checked,
      languages: $('languages').value.trim(),
      max_chars: $('max_chars').value.trim(),
    },
    access: access,
    tunnel: {
      tag: $('tunnel_tag').value.trim(),
      tunnel_id: $('tunnel_id').value.trim(),
      runtime_api_key: '',
      http_proxy: $('http_proxy').value.trim(),
    },
    edge: {
      // Derived exactly like canonical_model.validate(): auth_modes =
      // access ∩ {static, oauth}. (normalize() keeps input values; the form
      // must supply a consistent set.)
      auth_modes: ['static', 'oauth'].filter(function (m) { return access.indexOf(m) !== -1; }),
      tls: $('tls_edge').checked ? 'edge' : 'ingress',
      public_port: $('public_port').value.trim(),
      cert_file: $('cert_file').value.trim(),
      key_file: $('key_file').value.trim(),
      static_token_prefix: $('static_token_prefix').value.trim(),
      static_tokens: parseStaticTokens($('static_tokens').value),
      oauth: {
        issuer: $('oauth_issuer').value.trim(),
        resource: $('oauth_resource').value.trim(),
        audience: $('oauth_audience').value.trim(),
        required_scope: $('oauth_required_scope').value.trim(),
        jwks_url: $('oauth_jwks_url').value.trim(),
        authorization_servers: $('oauth_authorization_servers').value.trim(),
        scopes_supported: $('oauth_scopes_supported').value.trim(),
      },
    },
  };
}

function generateFromConfirmed() {
  clearError();
  var consent = $('ytdlp_consent').value.trim().toUpperCase();
  if ($('enable_ytdlp_true').checked) {
    if (consent === '') {
      showError('yt-dlp ist aktiviert: Die Zustimmung (JA eingeben) ist erforderlich.');
      $('ytdlp_consent').focus();
      return;
    }
    if (consent !== 'JA') {
      showError('Die Zustimmung muss exakt JA sein, um den inoffiziellen yt-dlp-Fallback zu aktivieren.');
      $('ytdlp_consent').focus();
      return;
    }
  }

  var model = assembleModel();
  model.mcp.youtube_api_key = confirmed.youtubeApiKey || '';
  if (model.access.indexOf('tunnel') !== -1) {
    model.tunnel.runtime_api_key = confirmed.runtimeApiKey || '';
  }
  if (model.access.indexOf('static') !== -1) {
    model.edge.static_tokens = confirmed.staticTokens.slice();
  }
  if (model.access.indexOf('oauth') !== -1) {
    model.edge.oauth.issuer = confirmed.oauthIssuer;
    model.edge.oauth.resource = confirmed.oauthResource;
  }

  var yaml;
  try {
    yaml = buildYaml(model); // re-validates the final, confirmed model
  } catch (err) {
    showError(String(err && err.message || err));
    return;
  }

  var output = $('yaml-output');
  output.value = yaml;
  $('output').classList.remove('hidden');
  $('btn-copy').disabled = false;
  $('btn-select').disabled = false;
  $('output').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function onGenerate() {
  clearError();
  var mcpTag = $('mcp_tag').value.trim();
  if (!mcpTag) {
    showError('Das Image-Tag des YouTube-MCP-Servers ist erforderlich.');
    $('mcp_tag').focus();
    return;
  }

  var languages = $('languages').value.trim();
  if (!LANGUAGES_PATTERN.test(languages)) {
    showError('Ungültige Sprachliste. Beispiel: de,en');
    $('languages').focus();
    return;
  }

  var maxChars = $('max_chars').value.trim();
  if (!/^[0-9]+$/.test(maxChars) || +maxChars < 1000 || +maxChars > 500000) {
    showError('Das Transkript-Limit muss eine Zahl zwischen 1000 und 500000 sein.');
    $('max_chars').focus();
    return;
  }

  var tunnelOn = $('access_tunnel').checked;
  if (tunnelOn) {
    var tunnelTag = $('tunnel_tag').value.trim();
    if (!tunnelTag) {
      showError('Das Tunnel-Image-Tag ist erforderlich.');
      $('tunnel_tag').focus();
      return;
    }
    var tunnelId = $('tunnel_id').value.trim();
    if (!TUNNEL_ID_PATTERN.test(tunnelId)) {
      showError('Die Tunnel-ID muss mit tunnel_ beginnen (Beispiel: tunnel_0123456789abcdef).');
      $('tunnel_id').focus();
      return;
    }
  }

  // Canonical-model validation first (same rules as canonical_model.py);
  // only then ask for secret confirmations.
  var model = assembleModel();
  try {
    validateModel(model);
  } catch (err) {
    showError(String(err && err.message || err));
    return;
  }

  // Secret confirmations run as a chain in fixed order (tunnel runtime key,
  // static tokens, OAuth config) — each step must complete before the next.
  var steps = [];
  if (tunnelOn) { steps.push(confirmRuntimeKey); }
  if ($('access_static').checked) { steps.push(confirmStaticTokens); }
  if ($('access_oauth').checked) { steps.push(confirmOAuthConfig); }

  function runChain(idx, done) {
    if (idx >= steps.length) { done(); return; }
    steps[idx](function () { runChain(idx + 1, done); });
  }

  confirmYouTubeKey(function () { runChain(0, generateFromConfirmed); });
}

/* -------------------------------------------------------- clipboard ---- */

function onCopy() {
  var output = $('yaml-output');
  var status = $('copy-status');
  function report(ok, fallbackHint) {
    status.textContent = ok
      ? 'YAML kopiert. Eintragen in Portainer: Stacks -> Add stack -> Web editor.'
      : (fallbackHint ||
         'Kopieren fehlgeschlagen. YAML manuell markieren und mit Strg/Cmd+C kopieren.');
    status.classList.add('ok');
    status.classList.remove('hidden');
  }
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(output.value).then(
      function () { report(true); },
      function () { report(false); }
    );
  } else {
    // Legacy path without the async Clipboard API.
    output.focus();
    output.select();
    var ok = false;
    try { ok = document.execCommand('copy'); } catch (err) { ok = false; }
    report(ok);
  }
}

function onSelectAll() {
  var output = $('yaml-output');
  output.focus();
  output.select();
}

/* --------------------------------------------------------- clearing ---- */

function clearSensitiveData() {
  // Form fields: all password inputs are wiped.
  var fields = document.querySelectorAll(
    '#generator-form input[type="text"], #generator-form input[type="password"]'
  );
  for (var i = 0; i < fields.length; i++) {
    if (fields[i].type === 'password') {
      fields[i].value = '';
    }
  }
  // Restore visible defaults (non-secret fields).
  $('mcp_tag').value = 'latest';
  $('tunnel_tag').value = '0.1.0';
  $('languages').value = 'de,en';
  $('max_chars').value = '60000';
  $('tunnel_id').value = '';
  $('http_proxy').value = '';
  $('ytdlp_consent').value = '';
  $('enable_ytdlp_true').checked = true;
  $('enable_ytdlp_false').checked = false;
  // Capability selection and edge defaults.
  ['access_local', 'access_tunnel', 'access_static', 'access_oauth']
    .forEach(function (id) { $(id).checked = false; });
  $('tls_ingress').checked = true;
  $('tls_edge').checked = false;
  $('public_port').value = '8443';
  $('cert_file').value = '';
  $('key_file').value = '';
  $('static_token_prefix').value = 'ytsk_';
  ['oauth_issuer', 'oauth_resource', 'oauth_audience', 'oauth_required_scope',
   'oauth_jwks_url', 'oauth_authorization_servers', 'oauth_scopes_supported']
    .forEach(function (id) { $(id).value = ''; });
  updateConsentVisibility();
  updateConditionalVisibility();

  // Secret state and reviews.
  confirmed.youtubeApiKey = null;
  confirmed.runtimeApiKey = null;
  confirmed.staticTokens = [];
  confirmed.oauthIssuer = '';
  confirmed.oauthResource = '';
  hideReview('yt-review');
  hideReview('rt-review');
  hideReview('st-review');
  hideReview('oa-review');
  resetShowToggle('yt-show', 'youtube_api_key');
  resetShowToggle('rt-show', 'runtime_api_key');
  resetShowToggle('st-show', 'static_tokens');

  // Generated YAML and UI references.
  var output = $('yaml-output');
  output.value = '';
  $('btn-copy').disabled = true;
  $('btn-select').disabled = true;
  $('output').classList.add('hidden');
  $('copy-status').textContent = '';
  $('copy-status').classList.add('hidden');
  clearError();
}

function resetShowToggle(buttonId, inputId) {
  $(buttonId).textContent = 'Zeigen';
  $(buttonId).setAttribute('aria-pressed', 'false');
  $(inputId).type = 'password';
}

function startOver() {
  clearSensitiveData();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

/* ------------------------------------------------------ show/hide ------ */

function wireShowToggle(buttonId, inputId) {
  $(buttonId).addEventListener('click', function () {
    var input = $(inputId);
    var showing = input.type === 'text';
    input.type = showing ? 'password' : 'text';
    $(buttonId).textContent = showing ? 'Zeigen' : 'Verbergen';
    $(buttonId).setAttribute('aria-pressed', showing ? 'false' : 'true');
  });
}

function updateConsentVisibility() {
  var field = $('consent-field');
  if ($('enable_ytdlp_true').checked) {
    field.classList.remove('hidden');
  } else {
    field.classList.add('hidden');
  }
}

// Capability-driven conditional visibility: tunnel fields only with the
// tunnel capability, edge fields only with static/oauth, TLS cert/key only
// in edge-TLS mode.
function updateConditionalVisibility() {
  var tunnelOn = $('access_tunnel').checked;
  var staticOn = $('access_static').checked;
  var oauthOn = $('access_oauth').checked;
  function toggle(id, hidden) {
    $(id).classList.toggle('hidden', !!hidden);
  }
  toggle('tunnel-section', !tunnelOn);
  toggle('edge-section', !(staticOn || oauthOn));
  toggle('static-fields', !staticOn);
  toggle('oauth-fields', !oauthOn);
  toggle('tls-cert-fields', !$('tls_edge').checked);
}

/* ------------------------------------------------------- form guard ---- */

// The form exists only for semantics/accessibility; submission must never
// navigate the page (CSP form-action 'none' also blocks it server-side).
function blockSubmission(event) {
  event.preventDefault();
}

/* -------------------------------------------------------------- init --- */

function init() {
  wireShowToggle('yt-show', 'youtube_api_key');
  wireShowToggle('rt-show', 'runtime_api_key');
  wireShowToggle('st-show', 'static_tokens');
  $('btn-generate').addEventListener('click', onGenerate);
  $('btn-copy').addEventListener('click', onCopy);
  $('btn-select').addEventListener('click', onSelectAll);
  $('btn-clear').addEventListener('click', clearSensitiveData);
  $('btn-restart').addEventListener('click', startOver);
  $('generator-form').addEventListener('submit', blockSubmission);
  var radios = document.querySelectorAll('input[name="enable_ytdlp"]');
  for (var i = 0; i < radios.length; i++) {
    radios[i].addEventListener('change', updateConsentVisibility);
  }
  var accessBoxes = document.querySelectorAll('input[name="access"]');
  for (var j = 0; j < accessBoxes.length; j++) {
    accessBoxes[j].addEventListener('change', updateConditionalVisibility);
  }
  var tlsRadios = document.querySelectorAll('input[name="edge_tls"]');
  for (var k = 0; k < tlsRadios.length; k++) {
    tlsRadios[k].addEventListener('change', updateConditionalVisibility);
  }
  updateConsentVisibility();
  updateConditionalVisibility();
}

// Test hook: expose pure helpers without auto-running when loaded by tests.
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    maskSecret: maskSecret,
    secretHasControlCharsOrEdgeWhitespace: secretHasControlCharsOrEdgeWhitespace,
    yamlQuote: yamlQuote,
    yamlScalar: yamlScalar,
    buildYaml: buildYaml,
    normalizeModel: normalizeModel,
    validateModel: validateModel,
    defaultModel: defaultModel,
    parseStaticTokens: parseStaticTokens,
    ACCESS_METHODS: ACCESS_METHODS,
    MCP_IMAGE_BASE: MCP_IMAGE_BASE,
    TUNNEL_IMAGE_BASE: TUNNEL_IMAGE_BASE,
    GOOGLE_KEY_PATTERN: GOOGLE_KEY_PATTERN,
    OPENAI_KEY_PREFIX: OPENAI_KEY_PREFIX,
    TUNNEL_ID_PATTERN: TUNNEL_ID_PATTERN,
    LANGUAGES_PATTERN: LANGUAGES_PATTERN,
  };
} else {
  init();
}
