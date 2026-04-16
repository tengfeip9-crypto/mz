from __future__ import annotations

import argparse
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

MODULE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = MODULE_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from project_config import RUN_LOG_DIR  # noqa: E402

WEB_APP_PATH = MODULE_DIR / "qzone_web_app.py"
PUBLIC_URL_FILE = RUN_LOG_DIR / "latest_public_url.txt"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8765
DEFAULT_TUNNEL_TIMEOUT = 35.0
DEFAULT_TUNNEL_PROTOCOL = "http2"
TUNNEL_URL_PATTERN = re.compile(r"https://(?!api\.)[a-z0-9-]+\.trycloudflare\.com", re.IGNORECASE)
PORT_SUFFIX_PATTERN = re.compile(r":(\d+)$")


def resolve_cloudflared_path(explicit_path: str | None = None) -> Path | None:
    candidates: list[Path] = []
    if explicit_path:
        candidates.append(Path(explicit_path))

    which_path = shutil.which("cloudflared")
    if which_path:
        candidates.append(Path(which_path))

    user_profile = os.environ.get("USERPROFILE")
    if user_profile:
        profile_path = Path(user_profile)
        candidates.extend(
            [
                profile_path / "cloudflared.exe",
                profile_path / "Downloads" / "cloudflared.exe",
                profile_path / "Desktop" / "cloudflared.exe",
            ]
        )

    candidates.append(PROJECT_ROOT / "cloudflared.exe")

    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        if candidate.is_file():
            return candidate
    return None


def discover_local_ip() -> str:
    try:
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def _extract_port(address: str) -> int | None:
    match = PORT_SUFFIX_PATTERN.search(address.strip())
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def find_listening_pids(port: int) -> list[int]:
    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return []

    pids: set[int] = set()
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line or not line.upper().startswith("TCP"):
            continue

        parts = line.split()
        if len(parts) < 5:
            continue

        local_address = parts[1]
        state = parts[3].upper()
        pid_text = parts[4]
        if state != "LISTENING":
            continue

        local_port = _extract_port(local_address)
        if local_port != port:
            continue

        try:
            pid = int(pid_text)
        except ValueError:
            continue
        if pid > 0:
            pids.add(pid)

    return sorted(pids)


def terminate_pid(pid: int) -> None:
    if pid <= 0 or pid == os.getpid():
        return
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return


def ensure_port_available(port: int, timeout_seconds: float = 8.0) -> None:
    existing_pids = [pid for pid in find_listening_pids(port) if pid != os.getpid()]
    if not existing_pids:
        return

    joined = ", ".join(str(pid) for pid in existing_pids)
    print(f"发现端口 {port} 已被旧进程占用，正在清理: {joined}")
    for pid in existing_pids:
        terminate_pid(pid)

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        remaining = [pid for pid in find_listening_pids(port) if pid != os.getpid()]
        if not remaining:
            return
        time.sleep(0.4)

    remaining_text = ", ".join(str(pid) for pid in remaining)
    raise RuntimeError(f"端口 {port} 仍被占用，无法启动网站服务: {remaining_text}")


def wait_for_http_ready(
    url: str,
    timeout_seconds: float,
    *,
    process: subprocess.Popen[str] | None = None,
) -> None:
    deadline = time.time() + timeout_seconds
    last_error = ""
    while time.time() < deadline:
        if process is not None and process.poll() is not None:
            raise RuntimeError("网站服务进程已提前退出，未能完成启动。")
        try:
            with urlopen(url, timeout=2) as response:
                if 200 <= response.status < 500:
                    return
        except HTTPError as exc:
            if 200 <= exc.code < 500:
                return
            last_error = str(exc)
        except URLError as exc:
            last_error = str(exc)
        except OSError as exc:
            last_error = str(exc)
        time.sleep(0.5)
    raise TimeoutError(f"本地网站未在预期时间内启动: {last_error or url}")


def terminate_process(process: subprocess.Popen[str] | None, name: str, timeout_seconds: float = 8.0) -> None:
    if process is None or process.poll() is not None:
        return

    process.terminate()
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    finally:
        print(f"{name} 已停止。")


