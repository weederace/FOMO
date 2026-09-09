from alembic import op

revision = "0002_score_history_index"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_trader_scores_trader_calculated",
        "trader_scores",
        ["trader_id", "calculated_at"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_trader_scores_trader_calculated", table_name="trader_scores")
