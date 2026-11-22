import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from candleflow.models import LoginSession, User


class DomainError(Exception):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def password_hash(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
    return f"{salt}:{digest.hex()}"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def login(db: Session, username: str, password: str) -> tuple[User, str]:
    user = db.scalar(select(User).where(User.login == username))
    expected = user.password_hash if user else "0" * 32 + ":" + "0" * 128
    if not hmac.compare_digest(password_hash(password, expected.split(":")[0]), expected) or user is None:
        raise DomainError("Неверный логин или пароль", 401)
    token = secrets.token_urlsafe(32)
    db.add(
        LoginSession(
            token_hash=token_hash(token), user_id=user.id, expires_at=datetime.now(UTC) + timedelta(hours=12)
        )
    )
    return user, token


def authenticate(db: Session, token: str | None) -> User:
    entry = db.get(LoginSession, token_hash(token)) if token else None
    if not entry or entry.expires_at <= datetime.now(UTC):
        raise DomainError("Войдите в систему", 401)
    user = db.get(User, entry.user_id)
    if user is None:
        raise DomainError("Войдите в систему", 401)
    return user


def require(user: User, *roles: str) -> None:
    if user.role not in roles:
        raise DomainError("Недостаточно прав для этого действия", 403)
