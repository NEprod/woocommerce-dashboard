/* Progressive enhancement only; validation and save authority remain server-side. */
(function () {
  'use strict';
  function formatJSON(text) {
    JSON.parse(text); // Syntax only. Preserve duplicate keys for server rejection.
    let indent = 0;
    let output = '';
    const tokens = text.match(/"(?:\\.|[^"\\])*"|[{}\[\],:]|[^\s{}\[\],:]+/g);
    tokens.forEach(function (token, index) {
      if (token === '{' || token === '[') { output += token; indent++; if (!['}', ']'].includes(tokens[index + 1])) output += '\n' + '  '.repeat(indent); }
      else if (token === '}' || token === ']') { indent--; if (!['{', '['].includes(tokens[index - 1])) output += '\n' + '  '.repeat(indent); output += token; }
      else if (token === ',') output += ',\n' + '  '.repeat(indent);
      else if (token === ':') output += ': ';
      else output += token;
    });
    return output;
  }
  function highlightJSON(text) {
    const escape = (value) => value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    const expression = /"(?:\\.|[^"\\])*"\s*:|"(?:\\.|[^"\\])*"|\b(?:true|false|null)\b|-?\b\d+(?:\.\d+)?\b/g;
    let output = '', cursor = 0;
    text.slice(0, 40000).replace(expression, function (token, offset) {
      output += escape(text.slice(cursor, offset));
      const kind = token.endsWith(':') ? 'key' : token.startsWith('"') ? 'string' : /^(true|false|null)$/.test(token) ? 'literal' : 'number';
      output += '<span class="json-token-' + kind + '">' + escape(token) + '</span>';
      cursor = offset + token.length;
      return token;
    });
    return output + escape(text.slice(cursor, 40000));
  }
  if (typeof module !== 'undefined') module.exports = { formatJSON, highlightJSON };
  if (typeof document === 'undefined') return;
  let dirty = false;
  document.querySelectorAll('[data-taxonomy-draft]').forEach(function (form) {
    form.addEventListener('input', function () { dirty = true; });
  });
  window.addEventListener('beforeunload', function (event) {
    if (dirty) { event.preventDefault(); event.returnValue = ''; }
  });
  document.querySelectorAll('.taxonomy-form').forEach(function (form) {
    form.addEventListener('submit', function (event) {
      dirty = false;
      // Keep named submit controls enabled so the selected action is submitted.
      if (form.dataset.submitted) { event.preventDefault(); return; }
      form.dataset.submitted = 'yes';
    });
  });
  const button = document.querySelector('[data-format-json]');
  const input = document.getElementById('registry-json');
  function refreshCode() {
    if (!input) return;
    const lines = document.getElementById('registry-lines');
    lines.textContent = Array.from({ length: input.value.split('\n').length }, (_, i) => i + 1).join('\n');
    document.getElementById('registry-highlight').innerHTML = highlightJSON(input.value);
  }
  if (input) {
    refreshCode();
    input.addEventListener('input', refreshCode);
    input.addEventListener('scroll', () => { document.getElementById('registry-lines').scrollTop = input.scrollTop; });
    document.querySelector('[data-find-json]').addEventListener('click', () => {
      const needle = document.getElementById('registry-find').value;
      let position = input.value.indexOf(needle, input.selectionEnd);
      if (position < 0) position = input.value.indexOf(needle);
      if (needle && position >= 0) { input.focus(); input.setSelectionRange(position, position + needle.length); }
      document.getElementById('json-status').textContent = needle && position >= 0 ? 'Match selected in source.' : 'No match.';
    });
  }
  if (button) button.addEventListener('click', function () {
    const status = document.getElementById('json-status');
    try { input.value = formatJSON(input.value); refreshCode(); dirty = true; status.textContent = 'JSON formatted. Review validates the registry schema before saving.'; }
    catch (_) { status.textContent = 'Invalid JSON. Your draft has not changed.'; }
  });
}());
