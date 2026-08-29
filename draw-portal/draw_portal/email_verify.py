from __future__ import annotations

import hashlib
import logging
import secrets
import smtplib
import ssl
from email.message import EmailMessage

logger = logging.getLogger(__name__)


def generate_email_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_email_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def verify_email_code(code: str, code_hash: str) -> bool:
    import hmac

    return hmac.compare_digest(hash_email_code(code.strip()), code_hash)


def send_verification_email(
    *,
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    smtp_from: str,
    to_email: str,
    code: str,
    public_base_url: str,
) -> None:
    msg = EmailMessage()
    msg["Subject"] = "呼图云 · 邮箱验证码"
    msg["From"] = smtp_from
    msg["To"] = to_email
    msg.set_content(
        f"您的呼图云注册验证码是：{code}\n\n"
        f"10 分钟内有效。若非本人操作请忽略。\n\n"
        f"网页入口：{public_base_url.rstrip('/')}/draw-upload/\n"
    )
    context = ssl.create_default_context()
    if smtp_port == 465:
        smtp_cls = smtplib.SMTP_SSL
        smtp_kwargs: dict[str, object] = {"context": context}
    else:
        smtp_cls = smtplib.SMTP
        smtp_kwargs = {}
    with smtp_cls(smtp_host, smtp_port, timeout=30, **smtp_kwargs) as smtp:
        if smtp_port not in (465, 25, 80):
            smtp.starttls(context=context)
        if smtp_user:
            smtp.login(smtp_user, smtp_password)
        smtp.send_message(msg)
    logger.info("verification email queued to=%s", to_email.split("@")[-1])
