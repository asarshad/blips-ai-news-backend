
"""Initial migration

Revision ID: e021dbf2e9df
Revises: 
Create Date: 2023-04-10 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e021dbf2e9df'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create articles table
    op.create_table('articles',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(), nullable=True),
        sa.Column('source_url', sa.String(), nullable=True),
        sa.Column('content', sa.Text(), nullable=True),
        sa.Column('summary', sa.Text(), nullable=True),
        sa.Column('image_url', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_articles_id'), 'articles', ['id'], unique=False)
    op.create_index(op.f('ix_articles_title'), 'articles', ['title'], unique=False)
    
    # Create tags table
    op.create_table('tags',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_tags_id'), 'tags', ['id'], unique=False)
    op.create_index(op.f('ix_tags_name'), 'tags', ['name'], unique=True)
    
    # Create article_tag association table
    op.create_table('article_tag',
        sa.Column('article_id', sa.Integer(), nullable=True),
        sa.Column('tag', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(['article_id'], ['articles.id'], ),
    )
    
    # Create conversations table
    op.create_table('conversations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('article_id', sa.Integer(), nullable=True),
        sa.Column('message', sa.Text(), nullable=True),
        sa.Column('sender', sa.String(), nullable=True),
        sa.Column('timestamp', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['article_id'], ['articles.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_conversations_id'), 'conversations', ['id'], unique=False)
    
    # Create usage table
    op.create_table('usage',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('device_id', sa.String(), nullable=True),
        sa.Column('article_id', sa.Integer(), nullable=True),
        sa.Column('used_tokens', sa.Integer(), nullable=True),
        sa.Column('message_count', sa.Integer(), nullable=True),
        sa.Column('timestamp', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['article_id'], ['articles.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_usage_device_id'), 'usage', ['device_id'], unique=False)
    op.create_index(op.f('ix_usage_id'), 'usage', ['id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_usage_id'), table_name='usage')
    op.drop_index(op.f('ix_usage_device_id'), table_name='usage')
    op.drop_table('usage')
    
    op.drop_index(op.f('ix_conversations_id'), table_name='conversations')
    op.drop_table('conversations')
    
    op.drop_table('article_tag')
    
    op.drop_index(op.f('ix_tags_name'), table_name='tags')
    op.drop_index(op.f('ix_tags_id'), table_name='tags')
    op.drop_table('tags')
    
    op.drop_index(op.f('ix_articles_title'), table_name='articles')
    op.drop_index(op.f('ix_articles_id'), table_name='articles')
    op.drop_table('articles')
