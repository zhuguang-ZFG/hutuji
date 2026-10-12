"""成功配额的持久预占；客户端离开不能释放仍在执行的付费操作。"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import functools
import inspect
import time
from dataclasses import dataclass

from fastapi import HTTPException


@dataclass
class Reservation:
    store: object
    token: str
    user_id: str
    event_type: str
    settled: bool = False


_current = contextvars.ContextVar("usage_reservation", default=None)
_running: set[asyncio.Task] = set()


@contextlib.contextmanager
def slot(store, user_id, event_type, limit, detail, *, error=HTTPException):
    token = store.reserve_usage(user_id, event_type, limit, when=time.time())
    if token is None:
        if error is HTTPException:
            raise error(status_code=429, detail=detail)
        raise error(detail, status=429)
    reservation = Reservation(store, token, user_id, event_type)
    context_token = _current.set(reservation)
    uncertain = False
    try:
        yield reservation
    except asyncio.CancelledError:
        # 服务退出可能取消协程，但 to_thread 中的调用还没结束；保守保留预占。
        uncertain = True
        raise
    finally:
        _current.reset(context_token)
        if not reservation.settled and not uncertain:
            store.release_usage(token)


def record(store, **event) -> bool:
    reservation = _current.get()
    if (reservation is None or reservation.store is not store
            or reservation.user_id != event.get("user_id")
            or reservation.event_type != event.get("event_type")):
        return False
    # 已产生成果却结算失败时，保留数据库预占供人工核对，不能释放后重复付费。
    reservation.settled = True
    store.settle_usage(reservation.token, **event)
    return True


def guarded(store, event_type, limit, detail):
    """保留 FastAPI 签名；后台任务持有预算直到真实成功/失败。"""
    def decorate(function):
        signature = inspect.signature(function, eval_str=True)

        @functools.wraps(function)
        async def wrapper(*args, **kwargs):
            user_id = signature.bind(*args, **kwargs).arguments["user_id"]

            async def execute():
                with slot(store, user_id, event_type, limit, detail):
                    result = await function(*args, **kwargs)
                    if event_type == "wx_chat":
                        used = sum(row["count"] for row in store.usage_counts_for_user_since(
                            user_id, time.time() - 86400) if row["event_type"] == event_type)
                        result["remaining"] = max(0, limit - used)
                    return result

            task = asyncio.create_task(execute())
            _running.add(task)

            def finished(done):
                _running.discard(done)
                if not done.cancelled():
                    done.exception()

            task.add_done_callback(finished)
            return await asyncio.shield(task)

        wrapper.__signature__ = signature
        return wrapper
    return decorate
