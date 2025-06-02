from pydantic import BaseModel, ConfigDict, Field
from datetime import datetime
from typing import List, Optional

VALID_ROLES = {"admin", "cook", "manager"}

class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=50, pattern=r'^[a-zA-Z0-9_]+$')
    password: str = Field(min_length=6)
    role: str

class UserLogin(BaseModel):
    username: str
    password: str

class ProductBase(BaseModel):
    name: str
    quantity: float
    threshold: float

class ProductCreate(ProductBase):
    name: str
    quantity: float = Field(ge=0)
    threshold: float = Field(ge=0)
    delivery_date: Optional[datetime] = None

class ProductSchema(ProductBase):
    id: int
    delivery_date: datetime

    model_config = ConfigDict(from_attributes=True)

# ✅ Alias qo‘shildi:
Product = ProductSchema

class MealBase(BaseModel):
    name: str

class MealSchema(MealBase):
    id: int

    model_config = ConfigDict(from_attributes=True)

class MealIngredientBase(BaseModel):
    meal_id: int
    product_id: int
    quantity: float = Field(ge=0)

class MealIngredient(MealIngredientBase):
    model_config = ConfigDict(from_attributes=True)

class MealCreate(MealBase):
    ingredients: List[MealIngredient]

class MealServingBase(BaseModel):
    meal_id: int

class MealServingCreate(MealServingBase):
    pass

class MealServingSchema(MealServingBase):
    id: int
    user_id: int
    timestamp: datetime

    model_config = ConfigDict(from_attributes=True)

class InventoryLogBase(BaseModel):
    product_id: int
    change_type: str
    quantity: float

class InventoryLogCreate(InventoryLogBase):
    pass

class InventoryLog(InventoryLogBase):
    id: int
    user_id: int
    timestamp: datetime

    model_config = ConfigDict(from_attributes=True)

class PortionEstimate(BaseModel):
    meal_id: int
    name: str
    portions: int
