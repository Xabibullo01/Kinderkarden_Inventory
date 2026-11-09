from datetime import datetime
from fastapi import HTTPException
import logging

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from math import floor
import redis.asyncio as redis

from .config import INVENTORY_CHANNEL, REDIS_URL

from .models import (
    Product, Meal, MealIngredient, MealServing, InventoryLog
)
from .schemas import ProductCreate, MealCreate, MealServingCreate

logger = logging.getLogger(__name__)


async def create_product(db: AsyncSession, product: ProductCreate, user_id: int):
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
        user_id=user_id,
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


async def update_product(db: AsyncSession, product_id: int, product: ProductCreate, user_id: int):
    result = await db.execute(select(Product).where(Product.id == product_id))
    db_product = result.scalars().first()
    if not db_product:
        return None
    old_quantity = db_product.quantity
    for key, value in product.model_dump(exclude_none=True).items():
        setattr(db_product, key, value)
    if old_quantity != db_product.quantity:
        db.add(InventoryLog(
            product_id=product_id,
            change_type="delivery" if db_product.quantity > old_quantity else "adjustment",
            quantity=abs(db_product.quantity - old_quantity),
            timestamp=datetime.utcnow(),
            user_id=user_id
        ))
    await db.commit()
    await db.refresh(db_product)
    return db_product


async def delete_product(db: AsyncSession, product_id: int, user_id: int):
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
        user_id=user_id
    ))
    await db.delete(db_product)
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
        delete(MealIngredient).where(MealIngredient.meal_id == meal_id)
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
        delete(MealIngredient).where(MealIngredient.meal_id == meal_id)
    )
    await db.delete(db_meal)
    await db.commit()
    return db_meal


async def serve_meal(db: AsyncSession, meal_serving: MealServingCreate, user_id: int):
    r = await db.execute(select(MealIngredient).where(MealIngredient.meal_id == meal_serving.meal_id))
    ingredients = r.scalars().all()
    if not ingredients:
        raise HTTPException(status_code=404, detail="Meal has no ingredients defined")

    # Lock the product rows (SELECT ... FOR UPDATE) so two cooks serving at the same
    # time cannot both read the same stock and push it below zero.
    product_ids = [i.product_id for i in ingredients]
    res = await db.execute(
        select(Product).where(Product.id.in_(product_ids)).with_for_update()
    )
    products = {p.id: p for p in res.scalars().all()}

    # Validate everything first, then change anything (all-or-nothing).
    for ingredient in ingredients:
        product = products.get(ingredient.product_id)
        if not product or product.quantity < ingredient.quantity:
            name = product.name if product else "product"  # read before rollback expires it
            await db.rollback()  # release the row locks
            raise HTTPException(status_code=400, detail=f"Insufficient quantity for {name}")

    now = datetime.utcnow()
    messages = []
    for ingredient in ingredients:
        product = products[ingredient.product_id]
        product.quantity -= ingredient.quantity
        db.add(InventoryLog(
            product_id=ingredient.product_id,
            change_type="consumption",
            quantity=ingredient.quantity,
            timestamp=now,
            user_id=user_id,
        ))
        messages.append(f"Inventory updated: {ingredient.quantity}g of {ingredient.product_id}")

    serving = MealServing(meal_id=meal_serving.meal_id, user_id=user_id, timestamp=now)
    db.add(serving)
    await db.commit()
    await db.refresh(serving)

    # Publish only AFTER the commit succeeded, otherwise listeners could see
    # updates that were rolled back.
    await publish_inventory_updates(messages)
    return serving


async def publish_inventory_updates(messages: list[str]) -> None:
    """Best-effort notification: a Redis outage must not break serving a meal."""
    if not messages:
        return
    try:
        client = redis.from_url(REDIS_URL, encoding="utf-8", decode_responses=True)
        try:
            for message in messages:
                await client.publish(INVENTORY_CHANNEL, message)
        finally:
            await client.aclose()
    except Exception:
        logger.warning("Could not publish inventory updates to Redis", exc_info=True)


async def get_portion_estimates(db: AsyncSession):
    # 3 queries in total instead of 1 + N + N*M (the classic N+1 problem).
    meals = (await db.execute(select(Meal))).scalars().all()
    ingredients = (await db.execute(select(MealIngredient))).scalars().all()
    products = {p.id: p for p in (await db.execute(select(Product))).scalars().all()}

    by_meal: dict[int, list[MealIngredient]] = {}
    for ingredient in ingredients:
        by_meal.setdefault(ingredient.meal_id, []).append(ingredient)

    estimates = []
    for meal in meals:
        meal_ingredients = by_meal.get(meal.id, [])
        if not meal_ingredients:
            estimates.append({"meal_id": meal.id, "name": meal.name, "portions": 0})
            continue
        portions = None
        for ingredient in meal_ingredients:
            product = products.get(ingredient.product_id)
            if product is None or ingredient.quantity <= 0:
                avail = 0
            else:
                avail = floor(product.quantity / ingredient.quantity)
            portions = avail if portions is None else min(portions, avail)
        estimates.append({"meal_id": meal.id, "name": meal.name, "portions": portions or 0})
    return estimates
