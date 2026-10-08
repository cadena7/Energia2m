// One request at a time, with timeout, backoff and page lifecycle cleanup.
window.createDevicePoller = ({ enabled = () => true, run, onError,
    interval = 1000, timeout = 8000 }) => {
    let timer = null;
    let active = null;
    let pageHidden = false;
    let failures = 0;
    let refreshPending = false;
    const available = () => !pageHidden && !document.hidden && enabled();

    function schedule(delay) {
        clearTimeout(timer);
        timer = available() ? setTimeout(tick, delay) : null;
    }

    async function tick() {
        timer = null;
        if (!available() || active) return;
        refreshPending = false;
        const controller = new AbortController();
        active = controller;
        const deadline = setTimeout(() => controller.abort(), timeout);
        try {
            await run(controller.signal);
            failures = 0;
        } catch (error) {
            if (available()) {
                failures = Math.min(failures + 1, 4);
                onError(error);
            }
        } finally {
            clearTimeout(deadline);
            active = null;
            schedule(refreshPending ? 0 : Math.min(interval * (2 ** failures), 15000));
        }
    }

    function refresh() {
        clearTimeout(timer);
        timer = null;
        if (!available()) {
            if (active) active.abort();
            return;
        }
        failures = 0;
        refreshPending = true;
        if (!active) schedule(0);
    }

    document.addEventListener('visibilitychange', refresh);
    window.addEventListener('pagehide', () => { pageHidden = true; refresh(); });
    window.addEventListener('pageshow', () => { pageHidden = false; refresh(); });
    window.addEventListener('online', refresh);
    refresh();
    return { refresh };
};
