import asyncio
import logging

import resend

from app.core.config import settings

logger = logging.getLogger(__name__)
OTP_EMAIL_SUBJECT = "Mneme OTP"


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

        if settings.DEBUG:
            logger.info("DEBUG mode: skipping actual email sending to %s", to)
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
                logger.error("ResendError sending email to %s: %s", to, e)
        except Exception as e:
            logger.error("Failed to send email to %s: %s", to, e)

    async def send_email(
        self,
        to: str,
        subject: str = OTP_EMAIL_SUBJECT,
        text: str = "test",
    ) -> None:
        await asyncio.to_thread(self._send_email, to, subject, text)

    async def send_otp_email(self, to: str, otp_code: str) -> None:
        await self.send_email(
            to,
            OTP_EMAIL_SUBJECT,
            f"Your OTP code is {otp_code}",
        )
