import os

import pytest
import sqlalchemy as sa

pytestmark = [pytest.mark.integration]


def test_alembic_version_table_exists_and_has_value():
    engine = sa.create_engine(os.environ["DATABASE_URL"])
    try:
        with engine.connect() as conn:
            version = conn.execute(sa.text("select version_num from alembic_version")).scalar_one()
            assert isinstance(version, str)
            assert version.strip() != ""
    finally:
        engine.dispose()
