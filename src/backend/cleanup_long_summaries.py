"""
Script to remove articles with summaries longer than 65 words.
Run from the backend directory: python cleanup_long_summaries.py
"""

import sys
import os

# Add the app directory to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from app.core.config import settings
from app.models.article import Article

def count_words(text: str) -> int:
    """Count words in a text string."""
    if not text:
        return 0
    return len(text.split())

def exceeds_limit(text: str, max_words: int = 90, max_chars: int = 540) -> bool:
    """Check if text exceeds both word and character limits."""
    if not text:
        return False
    return len(text.split()) > max_words and len(text) > max_chars

def main():
    # Create database connection
    engine = create_engine(settings.DATABASE_URL)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    
    try:
        # Get all articles
        articles = db.query(Article).all()
        
        print(f"Total articles in database: {len(articles)}")
        
        # Find articles with summaries > 90 words AND > 540 chars
        articles_to_delete = []
        for article in articles:
            word_count = count_words(article.summary)
            char_count = len(article.summary) if article.summary else 0
            if exceeds_limit(article.summary):
                articles_to_delete.append((article, word_count, char_count))
        
        print(f"Articles with summaries > 90 words AND > 540 chars: {len(articles_to_delete)}")
        
        if not articles_to_delete:
            print("No articles to delete.")
            return
        
        # Show articles that will be deleted
        print("\nArticles to be deleted:")
        print("-" * 80)
        for article, word_count, char_count in articles_to_delete:
            print(f"ID: {article.id} | Words: {word_count} | Chars: {char_count} | Title: {article.title[:50]}...")
        print("-" * 80)
        
        # Confirm deletion
        confirm = input(f"\nDelete {len(articles_to_delete)} articles? (yes/no): ")
        
        if confirm.lower() == 'yes':
            for article, _, _ in articles_to_delete:
                db.delete(article)
            db.commit()
            print(f"\nDeleted {len(articles_to_delete)} articles successfully.")
        else:
            print("Deletion cancelled.")
            
    except Exception as e:
        print(f"Error: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    main()
