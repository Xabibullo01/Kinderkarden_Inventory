from datetime import datetime
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from math import floor
import redis.asyncio as redis

from .models import (
    Product, Meal, MealIngredient, MealServing, InventoryLog
)
from .schemas import ProductCreate, MealCreate, MealServingCreate


async def create_product(db: AsyncSession, product: ProductCreate):
    # Build new Product with naive UTC timestamp
    db_product = Product(
        name=product.name,
        quantity=product.quantity,
        threshold=product.threshold,
        delivery_date=datetime.utcnow(),
    )
    db.add(db_product)
    # Flush to get ID, then log
    await db.flush()
    db.add(InventoryLog(
        product_id=db_product.id,
        change_type="delivery",
        quantity=product.quantity,
        timestamp=datetime.utcnow(),
        user_id=1,
    ))
    # Commit once, catch duplicate-name
    try:
        await db.commit()
    except IntegrityError as e:
        await db.rollback()
        if "products_name_key" in str(e.orig):
            raise HTTPException(status_code=400, detail="A product with that name already exists")
        raise
    await db.refresh(db_product)
    return db_product


async def get_product(db: AsyncSession, product_id: int):
    result = await db.execute(select(Product).where(Product.id == product_id))
    return result.scalars().first()


async def update_product(db: AsyncSession, product_id: int, product: ProductCreate):
    result = await db.execute(select(Product).where(Product.id == product_id))
    db_product = result.scalars().first()
    if not db_product:
        return None
    old_quantity = db_product.quantity
    for key, value in product.dict().items():
        setattr(db_product, key, value)
    if old_quantity != db_product.quantity:
        db.add(InventoryLog(
            product_id=product_id,
            change_type="delivery" if db_product.quantity > old_quantity else "adjustment",
            quantity=abs(db_product.quantity - old_quantity),
            timestamp=datetime.utcnow(),
            user_id=1
        ))
    await db.commit()
    await db.refresh(db_product)
    return db_product


async def delete_product(db: AsyncSession, product_id: int):
    result = await db.execute(select(Product).where(Product.id == product_id))
    db_product = result.scalars().first()
    if not db_product:
        return None
    # log removal with timestamp
    db.add(InventoryLog(
        product_id=product_id,
        change_type="removal",
        quantity=db_product.quantity,
        timestamp=datetime.utcnow(),
        user_id=1
    ))
    db.delete(db_product)
    await db.commit()
    return db_product


async def create_meal(db: AsyncSession, meal: MealCreate):
    db_meal = Meal(name=meal.name)
    db.add(db_meal)
    await db.commit()
    await db.refresh(db_meal)
    for ingredient in meal.ingredients:
        result = await db.execute(select(Product).where(Product.id == ingredient.product_id))
        product = result.scalars().first()
        if not product:
            raise HTTPException(status_code=422, detail=f"Product with id {ingredient.product_id} not found")
        db.add(MealIngredient(
            meal_id=db_meal.id,
            product_id=ingredient.product_id,
            quantity=ingredient.quantity
        ))
    await db.commit()
    return db_meal


async def get_meal(db: AsyncSession, meal_id: int):
    result = await db.execute(select(Meal).where(Meal.id == meal_id))
    return result.scalars().first()


async def update_meal(db: AsyncSession, meal_id: int, meal: MealCreate):
    result = await db.execute(select(Meal).where(Meal.id == meal_id))
    db_meal = result.scalars().first()
    if not db_meal:
        return None
    db_meal.name = meal.name
    # remove old ingredients
    await db.execute(
        select(MealIngredient).where(MealIngredient.meal_id == meal_id).delete()
    )
    for ingredient in meal.ingredients:
        db.add(MealIngredient(
            meal_id=meal_id,
            product_id=ingredient.product_id,
            quantity=ingredient.quantity
        ))
    await db.commit()
    await db.refresh(db_meal)
    return db_meal


async def delete_meal(db: AsyncSession, meal_id: int):
    result = await db.execute(select(Meal).where(Meal.id == meal_id))
    db_meal = result.scalars().first()
    if not db_meal:
        return None
    # prevent deletion if servings exist
    r = await db.execute(select(MealServing).where(MealServing.meal_id == meal_id))
    if r.scalars().first():
        raise HTTPException(status_code=400, detail="Cannot delete meal with existing servings")
    await db.execute(
        select(MealIngredient).where(MealIngredient.meal_id == meal_id).delete()
    )
    db.delete(db_meal)
    await db.commit()
    return db_meal


async def serve_meal(db: AsyncSession, meal_serving: MealServingCreate, user_id: int):
    r = await db.execute(select(MealIngredient).where(MealIngredient.meal_id == meal_serving.meal_id))
    ingredients = r.scalars().all()
    if not ingredients:
        raise HTTPException(status_code=404, detail="Meal has no ingredients defined")

    redis_client = await redis.from_url("redis://redis:6379/0", encoding="utf-8", decode_responses=True)
    try:
        for ingredient in ingredients:
            res = await db.execute(select(Product).where(Product.id == ingredient.product_id))
            product = res.scalars().first()
            if not product or product.quantity < ingredient.quantity:
                raise HTTPException(status_code=400, detail=f"Insufficient quantity for {product.name if product else 'product'}")
            product.quantity -= ingredient.quantity
            db.add(InventoryLog(
                product_id=ingredient.product_id,
                change_type="consumption",
                quantity=ingredient.quantity,
                timestamp=datetime.utcnow(),
                user_id=user_id
            ))
            await redis_client.publish("inventory_updates", f"Inventory updated: {ingredient.quantity}g of {ingredient.product_id}")

        serving = MealServing(meal_id=meal_serving.meal_id, user_id=user_id, timestamp=datetime.utcnow(), )
        db.add(serving)
        await db.commit()
        await db.refresh(serving)
        return serving
    finally:
        await redis_client.aclose()


async def get_portion_estimates(db: AsyncSession):
    result = await db.execute(select(Meal))
    meals = result.scalars().all()
    estimates = []
    for meal in meals:
        r2 = await db.execute(select(MealIngredient).where(MealIngredient.meal_id == meal.id))
        ingredients = r2.scalars().all()
        if not ingredients:
            estimates.append({"meal_id": meal.id, "name": meal.name, "portions": 0})
            continue
        portions = float("inf")
        for ingredient in ingredients:
            r3 = await db.execute(select(Product).where(Product.id == ingredient.product_id))
            product = r3.scalars().first()
            if product:
                avail = floor(product.quantity / ingredient.quantity) if ingredient.quantity > 0 else 0
                portions = min(portions, avail)
        estimates.append({"meal_id": meal.id, "name": meal.name, "portions": int(portions)})
    return estimates
