import asyncio
import logging

import resend

from app.core.config import settings

logger = logging.getLogger(__name__)
OTP_EMAIL_SUBJECT = "Mneme OTP"


def normalize_email(email: str) -> str:
    """Canonical form of an address: identity, rate-limit and lockout keys.

    Every write and every lookup must go through this so `Foo@x.com` and
    `foo@x.com` can never become two users (enforced by the unique index on
    lower(email) — see the email-normalization migration).
    """
    return email.strip().lower()


class EmailService:
    def __init__(self) -> None:
        if settings.RESEND_API_KEY:
            resend.api_key = settings.RESEND_API_KEY

    def _send_email(
        self,
        to: str,
        subject: str = OTP_EMAIL_SUBJECT,
        text: str = "test",
    ) -> None:
        if not settings.RESEND_API_KEY:
            logger.warning("RESEND_API_KEY unset; skipping email to %s", to)
            return

        if settings.DEBUG and not settings.is_production:
            logger.info("DEBUG mode: skipping actual email sending to %s. Content: %s", to, text)
            return

        try:
            resend.Emails.send(
                {
                    "from": settings.EMAIL_FROM,
                    "to": [to],
                    "subject": subject,
                    "text": text,
                }
            )
        except resend.exceptions.ResendError as e:
            if "testing emails" in str(e).lower():
                logger.warning(
                    "Resend test mode: OTP to %s. Configure verified domain in production. Error: %s",
                    to,
                    e,
                )
            else:
                logger.error("ResendError sending email to %s", to)
                logger.exception("ResendError")
        except Exception as e:
            logger.error("Failed to send email to %s", to)
            logger.exception("Failed to send email")

    async def send_email(
        self,
        to: str,
        subject: str = OTP_EMAIL_SUBJECT,
        text: str = "test",
    ) -> None:
        await asyncio.to_thread(self._send_email, to, subject, text)

    async def send_otp_email(self, to: str, otp_code: str) -> None:
        # Never log codes in production, even if the settings check were bypassed
        if settings.DEBUG and not settings.is_production:
            logger.info("[DEV/DEBUG OTP] Recipient: %s | OTP Code: %s", to, otp_code)
        await self.send_email(
            to,
            OTP_EMAIL_SUBJECT,
            f"Your OTP code is {otp_code}",
        )

