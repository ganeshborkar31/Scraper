from app.db import models  # noqa: F401
from app.db.session import Base, engine

Base.metadata.create_all(engine)
