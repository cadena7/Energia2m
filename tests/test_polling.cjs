const assert = require('node:assert/strict');
const { test } = require('node:test');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');

function environment() {
    let now = 0;
    let nextId = 0;
    const timers = new Map();
    const window = new EventTarget();
    const document = new EventTarget();
    document.hidden = false;
    const context = vm.createContext({
        window, document, AbortController,
        setTimeout(fn, delay) {
            const id = ++nextId;
            timers.set(id, { at: now + delay, fn });
            return id;
        },
        clearTimeout(id) { timers.delete(id); }
    });
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/polling.js'), 'utf8'), context);
    return {
        window, document, create: window.createDevicePoller,
        async advance(milliseconds) {
            const end = now + milliseconds;
            for (let iteration = 0; iteration < 1000; iteration++) {
                for (let flush = 0; flush < 10; flush++) await Promise.resolve();
                const candidates = [...timers].filter(([, timer]) => timer.at <= end)
                    .sort((a, b) => a[1].at - b[1].at);
                if (!candidates.length) { now = end; return; }
                const [id, timer] = candidates[0];
                now = timer.at;
                timers.delete(id);
                timer.fn();
            }
            throw new Error('Timer loop did not settle');
        }
    };
}

test('a slow request cannot overlap with timers or repeated refresh events', async () => {
    const env = environment();
    let calls = 0;
    let finish;
    const poller = env.create({ interval: 10, timeout: 1000,
        run: () => { calls++; return new Promise(resolve => { finish = resolve; }); },
        onError: () => assert.fail('unexpected error') });
    await env.advance(0);
    for (let i = 0; i < 20; i++) poller.refresh();
    await env.advance(100);
    assert.equal(calls, 1);
    finish();
    await env.advance(0);
    assert.equal(calls, 2);
});

test('timeouts back off and a successful reconnection restores the interval', async () => {
    const env = environment();
    let calls = 0;
    let errors = 0;
    env.create({ interval: 10, timeout: 20,
        run: signal => {
            calls++;
            if (calls >= 3) return Promise.resolve();
            return new Promise((resolve, reject) => signal.addEventListener('abort', () => reject(new Error('timeout'))));
        }, onError: () => { errors++; } });
    await env.advance(20);
    assert.equal(calls, 1);
    assert.equal(errors, 1);
    await env.advance(20);
    assert.equal(calls, 2);
    await env.advance(59);
    assert.equal(calls, 2);
    assert.equal(errors, 2);
    await env.advance(1);
    assert.equal(calls, 3);
    await env.advance(10);
    assert.equal(calls, 4);
});

test('hiding/closing cancels reads; showing/bfcache restore restarts once', async () => {
    const env = environment();
    let calls = 0;
    let aborts = 0;
    env.create({ interval: 10, timeout: 1000,
        run: signal => {
            calls++;
            return new Promise((resolve, reject) => signal.addEventListener('abort', () => {
                aborts++; reject(new Error('cancelled'));
            }));
        }, onError: () => assert.fail('lifecycle cancellation is not a network error') });
    await env.advance(0);
    env.document.hidden = true;
    env.document.dispatchEvent(new Event('visibilitychange'));
    await env.advance(100);
    assert.equal(aborts, 1);
    assert.equal(calls, 1);
    env.document.hidden = false;
    env.document.dispatchEvent(new Event('visibilitychange'));
    await env.advance(0);
    assert.equal(calls, 2);
    env.window.dispatchEvent(new Event('pagehide'));
    await env.advance(100);
    assert.equal(aborts, 2);
    assert.equal(calls, 2);
    env.window.dispatchEvent(new Event('pageshow'));
    env.window.dispatchEvent(new Event('online'));
    await env.advance(0);
    assert.equal(calls, 3);
});

test('closing the calibration panel stops polling until it opens again', async () => {
    const env = environment();
    let open = false;
    let calls = 0;
    const poller = env.create({ enabled: () => open, interval: 10,
        run: async () => { calls++; }, onError: () => assert.fail() });
    await env.advance(100);
    assert.equal(calls, 0);
    open = true;
    poller.refresh();
    await env.advance(0);
    assert.equal(calls, 1);
    open = false;
    poller.refresh();
    await env.advance(100);
    assert.equal(calls, 1);
    open = true;
    poller.refresh();
    await env.advance(0);
    assert.equal(calls, 2);
});
