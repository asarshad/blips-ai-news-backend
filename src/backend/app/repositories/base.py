"""
Base repository class providing common CRUD operations.
All repositories should inherit from this base class.
"""

from typing import TypeVar, Generic, Type, Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.db.base import Base

# Generic type for SQLAlchemy models
ModelType = TypeVar("ModelType", bound=Base)


class BaseRepository(Generic[ModelType]):
    """
    Base repository with common database operations.
    
    Provides basic CRUD operations that can be inherited by specific repositories.
    """
    
    def __init__(self, db: Session, model: Type[ModelType]):
        """
        Initialize repository with database session and model class.
        
        Args:
            db: SQLAlchemy database session
            model: SQLAlchemy model class
        """
        self.db = db
        self.model = model
    
    def get_by_id(self, id: int) -> Optional[ModelType]:
        """Get a single record by ID."""
        return self.db.query(self.model).filter(self.model.id == id).first()
    
    def get_all(self, skip: int = 0, limit: int = 100) -> List[ModelType]:
        """Get all records with pagination."""
        return self.db.query(self.model).offset(skip).limit(limit).all()
    
    def get_recent(self, limit: int = 10, order_by: str = "created_at") -> List[ModelType]:
        """Get most recent records ordered by a column."""
        order_column = getattr(self.model, order_by, self.model.created_at)
        return self.db.query(self.model).order_by(desc(order_column)).limit(limit).all()
    
    def create(self, obj_data: dict) -> ModelType:
        """Create a new record."""
        db_obj = self.model(**obj_data)
        self.db.add(db_obj)
        self.db.commit()
        self.db.refresh(db_obj)
        return db_obj
    
    def update(self, id: int, obj_data: dict) -> Optional[ModelType]:
        """Update an existing record."""
        db_obj = self.get_by_id(id)
        if db_obj:
            for key, value in obj_data.items():
                setattr(db_obj, key, value)
            self.db.commit()
            self.db.refresh(db_obj)
        return db_obj
    
    def delete(self, id: int) -> bool:
        """Delete a record by ID."""
        db_obj = self.get_by_id(id)
        if db_obj:
            self.db.delete(db_obj)
            self.db.commit()
            return True
        return False
    
    def exists(self, id: int) -> bool:
        """Check if a record exists by ID."""
        return self.db.query(self.model).filter(self.model.id == id).first() is not None
