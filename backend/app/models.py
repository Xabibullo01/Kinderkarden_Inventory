# app/models.py

from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from .database import Base

class User(Base):
    __tablename__ = "users"
    id            = Column(Integer, primary_key=True, index=True)
    username      = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    role          = Column(String, nullable=False)

class Product(Base):
    __tablename__ = "products"
    id            = Column(Integer, primary_key=True, index=True)
    name          = Column(String, unique=True, nullable=False)
    quantity      = Column(Float, nullable=False)
    delivery_date = Column(DateTime, default=datetime.utcnow)
    threshold     = Column(Float, nullable=False)

class Meal(Base):
    __tablename__ = "meals"
    id       = Column(Integer, primary_key=True, index=True)
    name     = Column(String, unique=True, nullable=False)
    servings = relationship("MealServing", back_populates="meal")

class MealIngredient(Base):
    __tablename__ = "meal_ingredients"
    meal_id    = Column(Integer, ForeignKey("meals.id"),    primary_key=True)
    product_id = Column(Integer, ForeignKey("products.id"), primary_key=True)
    quantity   = Column(Float, nullable=False)

class MealServing(Base):
    __tablename__ = "meal_servings"
    id        = Column(Integer, primary_key=True, index=True)
    meal_id   = Column(Integer, ForeignKey("meals.id"), nullable=False)
    user_id   = Column(Integer, ForeignKey("users.id"), nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)

    meal = relationship("Meal", back_populates="servings")
    user = relationship("User")

class InventoryLog(Base):
    __tablename__ = "inventory_logs"
    id          = Column(Integer, primary_key=True, index=True)
    product_id  = Column(Integer, ForeignKey("products.id"), nullable=False)
    change_type = Column(String, nullable=False)
    quantity    = Column(Float, nullable=False)
    timestamp   = Column(DateTime, default=datetime.utcnow)
    user_id     = Column(Integer, ForeignKey("users.id"), nullable=False)
