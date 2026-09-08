"""生图类 API 返回下载地址的公网 https 闸（IMG-URL-SSRF-GAP 唯一实现）。

上游 images API 的响应受 prompt/任务状态影响，可能返回内网 https 地址；
服务器直接 GET 该地址等于把内网当代理（SSRF）。bitmap_svg 生图下载与
modelscope_client 结果图下载共用本模块，各自包装成自己的窄异常类型。
"""

import ipaddress
from urllib.parse import urlsplit


class UrlGuardError(ValueError):
    """下载地址未过公网 https 闸。消息文案与历史口径一致（测试按文案 match）。"""


def validate_public_https_url(url: str) -> None:
    """下载地址必须指向公网 https，否则抛 UrlGuardError。判定：

    - scheme 必须 https；
    - hostname 是 IP 字面量时用 ipaddress 拒一切非 global 地址（is_global False
      即拒，覆盖 loopback/private/link-local/reserved/shared 全段）；
    - hostname 为 'localhost'（大小写不敏感）或以 '.localhost'/'.local'/'.internal'
      结尾同样拒绝；
    - 主机名先剥结尾点：DNS 绝对名形态（`127.0.0.1.`、`localhost.`）与裸名等价，
      不剥会被当成普通域名放行。
    DNS-rebinding 不在本层防御面：域名先解析到内网再变公网，也无法把内网内容
    带出——响应还须过 PNG magic，且下载内容从不回显给调用方。
    """
    try:
        parts = urlsplit(url)
        # urlsplit 不校验端口：非数字端口要访问 .port 才抛 ValueError。httpx
        # 构造请求时对这类 URL 抛 InvalidURL（不是 HTTPError 子类，下载循环的
        # except 捕不到），不在这里拦会裸穿成 500。
        parts.port
    except ValueError:
        # 非法端口等 urlsplit 拒绝的形态按无效地址处理，fail-closed。
        raise UrlGuardError("生图 API 未返回有效的 https 图片地址") from None
    if parts.scheme != "https":
        raise UrlGuardError("生图 API 未返回有效的 https 图片地址")
    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise UrlGuardError("生图 API 未返回有效的 https 图片地址")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if ":" in host:
            # 含 ':' 又不是 IP 字面量 = 未加方括号的 IPv6 / 畸形主机名；urlsplit
            # 与 httpx 对端口的切分不一致，httpx 会抛 InvalidURL 裸穿成 500。
            raise UrlGuardError("生图 API 未返回有效的 https 图片地址") from None
    else:
        if not ip.is_global:
            raise UrlGuardError("生图 API 返回的图片地址指向内网/保留地址段")
        return
    lowered = host.lower()
    if lowered == "localhost" or lowered.endswith((".localhost", ".local", ".internal")):
        raise UrlGuardError("生图 API 返回的图片地址指向本地主机名")
