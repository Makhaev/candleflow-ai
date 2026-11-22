from alembic import context

from candleflow import chat_models, models  # noqa: F401
from candleflow.db import engine

if context.is_offline_mode():
    context.configure(url=engine.url, target_metadata=models.Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=models.Base.metadata)
        with context.begin_transaction():
            context.run_migrations()
