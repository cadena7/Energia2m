const assert = require('node:assert/strict');
const { test } = require('node:test');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/dashboard.js'), 'utf8');
const history = () => ({ SAMPLE_INTERVAL_MS: 500, Norte: [1], Sur: [2], Este: [3], Oeste: [4] });

function dashboard({ missing = false, constructError = false, updateError = false } = {}) {
    const elements = {};
    let poller;
    let response = { ok: true, json: async () => history() };
    const context = {
        document: { getElementById(id) { return elements[id] ||= { textContent: '', open: false }; } },
        console: { error() {} },
        createDevicePoller(options) { poller = options; },
        fetch: async () => response
    };
    if (!missing) context.Chart = function (_, config) {
        if (constructError) throw new Error('canvas unavailable');
        this.data = config.data;
        this.options = config.options;
        this.update = () => { if (updateError) throw new Error('draw failed'); };
    };
    vm.runInNewContext(source, context);
    return {
        elements,
        setResponse(value) { response = value; },
        async tick() {
            try { await poller.run({ aborted: false }); }
            catch (error) { poller.onError(error); }
        }
    };
}

test('bundled Chart.js loads as a classic script and exposes Chart', () => {
    const context = vm.createContext({});
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/Chart.min.js'), 'utf8'), context);
    assert.equal(typeof context.Chart, 'function');
    assert.equal(context.Chart.version, '4.5.0');
});

for (const options of [{ missing: true }, { constructError: true }, { updateError: true }]) {
    test('chart failure preserves working data polling: ' + JSON.stringify(options), async () => {
        const env = dashboard(options);
        await env.tick();
        assert.equal(env.elements.valN.textContent, '1.00');
        assert.match(env.elements['connection-status'].textContent, /Datos recibidos/);
        assert.match(env.elements['chart-status'].textContent, /Error/);
        await env.tick();
        assert.equal(env.elements.valO.textContent, '4.00');
    });
}

test('HTTP, malformed JSON and invalid history report their actual cause', async () => {
    const env = dashboard();
    for (const [response, expected] of [
        [{ ok: false, status: 503 }, /HTTP 503/],
        [{ ok: true, json: async () => { throw new Error('unexpected token'); } }, /JSON inválida.*unexpected token/],
        [{ ok: true, json: async () => ({ ...history(), Norte: ['bad'] }) }, /Historial inválido/],
        [{ ok: true, json: async () => ({ ...history(), SAMPLE_INTERVAL_MS: -1 }) }, /SAMPLE_INTERVAL_MS/]
    ]) {
        env.setResponse(response);
        await env.tick();
        assert.match(env.elements['connection-status'].textContent, expected);
        assert.equal(env.elements.valN.textContent, '—');
    }
    env.setResponse({ ok: true, json: async () => history() });
    await env.tick();
    assert.equal(env.elements.valN.textContent, '1.00');
    assert.equal(env.elements['chart-status'].textContent, 'Gráfica actualizada.');
});

test('network and timeout failures are identified separately', async () => {
    for (const [name, expected] of [['TypeError', /Fallo de red/], ['AbortError', /Tiempo de espera agotado/]]) {
        let poller;
        const elements = {};
        vm.runInNewContext(source, {
            document: { getElementById(id) { return elements[id] ||= {}; } },
            console: { error() {} },
            createDevicePoller(options) { poller = options; },
            fetch: async () => { const error = new Error('request failed'); error.name = name; throw error; }
        });
        try { await poller.run({ aborted: false }); }
        catch (error) { poller.onError(error); }
        assert.match(elements['connection-status'].textContent, expected);
    }
});

test('missing calibration opens configuration and never displays fabricated currents', async () => {
    const env = dashboard();
    env.setResponse({ ok: true, json: async () => ({ SAMPLE_INTERVAL_MS: 500,
        Norte: [], Sur: [], Este: [], Oeste: [], calibration_ready: false, warning: 'Falta config.json' }) });
    await env.tick();
    assert.equal(env.elements.valN.textContent, '—');
    assert.equal(env.elements['calibration-panel'].open, true);
    assert.equal(env.elements['connection-status'].textContent, 'Falta config.json');
});
