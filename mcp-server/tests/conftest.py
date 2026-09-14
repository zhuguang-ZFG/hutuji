"""需要真实服务语义的测试使用独立本机实例。"""
import shutil
import socket
import subprocess
import time

import pytest


@pytest.fixture
def live_redis(tmp_path):
    """独立本机 Redis，不接触开发/生产实例；实际执行 Lua 与 Redis 的原子语义。"""
    import redis

    executable = shutil.which("redis-server")
    if not executable:
        pytest.skip("本机没有 Redis 服务端，真实 Redis 回归未执行")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    with (tmp_path / "redis.log").open("wb") as log:
        process = subprocess.Popen(
            [executable, "--bind", "127.0.0.1", "--port", str(port),
             "--save", "", "--appendonly", "no", "--dir", str(tmp_path)],
            stdout=log, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        client = redis.Redis(host="127.0.0.1", port=port, decode_responses=True,
                             socket_connect_timeout=1, socket_timeout=1)
        try:
            deadline = time.monotonic() + 5
            while True:
                try:
                    client.ping()
                    break
                except redis.RedisError:
                    if process.poll() is not None or time.monotonic() >= deadline:
                        pytest.fail("隔离 Redis 未启动，见 redis.log")
                    time.sleep(0.02)
            yield f"redis://127.0.0.1:{port}", client
        finally:
            client.close()
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)
