/**
 * tileBridge injects the Vitality bridge into every sealed tile at mount.
 *
 * A tile is a sandboxed srcDoc iframe: opaque origin, no network, and no
 * localStorage (it throws). The ONLY way a tile persists is by calling
 * window.Vitality.save/load, which this shim defines by postMessaging the host
 * (useTileHost) and matching each reply by id. Tiles stay pure feature code;
 * the bridge lives and upgrades here in one place.
 */

const SHIM = `<script>
(function () {
  var pending = {}, seq = 0;
  window.addEventListener('message', function (e) {
    var m = e.data;
    if (!m || m.source !== 'vitality-host') return;
    var p = pending[m.id];
    if (!p) return;
    delete pending[m.id];
    if (m.type === 'load:result') p.resolve(m.data);
    else if (m.type === 'save:ok') p.resolve(true);
    else if (m.type === 'save:error') p.reject(new Error(m.reason || 'save_failed'));
  });
  function call(type, extra) {
    return new Promise(function (resolve, reject) {
      var id = 'v' + (++seq);
      pending[id] = { resolve: resolve, reject: reject };
      var msg = { source: 'vitality-tile', type: type, id: id };
      if (extra) for (var k in extra) msg[k] = extra[k];
      parent.postMessage(msg, '*');
      // backstop: never let a tile hang if a reply is somehow lost.
      setTimeout(function () {
        if (!pending[id]) return;
        delete pending[id];
        if (type === 'load') resolve([]);
        else reject(new Error('vitality_timeout'));
      }, 8000);
    });
  }
  window.Vitality = {
    save: function (data) { return call('save', { data: data }); },
    load: function () { return call('load', {}); },
    report: function (stream) {
      parent.postMessage({ source: 'vitality-tile', type: 'report', stream: stream }, '*');
    }
  };
})();
</script>`

/** Prepend the bridge shim so window.Vitality exists inside the sealed tile. */
export function withBridge(html: string): string {
  if (html.includes('<head>')) return html.replace('<head>', '<head>' + SHIM)
  if (html.includes('<body>')) return html.replace('<body>', '<body>' + SHIM)
  return SHIM + html
}
