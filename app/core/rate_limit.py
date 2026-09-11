from fastapi import FastAPI
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

# Default limiter using the client's IP address
limiter = Limiter(key_func=get_remote_address, default_limits=["100/minute"])


def setup_rate_limiting(app: FastAPI) -> None:
    """Configures rate limiting for the FastAPI app."""
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
