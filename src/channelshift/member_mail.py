"""Opt-in SMTP delivery; never reuses a browser login or sends in tests."""
from __future__ import annotations

from email.message import EmailMessage
import os
from pathlib import Path
import re
import smtplib
import ssl
import stat
from urllib.parse import urlsplit

from .member_auth import AuthError, _reject_links, _valid_token, normalize_email


def _origin(value):
    try:
        parsed = urlsplit(value)
        valid_scheme = parsed.scheme == "https" or (parsed.scheme == "http" and parsed.hostname == "127.0.0.1")
        if (not valid_scheme or not parsed.hostname or parsed.username is not None or parsed.password is not None
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment
                or any(c.isspace() for c in value) or (parsed.port is not None and not 1 <= parsed.port <= 65535)):
            raise ValueError
        return parsed.scheme + "://" + parsed.netloc
    except (ValueError, TypeError, AttributeError):
        raise AuthError("invalid_public_base_url") from None


def _read_password(path):
    try:
        path = Path(os.path.abspath(os.fspath(path)))
        _reject_links(path)
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise AuthError("unsafe_smtp_password_file")
        if os.name != "nt" and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
            raise AuthError("unsafe_smtp_password_file")
        with path.open("rb") as stream:
            raw = stream.read(1025)
        if not 1 <= len(raw) <= 1024:
            raise AuthError("invalid_smtp_configuration")
        password = raw.decode("utf-8").strip()
        if not password or any(char in password for char in "\r\n\x00"):
            raise AuthError("invalid_smtp_configuration")
        return password
    except (OSError, UnicodeError):
        raise AuthError("invalid_smtp_configuration") from None


class SMTPMailer:
    def __init__(self, host, port, sender, username, password_file, mode, base_url):
        if (type(host) is not str or not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", host)
                or type(port) is not int or not 1 <= port <= 65535
                or mode not in {"starttls", "smtps"} or not username
                or any(char in username for char in "\r\n\x00")):
            raise AuthError("invalid_smtp_configuration")
        self.host, self.port = host, port
        self.sender, self.username = normalize_email(sender), username
        self.password_file = str(password_file)
        self.mode, self.base_url = mode, _origin(base_url)
        # Read to reject a missing/unsafe file at configuration time, not after signup.
        # The value is not retained; rotation takes effect on the next message.
        _read_password(password_file)

    def __call__(self, recipient, token):
        self.send_verification(recipient, token, "등록한 계정")

    def send_verification(self, recipient, token, username):
        recipient = normalize_email(recipient)
        if not _valid_token(token) or type(username) is not str or any(c in username for c in "\r\n\x00"):
            raise AuthError("invalid_email_request")
        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = recipient
        message["Subject"] = "ChannelShift 이메일 인증"
        message.set_content(
            f"ChannelShift 회원가입 이메일 인증입니다.\n\n계정명: {username}\n"
            "직접 가입하지 않았다면 이 메일을 무시하세요. 누구에게도 인증 링크를 전달하지 마세요.\n"
            "링크는 30분 동안 한 번만 사용할 수 있습니다. 인증 화면에서 사용할 비밀번호를 다시 설정합니다.\n\n"
            f"{self.base_url}/login#verify={token}\n\n"
            "이메일 인증만으로 조직 권한이나 기존 프로젝트 접근 권한이 부여되지는 않습니다.\n"
        )
        try:
            password = _read_password(self.password_file)
            context = ssl.create_default_context()
            if self.mode == "smtps":
                with smtplib.SMTP_SSL(self.host, self.port, timeout=15, context=context) as smtp:
                    smtp.login(self.username, password)
                    smtp.send_message(message)
            else:
                with smtplib.SMTP(self.host, self.port, timeout=15) as smtp:
                    smtp.ehlo()
                    smtp.starttls(context=context)
                    smtp.ehlo()
                    smtp.login(self.username, password)
                    smtp.send_message(message)
        except Exception:
            raise AuthError("email_delivery_failed") from None


def from_environment(base_url):
    """None means unconfigured. Partial or unsafe configuration is rejected."""
    keys = ("HOST", "PORT", "FROM", "USER", "PASSWORD_FILE", "MODE")
    values = {key: os.environ.get("CHANNELSHIFT_SMTP_" + key, "") for key in keys}
    if not any(values.values()):
        return None
    if not all(values[key] for key in ("HOST", "FROM", "USER", "PASSWORD_FILE")):
        raise AuthError("invalid_smtp_configuration")
    mode = values["MODE"] or "starttls"
    try:
        port = int(values["PORT"] or ("465" if mode == "smtps" else "587"))
    except ValueError:
        raise AuthError("invalid_smtp_configuration") from None
    return SMTPMailer(values["HOST"], port, values["FROM"], values["USER"],
                      values["PASSWORD_FILE"], mode, base_url)
