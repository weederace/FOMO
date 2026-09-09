from alembic import op

revision = "0004_snapshot_indexes"
down_revision = "0003_live_pipeline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_trader_snapshots_trader_captured", "trader_snapshots", ["trader_id", "captured_at"], if_not_exists=True)
    op.create_index("ix_trader_snapshots_rank_captured", "trader_snapshots", ["rank", "captured_at"], if_not_exists=True)


def downgrade() -> None:
    op.drop_index("ix_trader_snapshots_rank_captured", table_name="trader_snapshots")
    op.drop_index("ix_trader_snapshots_trader_captured", table_name="trader_snapshots")
