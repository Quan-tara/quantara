from db.database import engine, Base
import db.models

def init_db():
    Base.metadata.create_all(bind=engine)
    print("Database connected + tables created")

if __name__ == "__main__":
    init_db()