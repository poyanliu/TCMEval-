"""TCP-level reverse proxy: routes /api/* to FastAPI, everything else to Streamlit.
Handles both HTTP and WebSocket because it works at the TCP byte level."""

import asyncio
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("proxy")

LISTEN_PORT = 6008
STREAMLIT_PORT = 6009
FASTAPI_PORT = 8000
STREAMLIT_HOST = "127.0.0.1"
FASTAPI_HOST = "127.0.0.1"

API_PREFIXES = (b"/api/", b"/health", b"/docs", b"/redoc", b"/openapi.json")


async def pipe(reader, writer):
    """Read from reader and write to writer until EOF."""
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError, OSError):
        pass


def parse_path(data: bytes):
    """Extract the HTTP path from request line."""
    try:
        first_line = data.split(b"\r\n")[0].decode("utf-8", errors="replace")
        parts = first_line.split(" ")
        if len(parts) >= 2:
            return parts[1]
    except Exception:
        pass
    return None


async def handle_client(client_reader, client_writer):
    """Accept a client connection, peek at the path, then relay to the right backend."""
    backend_host = STREAMLIT_HOST
    backend_port = STREAMLIT_PORT

    try:
        peek = await asyncio.wait_for(client_reader.read(4096), timeout=10)
        if not peek:
            client_writer.close()
            return

        path = parse_path(peek)
        if path and any(path.startswith(p.decode()) for p in API_PREFIXES):
            backend_host, backend_port = FASTAPI_HOST, FASTAPI_PORT

        backend_reader, backend_writer = await asyncio.wait_for(
            asyncio.open_connection(backend_host, backend_port),
            timeout=5,
        )

        # Forward initial data + bidirectionally relay
        backend_writer.write(peek)

        async def forward():
            await backend_writer.drain()
            await asyncio.gather(
                pipe(client_reader, backend_writer),
                pipe(backend_reader, client_writer),
            )

        await asyncio.wait_for(forward(), timeout=86400)

    except (ConnectionRefusedError, asyncio.TimeoutError, OSError):
        pass
    except Exception:
        pass
    finally:
        try:
            client_writer.close()
        except OSError:
            pass


async def main():
    server = await asyncio.start_server(handle_client, "0.0.0.0", LISTEN_PORT, backlog=128)
    log.info("Proxy listening on 0.0.0.0:%d", LISTEN_PORT)
    log.info("  /api/* /health /docs  → FastAPI :%d", FASTAPI_PORT)
    log.info("  /literature/* ...      → Streamlit :%d", STREAMLIT_PORT)

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
