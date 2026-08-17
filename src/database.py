from sqlalchemy import Engine, create_engine


def create_database_engine(database_url: str) -> Engine:
    return create_engine(database_url)
