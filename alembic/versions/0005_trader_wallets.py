from alembic import op
import sqlalchemy as sa

revision = "0005_trader_wallets"
down_revision = "0004_snapshot_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("traders", sa.Column("wallets", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("traders", "wallets")
