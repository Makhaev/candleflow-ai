import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from candleflow.models import LoginSession, User

SESSION_TTL = timedelta(hours=12)


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


# Hash of an unused password: unknown logins still pay the full scrypt cost, so response
# time does not reveal which logins exist.
DUMMY_HASH = "0" * 32 + ":" + "0" * 128


def find_user(db: Session, username: str) -> User | None:
    return db.scalar(select(User).where(User.login == username))


def verify_password(user: User | None, password: str) -> bool:
    """CPU-heavy (scrypt). The API runs it in a worker thread to keep the event loop free."""
    expected = user.password_hash if user else DUMMY_HASH
    return hmac.compare_digest(password_hash(password, expected.split(":")[0]), expected) and user is not None


def open_session(db: Session, user: User) -> str:
    token = secrets.token_urlsafe(32)
    db.add(
        LoginSession(
            token_hash=token_hash(token), user_id=user.id, expires_at=datetime.now(UTC) + SESSION_TTL
        )
    )
    return token


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
