(args) => {
    return new Promise((resolve) => {
        const timeoutMs = (args.timeout || 60) * 1000;
        const controller = new AbortController();
        const rid = args.rid;
        window.__AISTUDIO__ = window.__AISTUDIO__ || {};
        const aborts = window.__AISTUDIO__.streamAbort = window.__AISTUDIO__.streamAbort || {};
        if (rid) aborts[rid] = () => controller.abort();
        function cleanup() {
            clearTimeout(timer);
            if (rid) delete aborts[rid];
        }
        const timer = setTimeout(() => {
            controller.abort();
            resolve({status: 0, body: 'timeout'});
        }, timeoutMs);

        const headers = Object.assign({}, args.headers || {});

        window.fetch(args.url, {
            method: 'POST',
            headers: headers,
            body: args.body,
            credentials: 'include',
            signal: controller.signal,
        }).then(async (response) => {
            try {
                const text = await response.text();
                resolve({status: response.status, body: text});
            } catch (e) {
                const msg = (e && e.name === 'AbortError') ? 'timeout' : 'network error';
                resolve({status: 0, body: msg});
            } finally {
                cleanup();
            }
        }).catch((err) => {
            cleanup();
            const msg = (err && err.name === 'AbortError') ? 'timeout' : 'network error';
            resolve({status: 0, body: msg});
        });
    });
}
