import { createServer } from 'node:http';
import { randomBytes } from 'node:crypto';

/** Reconnectable, loopback-only transport to the existing in-memory controller. */
export async function startControlChannel(handle) {
  const route = `/${randomBytes(32).toString('hex')}`;
  const server = createServer(async (request, response) => {
    response.setHeader('Cache-Control', 'no-store');
    const host = `127.0.0.1:${server.address().port}`;
    if (request.method !== 'POST' || request.url !== route || request.headers.host !== host ||
        request.headers.origin || request.headers['content-type'] !== 'application/json') {
      response.writeHead(403).end(); return;
    }
    try {
      const chunks = []; let size = 0;
      for await (const chunk of request) {
        size += chunk.length;
        if (size > 65536) { response.writeHead(413).end(); return; }
        chunks.push(chunk);
      }
      const result = await handle(JSON.parse(Buffer.concat(chunks).toString('utf8')));
      response.writeHead(200, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify(result));
    } catch {
      response.writeHead(400, { 'Content-Type': 'application/json' });
      response.end(JSON.stringify({ status: 'ERROR', code: 'INVALID_CONTROL_REQUEST' }));
    }
  });
  server.requestTimeout = 15000;
  await new Promise((resolve, reject) => {
    server.once('error', reject); server.listen(0, '127.0.0.1', resolve);
  });
  return { url: `http://127.0.0.1:${server.address().port}${route}`,
    close: () => new Promise(resolve => server.close(resolve)) };
}
