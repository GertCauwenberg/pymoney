"""Sending the two emails the server ever sends: activation and password reset.

One protocol, four backends, chosen by ``PYMONEY_MAIL_BACKEND``:

``console``
    Prints the message. The default, so a development server works with no
    mail setup and the link is right there in the log.
``smtp``
    ``smtplib`` against ``PYMONEY_SMTP_HOST`` et al., STARTTLS unless told not to.
``file``
    Appends one JSON line per message to ``PYMONEY_MAIL_FILE``. For the browser
    tests, which need to read the link the way a person would read their inbox.
``memory``
    Keeps the messages in a list. For the unit tests.

Messages are plain text. A link in an activation mail is a thing to click, not
a thing to style.
"""

from __future__ import annotations

import json
import smtplib
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Protocol

__all__ = [
    "ConsoleMailer",
    "FileMailer",
    "Mailer",
    "MemoryMailer",
    "Message",
    "SmtpMailer",
    "mailer_from_settings",
]


@dataclass(frozen=True, slots=True)
class Message:
    to: str
    subject: str
    body: str


class Mailer(Protocol):
    def send(self, message: Message) -> None: ...


class ConsoleMailer:
    def send(self, message: Message) -> None:
        print(
            f"pymoney: mail to {message.to}: {message.subject}\n{message.body}\n",
            file=sys.stderr,
        )


class MemoryMailer:
    def __init__(self) -> None:
        self.sent: list[Message] = []

    def send(self, message: Message) -> None:
        self.sent.append(message)


class FileMailer:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def send(self, message: Message) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {**asdict(message), "at": datetime.now(timezone.utc).isoformat()}
                )
                + "\n"
            )


class SmtpMailer:
    def __init__(
        self,
        host: str,
        port: int = 587,
        *,
        username: str | None = None,
        password: str | None = None,
        sender: str = "pymoney@localhost",
        starttls: bool = True,
        timeout: float = 20.0,
    ) -> None:
        self.host, self.port = host, port
        self.username, self.password = username, password
        self.sender, self.starttls, self.timeout = sender, starttls, timeout

    def send(self, message: Message) -> None:
        mail = EmailMessage()
        mail["From"], mail["To"], mail["Subject"] = (
            self.sender,
            message.to,
            message.subject,
        )
        mail.set_content(message.body)
        with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as smtp:
            if self.starttls:
                smtp.starttls()
            if self.username:
                smtp.login(self.username, self.password or "")
            smtp.send_message(mail)


def mailer_from_settings(settings) -> Mailer:
    backend = settings.mail_backend
    if backend == "console":
        return ConsoleMailer()
    if backend == "memory":
        return MemoryMailer()
    if backend == "file":
        return FileMailer(settings.mail_file)
    if backend == "smtp":
        if not settings.smtp_host:
            raise ValueError("PYMONEY_MAIL_BACKEND=smtp needs PYMONEY_SMTP_HOST")
        return SmtpMailer(
            settings.smtp_host,
            settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password,
            sender=settings.mail_from,
            starttls=settings.smtp_starttls,
        )
    raise ValueError(
        f"unknown mail backend {backend!r}; one of console, smtp, file, memory"
    )
