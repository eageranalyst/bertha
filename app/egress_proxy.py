"""The only way out of the agent network.

Agent and wiki build containers sit on an internal Docker network with no
route to the host or the internet. This proxy runs on that network and the
normal one, and offers two exits:

- an HTTP proxy (CONNECT for HTTPS, plain forwarding for HTTP) that only
  reaches hosts on the allowlist: GitHub and package registries;
- a straight TCP forward to the model server on the host.

Everything else, including Bertha's own API and any other service on the
host, is unreachable. Refused hosts are logged as EGRESS_DENIED lines.

Runs as `python3 -m app.egress_proxy` with config in env vars:
  EGRESS_ALLOW        comma-separated hosts; ".example.com" also allows subdomains
  MODEL_UPSTREAM      host:port of the model server, e.g. host.docker.internal:1234
  MODEL_LISTEN_PORT   port to forward it on
  PROXY_PORT          HTTP proxy port (default 3128)
"""

import asyncio
import os
import sys

ALLOWED_PORTS = {80, 443}


def parse_allow(raw: str) -> list[str]:
    return [h.strip().lower() for h in raw.split(",") if h.strip()]


def allowed(host: str, allow: list[str]) -> bool:
    host = host.lower().rstrip(".")
    for entry in allow:
        if entry.startswith("."):
            if host == entry[1:] or host.endswith(entry):
                return True
        elif host == entry:
            return True
    return False


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def splice(a_reader, a_writer, b_reader, b_writer) -> None:
    await asyncio.gather(pipe(a_reader, b_writer), pipe(b_reader, a_writer))


async def refuse(writer: asyncio.StreamWriter, host: str) -> None:
    print(f"EGRESS_DENIED {host}", flush=True)
    writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
    await writer.drain()
    writer.close()


def handle_proxy(allow: list[str]):
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
            writer.close()
            return
        request_line, _, rest = head.decode("latin-1").partition("\r\n")
        parts = request_line.split(" ")
        if len(parts) != 3:
            writer.close()
            return
        method, target, version = parts

        if method == "CONNECT":
            host, _, port = target.rpartition(":")
            port_number = int(port) if port.isdigit() else 443
            if not allowed(host, allow) or port_number not in ALLOWED_PORTS:
                await refuse(writer, target)
                return
            try:
                up_reader, up_writer = await asyncio.open_connection(host, port_number)
            except OSError:
                writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
                await writer.drain()
                writer.close()
                return
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            await splice(reader, writer, up_reader, up_writer)
            return

        # Plain HTTP through the proxy: "GET http://host[:port]/path HTTP/1.1".
        if not target.startswith("http://"):
            await refuse(writer, target)
            return
        hostport, _, path = target[len("http://"):].partition("/")
        host, _, port = hostport.partition(":")
        port_number = int(port) if port.isdigit() else 80
        if not allowed(host, allow) or port_number not in ALLOWED_PORTS:
            await refuse(writer, hostport)
            return
        try:
            up_reader, up_writer = await asyncio.open_connection(host, port_number)
        except OSError:
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            writer.close()
            return
        up_writer.write(f"{method} /{path} {version}\r\n{rest}".encode("latin-1"))
        await up_writer.drain()
        await splice(reader, writer, up_reader, up_writer)

    return handle


def handle_forward(upstream_host: str, upstream_port: int):
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            up_reader, up_writer = await asyncio.open_connection(upstream_host, upstream_port)
        except OSError:
            writer.close()
            return
        await splice(reader, writer, up_reader, up_writer)

    return handle


async def main() -> None:
    allow = parse_allow(os.environ.get("EGRESS_ALLOW", ""))
    upstream_host, _, upstream_port = os.environ["MODEL_UPSTREAM"].rpartition(":")
    listen_port = int(os.environ.get("MODEL_LISTEN_PORT", upstream_port))
    proxy_port = int(os.environ.get("PROXY_PORT", "3128"))

    proxy = await asyncio.start_server(handle_proxy(allow), "0.0.0.0", proxy_port)
    forward = await asyncio.start_server(
        handle_forward(upstream_host, int(upstream_port)), "0.0.0.0", listen_port
    )
    print(f"egress proxy on :{proxy_port}, model forward :{listen_port} -> {upstream_host}:{upstream_port}, "
          f"allowing {', '.join(allow) or 'nothing'}", flush=True)
    async with proxy, forward:
        await asyncio.gather(proxy.serve_forever(), forward.serve_forever())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
