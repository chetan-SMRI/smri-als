import secrets

from .config import Settings


def valid_credentials(username: str, password: str, settings: Settings) -> bool:
    return secrets.compare_digest(username.encode(), settings.admin_username.encode()) and secrets.compare_digest(
        password.encode(), settings.admin_password.encode()
    )