class CloudflaredQuickTunnel:
    def __init__(self, executable: Path, target_url: str, protocol: str = DEFAULT_TUNNEL_PROTOCOL) -> None:
        self.executable = executable
        self.target_url = target_url
        self.protocol = protocol
        self.process: subprocess.Popen[str] | None = None
        self.public_url = ""
        self._queue: queue.Queue[str] = queue.Queue()
        self._reader_thread: threading.Thread | None = None

    def start(self) -> None:
        command = [str(self.executable), "tunnel", "--protocol", self.protocol, "--url", self.target_url]
        self.process = subprocess.Popen(
            command,
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self._reader_thread = threading.Thread(target=self._pump_output, daemon=True)
        self._reader_thread.start()

    def _pump_output(self) -> None:
        if self.process is None or self.process.stdout is None:
            return

        for raw_line in self.process.stdout:
            line = raw_line.rstrip()
            if line:
                print(f"[cloudflared] {line}")
            match = TUNNEL_URL_PATTERN.search(line)
            if match and not self.public_url:
                self.public_url = match.group(0)
                self._queue.put(self.public_url)

    def wait_for_public_url(self, timeout_seconds: float) -> str:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if self.public_url:
                return self.public_url
            if self.process is not None and self.process.poll() is not None:
                raise RuntimeError("cloudflared 已提前退出，未返回公网地址。")
            try:
                return self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
        raise TimeoutError("cloudflared 启动超时，未解析到公网地址。")

    def stop(self) -> None:
        terminate_process(self.process, "cloudflared")


def write_public_url_file(
    *,
    output_path: Path,
    local_url: str,
    lan_url: str,
    public_url: str | None,
    cloudflared_path: Path | None,
) -> None:
    RUN_LOG_DIR.mkdir(parents=True, exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"started_at={time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"local_url={local_url}",
        f"lan_url={lan_url}",
    ]
    if public_url:
        lines.append(f"public_url={public_url}")
    if cloudflared_path:
        lines.append(f"cloudflared_path={cloudflared_path}")
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def start_server(host: str, port: int) -> subprocess.Popen[str]:
    command = [sys.executable, str(WEB_APP_PATH), "--host", host, "--port", str(port)]
    return subprocess.Popen(command, cwd=str(PROJECT_ROOT))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="启动 QQ 空间远程登录网站，可选自动创建 Cloudflare Quick Tunnel")
    parser.add_argument("--host", default=DEFAULT_HOST, help="本地网站监听地址，默认 0.0.0.0")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="本地网站监听端口，默认 8765")
    parser.add_argument("--local-only", action="store_true", help="只启动本地网站，不创建公网隧道")
    parser.add_argument("--cloudflared-path", help="cloudflared.exe 路径，未提供时会自动查找")
    parser.add_argument(
        "--tunnel-timeout",
        type=float,
        default=DEFAULT_TUNNEL_TIMEOUT,
        help="等待 Quick Tunnel 公网地址的秒数，默认 35",
    )
    parser.add_argument(
        "--public-url-file",
        default=str(PUBLIC_URL_FILE),
        help="把最新访问地址写入到文件，默认 run_logs/latest_public_url.txt",
    )
    parser.add_argument(
        "--tunnel-protocol",
        default=DEFAULT_TUNNEL_PROTOCOL,
        choices=["http2", "quic", "auto"],
        help="cloudflared Quick Tunnel 使用的协议，默认 http2",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    local_url = f"http://127.0.0.1:{args.port}"
    lan_url = f"http://{discover_local_ip()}:{args.port}"
    ready_url = f"{local_url}/api/auth/me"

    server_process: subprocess.Popen[str] | None = None
    tunnel: CloudflaredQuickTunnel | None = None
    public_url: str | None = None

    try:
        ensure_port_available(args.port)
        server_process = start_server(args.host, args.port)
        wait_for_http_ready(ready_url, timeout_seconds=20.0, process=server_process)

        print(f"本地网站已就绪: {local_url}")
        print(f"局域网访问地址: {lan_url}")

        if not args.local_only:
            cloudflared_path = resolve_cloudflared_path(args.cloudflared_path)
            if cloudflared_path is None:
                print("未找到 cloudflared，可继续使用本地/局域网地址。若要公网测试，请传入 --cloudflared-path。")
            else:
                try:
                    tunnel = CloudflaredQuickTunnel(
                        cloudflared_path,
                        local_url,
                        protocol=args.tunnel_protocol,
                    )
                    tunnel.start()
                    public_url = tunnel.wait_for_public_url(args.tunnel_timeout)
                    print(f"公网测试地址: {public_url}")
                    print("把上面的地址发给你信任的设备即可直接打开。")
                except Exception as exc:
                    print(f"Quick Tunnel 启动失败，本地网站将继续保留: {exc}")
                    public_url = None
                    if tunnel is not None:
                        tunnel.stop()
                        tunnel = None
        else:
            cloudflared_path = None

        write_public_url_file(
            output_path=Path(args.public_url_file),
            local_url=local_url,
            lan_url=lan_url,
            public_url=public_url,
            cloudflared_path=cloudflared_path,
        )
        print(f"访问地址已写入: {Path(args.public_url_file)}")
        print("按 Ctrl+C 可以同时关闭网站和隧道。")

        while server_process.poll() is None:
            time.sleep(0.8)
    except KeyboardInterrupt:
        pass
    finally:
        if tunnel is not None:
            tunnel.stop()
        terminate_process(server_process, "网站服务")


if __name__ == "__main__":
    main()
