"""Execute browser deadline scripts with a mocked fetch to verify real aborts."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from aistudio_api.infrastructure.browser.scripts import (
    HOOKED_REQUEST_JS,
    STREAMING_INIT_JS,
)


@pytest.mark.parametrize("streaming", [False, True])
def test_browser_timeout_aborts_even_after_receiving_headers_and_chunks(streaming):
    runtime = shutil.which("node") or shutil.which("bun")
    if not runtime:
        pytest.skip("JavaScript runtime required")
    prelude = """
globalThis.window = globalThis;
let aborted = false;
let chunks = 0;
const watchdog = setTimeout(() => { console.error('deadline did not abort fetch'); process.exit(1); }, 500);
globalThis.__aistudio_stream_push__ = event => {
    if (JSON.parse(event).type === 'chunk') chunks++;
};
globalThis.fetch = async (url, options) => {
    options.signal.addEventListener('abort', () => { aborted = true; });
    return {
        status: 200,
        text: () => new Promise((resolve, reject) => {
            options.signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')));
        }),
        body: {getReader: () => ({
            read: () => new Promise(resolve => setTimeout(() => {
                resolve({done: aborted, value: aborted ? undefined : new Uint8Array([65])});
            }, 3)),
            cancel: () => Promise.resolve(),
        })},
    };
};
"""
    script = STREAMING_INIT_JS if streaming else HOOKED_REQUEST_JS
    check = (
        "if (!aborted || chunks === 0 || Object.keys(window.__AISTUDIO__.streams).length) throw new Error('stream deadline failed');"
        if streaming
        else "if (!aborted || result.status !== 0 || result.body !== 'timeout') throw new Error('replay deadline failed');"
    )
    code = (
        prelude
        + f"""
(async () => {{
    const result = await ({script})({{rid: 'test', url: 'https://example.test', timeout: 0.03}});
    await new Promise(resolve => setTimeout(resolve, 60));
    {check}
    clearTimeout(watchdog);
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    )
    result = subprocess.run(
        [runtime, "-e", code], capture_output=True, text=True, timeout=5
    )
    assert result.returncode == 0, result.stderr
