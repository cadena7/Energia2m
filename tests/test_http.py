"""HTTP cleanup and deadlines without a physical ESP32 or TCP sockets."""

import asyncio
import io
import unittest

from lib.microdot.microdot import Microdot, Request, Response


class Reader:
    def __init__(self, data=b'', stall=False):
        self.buffer = io.BytesIO(data)
        self.stall = stall

    async def read(self, n):
        value = self.buffer.read(n)
        if not value and self.stall:
            await asyncio.Event().wait()
        return value

    async def readexactly(self, n):
        value = await self.read(n)
        if len(value) != n:
            if self.stall:
                await asyncio.Event().wait()
            raise EOFError()
        return value


class Writer:
    def __init__(self, fail_at=None, stall_at=None, stall_close=False):
        self.calls = 0
        self.closed = False
        self.data = bytearray()
        self.fail_at = fail_at
        self.stall_at = stall_at
        self.stall_close = stall_close
        self.transport = self
        self.aborted = False

    def get_extra_info(self, name):
        return ('127.0.0.1', 1234)

    async def awrite(self, data):
        self.calls += 1
        if self.calls == self.fail_at:
            raise OSError(104, 'connection reset')
        if self.calls == self.stall_at:
            await asyncio.Event().wait()
        self.data.extend(data)

    def close(self):
        self.closed = True

    def abort(self):
        self.aborted = True

    async def wait_closed(self):
        if self.stall_close:
            await asyncio.Event().wait()


class HttpTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.app = Microdot()
        self.app.request_timeout = 0.02
        self.app.handler_timeout = 0.02
        self.app.response_timeout = 0.02
        self.app.close_timeout = 0.02
        self.files = []

        @self.app.route('/file')
        async def file(request):
            body = io.BytesIO(b'x' * 8192)
            self.files.append(body)
            return Response.send_file('test.js', stream=body)

    async def test_idle_incomplete_headers_and_body_expire(self):
        for data in (b'', b'GET /file HTTP/1.1\r\nHost:',
                     b'POST /file HTTP/1.1\r\nContent-Length: 15\r\n\r\n'):
            with self.subTest(data=data):
                writer = Writer()
                await asyncio.wait_for(self.app.handle_request(Reader(data, stall=True), writer), 0.2)
                self.assertTrue(writer.closed)
        self.assertEqual(self.files, [])

    async def test_file_and_connection_close_on_header_or_body_disconnect(self):
        # Status line, header, body: each can be interrupted by closing Chrome.
        for fail_at in (1, 2, 5, 6):
            with self.subTest(fail_at=fail_at):
                writer = Writer(fail_at=fail_at)
                await self.app.handle_request(Reader(b'GET /file HTTP/1.1\r\n\r\n'), writer)
                self.assertTrue(writer.closed)
                self.assertTrue(self.files[-1].closed)

    async def test_stalled_download_and_stalled_close_are_bounded(self):
        writer = Writer(stall_at=6, stall_close=True)
        await asyncio.wait_for(self.app.handle_request(Reader(b'GET /file HTTP/1.1\r\n\r\n'), writer), 0.2)
        self.assertTrue(writer.closed)
        self.assertTrue(self.files[-1].closed)

        self.assertTrue(writer.aborted)

    async def test_head_closes_file_without_sending_body(self):
        writer = Writer()
        await self.app.handle_request(Reader(b'HEAD /file HTTP/1.1\r\n\r\n'), writer)
        self.assertTrue(self.files[-1].closed)
        self.assertTrue(writer.closed)
        self.assertNotIn(b'xxxxxxxx', writer.data)

    async def test_handler_timeout_releases_connection(self):
        @self.app.route('/blocked')
        async def blocked(request):
            await asyncio.Event().wait()
        writer = Writer()
        await asyncio.wait_for(self.app.handle_request(Reader(b'GET /blocked HTTP/1.1\r\n\r\n'), writer), 0.2)
        self.assertTrue(writer.closed)

    async def test_cancelled_download_releases_file_and_connection(self):
        writer = Writer(stall_at=6)
        task = asyncio.create_task(self.app.handle_request(Reader(b'GET /file HTTP/1.1\r\n\r\n'), writer))
        while writer.calls < 6:
            await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(writer.closed)
        self.assertTrue(self.files[-1].closed)

    async def test_oversized_line_is_stopped_before_consuming_entire_input(self):
        reader = Reader(b'x' * (Request.max_readline * 10))
        with self.assertRaises(ValueError):
            await Request._safe_readline(reader)
        self.assertLessEqual(reader.buffer.tell(), Request.max_readline + 1)

    async def test_malformed_request_and_oversized_body_close_connection(self):
        for data, status in ((b'bad\r\n\r\n', b'400'),
                             (b'POST /file HTTP/1.1\r\nContent-Length: -1\r\n\r\n', b'400'),
                             (b'POST /file HTTP/1.1\r\nContent-Length: 999999\r\n\r\n', b'413')):
            writer = Writer()
            await self.app.handle_request(Reader(data), writer)
            self.assertIn(status, writer.data.split(b'\r\n')[0])
            self.assertTrue(writer.closed)


if __name__ == '__main__':
    unittest.main()
