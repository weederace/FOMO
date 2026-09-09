from alembic import op
import sqlalchemy as sa

revision = "0003_live_pipeline"
down_revision = "0002_score_history_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    snapshot_columns = {column["name"] for column in inspector.get_columns("trader_snapshots")}
    if "source" not in snapshot_columns:
        op.add_column("trader_snapshots", sa.Column("source", sa.String(50), server_default="unknown"))
    trade_columns = {column["name"] for column in inspector.get_columns("trades")}
    for name, column in [
        ("quantity", sa.Numeric(30, 12)),
        ("status", sa.String(20)),
        ("realized_pnl_usd", sa.Numeric(20, 6)),
        ("unrealized_pnl_usd", sa.Numeric(20, 6)),
        ("holding_duration_seconds", sa.Integer()),
        ("transaction_hash", sa.String(255)),
        ("trade_key", sa.String(500)),
    ]:
        if name not in trade_columns:
            op.add_column("trades", sa.Column(name, column, nullable=True))
    op.create_index("ix_trades_trader_executed", "trades", ["trader_id", "executed_at"], if_not_exists=True)
    op.create_index("uq_trades_trade_key", "trades", ["trade_key"], unique=True, if_not_exists=True)
    if "balance_snapshots" not in inspector.get_table_names():
        op.create_table(
            "balance_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("trader_id", sa.Integer(), sa.ForeignKey("traders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_symbol", sa.String(100)), sa.Column("token_address", sa.String(255)),
        sa.Column("chain", sa.String(100)), sa.Column("amount", sa.Numeric(30, 12)),
        sa.Column("value_usd", sa.Numeric(20, 6)), sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("balance_key", sa.String(600), nullable=False, unique=True),
        )
    op.create_index("ix_balances_trader_captured", "balance_snapshots", ["trader_id", "captured_at"], if_not_exists=True)
    if "collection_runs" not in inspector.get_table_names():
        op.create_table(
            "collection_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("provider", sa.String(50), nullable=False), sa.Column("status", sa.String(30), nullable=False),
        sa.Column("traders_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("trades_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("errors", sa.JSON()), sa.Column("duration_ms", sa.Integer()),
        )


def downgrade() -> None:
    op.drop_table("collection_runs")
    op.drop_table("balance_snapshots")
    op.drop_index("uq_trades_trade_key", table_name="trades")
    op.drop_index("ix_trades_trader_executed", table_name="trades")
    for name in ("trade_key", "transaction_hash", "holding_duration_seconds", "unrealized_pnl_usd", "realized_pnl_usd", "status", "quantity"):
        op.drop_column("trades", name)
    op.drop_column("trader_snapshots", "source")
