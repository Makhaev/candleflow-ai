import ipaddress
import os
import socket

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

if url := os.getenv("TEST_DATABASE_URL"):
    os.environ["DATABASE_URL"] = url
os.environ["AI_PROVIDER"] = "fake"
os.environ["OPENAI_API_KEY"] = ""
os.environ["RATE_LIMITS"] = "false"

from candleflow.auth import password_hash
from candleflow.db import SessionLocal, engine
from candleflow.models import Base, User


@pytest.fixture(scope="session", autouse=True)
def restrict_network():
    """Allow only the configured private test database and local event-loop sockets."""
    original_getaddrinfo = socket.getaddrinfo
    db_host = engine.url.host if os.getenv("TEST_DATABASE_URL") else None
    db_port = engine.url.port or 5432
    addresses = set()
    if db_host:
        addresses = {entry[4][0] for entry in original_getaddrinfo(db_host, db_port, type=socket.SOCK_STREAM)}
        if any(ipaddress.ip_address(address).is_global for address in addresses):
            pytest.fail("Use a private or loopback PostgreSQL address in TEST_DATABASE_URL", pytrace=False)

    def is_local(host):
        if host is None or str(host).lower() == "localhost":
            return True
        try:
            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    def checked_getaddrinfo(host, port, *args, **kwargs):
        if not is_local(host) and host != db_host:
            raise AssertionError("External DNS is disabled in tests")
        return original_getaddrinfo(host, port, *args, **kwargs)

    def checked_connection(operation):
        def connect(sock, address):
            if sock.family in (socket.AF_INET, socket.AF_INET6):
                host, port, *_ = address
                if not is_local(host) and not (host in addresses and port == db_port):
                    raise AssertionError("External TCP is disabled in tests")
            return operation(sock, address)

        return connect

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(socket, "getaddrinfo", checked_getaddrinfo)
        patch.setattr(socket.socket, "connect", checked_connection(socket.socket.connect))
        patch.setattr(socket.socket, "connect_ex", checked_connection(socket.socket.connect_ex))
        yield


@pytest.fixture(scope="session")
def postgres(restrict_network):
    if not os.getenv("TEST_DATABASE_URL"):
        pytest.fail("Set TEST_DATABASE_URL to a dedicated PostgreSQL test database", pytrace=False)
    if engine.url.get_backend_name() != "postgresql" or not (engine.url.database or "").endswith("_test"):
        pytest.fail("Use a dedicated PostgreSQL database whose name ends in _test", pytrace=False)
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture
def empty_db(postgres):
    with SessionLocal.begin() as connection:
        names = ", ".join(Base.metadata.tables)
        connection.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))


@pytest.fixture
def db(empty_db):
    with SessionLocal.begin() as connection:
        for role, name in (("manager", "Орлова Елена"), ("storekeeper", "Петров Иван")):
            connection.add(
                User(
                    login="warehouse" if role == "storekeeper" else role,
                    role=role,
                    full_name=name,
                    password_hash=password_hash("testpass"),
                )
            )
    with SessionLocal() as session:
        yield session
