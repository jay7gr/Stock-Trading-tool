"""
Notification system — Telegram and email alerts.

Sends trade alerts, daily summaries, and risk warnings.
"""

import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

import config


class TelegramNotifier:
    """Send messages via Telegram Bot API. Free, instant."""

    def __init__(self, bot_token: Optional[str] = None,
                 chat_id: Optional[str] = None):
        self.bot_token = bot_token or config.TELEGRAM_BOT_TOKEN
        self.chat_id = chat_id or config.TELEGRAM_CHAT_ID
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}" if self.bot_token else None

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def send(self, message: str, parse_mode: str = "HTML") -> bool:
        if not self.enabled:
            return False
        try:
            resp = requests.post(
                f"{self.base_url}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": message,
                    "parse_mode": parse_mode,
                },
                timeout=10,
            )
            return resp.status_code == 200
        except Exception as e:
            print(f"[Telegram] Error: {e}")
            return False

    def send_trade_alert(self, ticker: str, action: str, price: float,
                         size_gbp: float, score: float, reasoning: str,
                         stop_loss: float, take_profit: float):
        icon = "🟢" if action == "BUY" else "🔴"
        msg = (
            f"{icon} <b>{action} {ticker}</b>\n\n"
            f"Price: £{price:,.2f}\n"
            f"Size: £{size_gbp:,.2f}\n"
            f"Score: {score:.1f}/10\n"
            f"Stop: £{stop_loss:,.2f} | Target: £{take_profit:,.2f}\n\n"
            f"<i>{reasoning[:500]}</i>"
        )
        self.send(msg)

    def send_daily_summary(self, portfolio_value: float, daily_pnl: float,
                           total_pnl: float, trades_today: int,
                           target_pct: float):
        pnl_icon = "📈" if daily_pnl >= 0 else "📉"
        msg = (
            f"{pnl_icon} <b>Daily Summary</b>\n\n"
            f"Portfolio: £{portfolio_value:,.2f}\n"
            f"Today's P&L: £{daily_pnl:+,.2f}\n"
            f"Total P&L: £{total_pnl:+,.2f}\n"
            f"Trades today: {trades_today}\n"
            f"Target attainment: {target_pct:.0f}%"
        )
        self.send(msg)

    def send_risk_alert(self, message: str):
        self.send(f"⚠️ <b>RISK ALERT</b>\n\n{message}")

    def send_stop_hit(self, ticker: str, pnl: float, reason: str):
        self.send(
            f"🛑 <b>STOP HIT: {ticker}</b>\n\n"
            f"P&L: £{pnl:+,.2f}\n"
            f"Reason: {reason}"
        )


class EmailNotifier:
    """Send notifications via email. Free with Gmail app passwords."""

    def __init__(self, smtp_server: str = "smtp.gmail.com",
                 smtp_port: int = 587,
                 sender_email: str = "",
                 sender_password: str = "",
                 recipient_email: str = ""):
        self.smtp_server = smtp_server
        self.smtp_port = smtp_port
        self.sender_email = sender_email
        self.sender_password = sender_password
        self.recipient_email = recipient_email

    @property
    def enabled(self) -> bool:
        return bool(self.sender_email and self.sender_password and self.recipient_email)

    def send(self, subject: str, body: str) -> bool:
        if not self.enabled:
            return False
        try:
            msg = MIMEMultipart()
            msg["From"] = self.sender_email
            msg["To"] = self.recipient_email
            msg["Subject"] = f"[Trading Bot] {subject}"
            msg.attach(MIMEText(body, "plain"))

            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.sender_email, self.sender_password)
                server.sendmail(self.sender_email, self.recipient_email, msg.as_string())
            return True
        except Exception as e:
            print(f"[Email] Error: {e}")
            return False


class Notifier:
    """Unified notifier — sends to all configured channels."""

    def __init__(self):
        self.telegram = TelegramNotifier()
        self.email = EmailNotifier()

    def trade_alert(self, ticker: str, action: str, price: float,
                    size_gbp: float, score: float, reasoning: str,
                    stop_loss: float, take_profit: float):
        if self.telegram.enabled:
            self.telegram.send_trade_alert(
                ticker, action, price, size_gbp, score,
                reasoning, stop_loss, take_profit,
            )
        if self.email.enabled:
            self.email.send(
                f"{action} {ticker} @ £{price:.2f}",
                f"Score: {score:.1f}/10\nSize: £{size_gbp:.2f}\n"
                f"Stop: £{stop_loss:.2f} | Target: £{take_profit:.2f}\n\n"
                f"{reasoning}",
            )

    def daily_summary(self, portfolio_value: float, daily_pnl: float,
                      total_pnl: float, trades_today: int, target_pct: float):
        if self.telegram.enabled:
            self.telegram.send_daily_summary(
                portfolio_value, daily_pnl, total_pnl, trades_today, target_pct,
            )

    def risk_alert(self, message: str):
        if self.telegram.enabled:
            self.telegram.send_risk_alert(message)

    def stop_hit(self, ticker: str, pnl: float, reason: str):
        if self.telegram.enabled:
            self.telegram.send_stop_hit(ticker, pnl, reason)
