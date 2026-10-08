"""Local TCP stress test; accelerated timeouts, simulated hardware.

Run from the project root: python3 tests/stress_http.py
"""

import asyncio
import builtins
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_calibration import load_firmware


async def main():
    firmware = load_firmware()
    app = firmware.app
    app.request_timeout = 0.15
    app.response_timeout = 2
    app.close_timeout = 0.15
    server = await app.start_server(host='127.0.0.1', port=0, start_serving=False)
    await server.start_serving()
    port = server.sockets[0].getsockname()[1]
    opened = []
    original_open = builtins.open

    def track_open(*args, **kwargs):
        file = original_open(*args, **kwargs)
        opened.append(file)
        return file

    async def idle():
        async def wait():
            while app.active_connections:
                await asyncio.sleep(0.001)
        await asyncio.wait_for(wait(), 3)

    async def get(path):
        reader, writer = await asyncio.open_connection('127.0.0.1', port)
        try:
            writer.write(('GET ' + path + ' HTTP/1.1\r\nHost: localhost\r\n\r\n').encode())
            await writer.drain()
            response = await asyncio.wait_for(reader.read(), 3)
            assert response.startswith(b'HTTP/1.0 200'), (path, response[:80])
            assert b'Connection: close' in response
            return response
        finally:
            writer.close()
            await writer.wait_closed()

    async def abandon(data, read_prefix=False):
        reader, writer = await asyncio.open_connection('127.0.0.1', port)
        writer.write(data)
        await writer.drain()
        if read_prefix:
            await reader.read(100)
        writer.transport.abort()

    try:
        with patch('builtins.open', side_effect=track_open):
            # Fill all slots with idle/preconnected clients, then confirm recovery.
            clients = [await asyncio.open_connection('127.0.0.1', port) for _ in range(12)]
            await asyncio.sleep(0.01)
            assert app.active_connections <= app.max_connections
            await asyncio.sleep(0.2)
            for reader, writer in clients:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass
            await idle()
            await get('/api/history')

            for cycle in range(100):
                await get('/')
                await idle()
                await asyncio.gather(*(get(path) for path in (
                    '/static/Chart.min.js', '/static/calibration.css',
                    '/static/polling.js', '/static/dashboard.js',
                    '/static/calibration.js', '/api/history')))
                await idle()
                await asyncio.gather(
                    abandon(b'GET /static/Chart.min.js HTTP/1.1\r\n\r\n', True),
                    abandon(b'GET / HTTP/1.1\r\nHost:'),
                    abandon(b'POST /api/calibration HTTP/1.1\r\nContent-Length: 100\r\n\r\n{'),
                    get('/api/calibration'))
                await idle()
                assert all(file.closed for file in opened), 'Leaked file at cycle {}'.format(cycle)
                if (cycle + 1) % 25 == 0:
                    print('{} cycles: healthy, no open files or active connections'.format(cycle + 1), flush=True)
            await get('/api/history')
            await idle()
            print('PASS: 100 cycles, 1100+ connections, {} file responses closed'.format(len(opened)), flush=True)
    finally:
        server.close()
        await server.wait_closed()


asyncio.run(main())
