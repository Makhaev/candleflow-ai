from candleflow.api import app
from candleflow.chat_api import router

app.include_router(router)
