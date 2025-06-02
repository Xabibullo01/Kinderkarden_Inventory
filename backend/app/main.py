import warnings
# suppress the passlib crypt deprecation warning
warnings.filterwarnings(
    "ignore",
    category=DeprecationWarning,
    module="passlib.utils"
)
from sqlalchemy.orm import selectinload
import logging
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi import WebSocket, WebSocketDisconnect
from fastapi.security import OAuth2PasswordRequestForm, OAuth2PasswordBearer
from fastapi.middleware.cors import CORSMiddleware
from jose import jwt, JWTError
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import AsyncSession
from .schemas import Product
from .models import User, InventoryLog, MealServing, Product, Meal
from .auth import (
    verify_password,
    get_password_hash,
    create_access_token,
    get_current_user,
    authenticate_user,
    pwd_context,
)
from .crud import (
    create_product,
    get_product,
    update_product,
    delete_product,
    create_meal,
    get_meal,
    update_meal,
    delete_meal,
    serve_meal,
    get_portion_estimates,
)
from .schemas import (
    Product,
    ProductSchema,
    MealCreate,
    MealSchema,
    UserCreate,
    UserLogin,
    MealServingCreate,
    MealServingSchema,
    PortionEstimate,
    VALID_ROLES,
)
from .database import init_db, get_db
from celery.result import AsyncResult
from sqlalchemy import select
from typing import Optional, List
from datetime import timedelta, datetime, timezone
from dotenv import load_dotenv
import redis.asyncio as redis
import os
from .celery_app import celery_app
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketState

WebSocketTestSession.client_state = WebSocketState.CONNECTED
logger = logging.getLogger(__name__)

# load .env first thing
load_dotenv()

# simple, non‐expression secret loads
SECRET_KEY = os.getenv("SECRET_KEY")
if SECRET_KEY is None:
    raise ValueError("SECRET_KEY environment variable not set")

JWT_SECRET = os.getenv("JWT_SECRET")
if JWT_SECRET is None:
    raise ValueError("JWT_SECRET environment variable not set")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")
ACCESS_TOKEN_EXPIRE_MINUTES = 30

@asynccontextmanager
async def lifespan(app: FastAPI):
    # run your init_db at startup
    await init_db()
    yield
    # nothing special on shutdown

app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def read_root():
    return {"message": "Kindergarten Meal Tracking System"}

@app.post("/register")
async def register(
    form_data: UserCreate, db: AsyncSession = Depends(get_db)
):
    db_user = await db.execute(
        select(User).where(User.username == form_data.username)
    )
    db_user = db_user.scalars().first()
    if db_user:
        raise HTTPException(status_code=400, detail="Username already registered")
    if form_data.role not in VALID_ROLES:
        raise HTTPException(
            status_code=400, detail=f"Invalid role. Must be one of {VALID_ROLES}"
        )
    hashed_password = pwd_context.hash(form_data.password)
    new_user = User(
        username=form_data.username,
        password_hash=hashed_password,
        role=form_data.role,
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    return {"username": new_user.username, "role": new_user.role}

@app.post("/login")
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    user = await authenticate_user(form_data.username, form_data.password, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}

@app.post("/refresh")
async def refresh_token(current_user: User = Depends(get_current_user)):
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": current_user.username}, expires_delta=access_token_expires
    )
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    }

@app.get("/products", response_model=List[ProductSchema])
async def get_products(
    skip: int = 0,
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role not in ["admin", "manager"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    result = await db.execute(select(Product).offset(skip).limit(limit))
    return result.scalars().all()

@app.post("/products", response_model=ProductSchema)
async def create_new_product(
    product: Product,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role not in ["admin", "manager"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    return await create_product(db, product)

@app.get("/products/{product_id}", response_model=ProductSchema)
async def read_product(
    product_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    product = await get_product(db, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product

@app.put("/products/{product_id}", response_model=ProductSchema)
async def update_existing_product(
    product_id: int,
    product: Product,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role not in ["admin", "manager"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    updated_product = await update_product(db, product_id, product)
    if not updated_product:
        raise HTTPException(status_code=404, detail="Product not found")
    return updated_product

@app.delete("/products/{product_id}")
async def delete_existing_product(
    product_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role not in ["admin", "manager"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    product = await delete_product(db, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return {"message": "Product deleted"}

@app.get("/meals", response_model=List[MealSchema])
async def get_meals(
    skip: int = 0,
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role not in ["admin", "manager"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    result = await db.execute(select(Meal).offset(skip).limit(limit))
    return result.scalars().all()

@app.post("/meals", response_model=MealSchema)
async def create_new_meal(
    meal: MealCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role not in ["admin", "manager"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    return await create_meal(db, meal)

@app.get("/meals/{meal_id}", response_model=MealSchema)
async def read_meal(
    meal_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    meal = await get_meal(db, meal_id)
    if not meal:
        raise HTTPException(status_code=404, detail="Meal not found")
    return meal

@app.put("/meals/{meal_id}", response_model=MealSchema)
async def update_existing_meal(
    meal_id: int,
    meal: MealCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role not in ["admin", "manager"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    updated_meal = await update_meal(db, meal_id, meal)
    if not updated_meal:
        raise HTTPException(status_code=404, detail="Meal not found")
    return updated_meal

@app.delete("/meals/{meal_id}")
async def delete_existing_meal(
    meal_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role not in ["admin", "manager"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    meal = await delete_meal(db, meal_id)
    if not meal:
        raise HTTPException(status_code=404, detail="Meal not found")
    return {"message": "Meal deleted"}

@app.post("/serve-meal", response_model=MealServingSchema)
async def serve_meal_endpoint(
    meal_serving: MealServingCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role not in ["admin", "cook"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    return await serve_meal(db, meal_serving, current_user.id)

@app.get("/portion-estimates", response_model=List[PortionEstimate])
async def get_portion_estimates_endpoint(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role not in ["admin", "manager"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    return await get_portion_estimates(db)

@app.post("/generate-report")
async def generate_report(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role not in ["admin", "manager"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    task = celery_app.send_task("tasks.generate_monthly_report")
    logger.info(f"Started generate_monthly_report task with ID: {task.id}")
    return {"task_id": task.id, "status": "Report generation started"}

@app.get("/notifications")
async def get_notifications(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role not in ["admin", "manager"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    result = await db.execute(select(Product).where(Product.quantity < Product.threshold))
    low_inventory = result.scalars().all()
    task = celery_app.send_task("tasks.calculate_discrepancy_rate")
    return {
        "low_inventory": [
            {"id": p.id, "name": p.name, "quantity": p.quantity}
            for p in low_inventory
        ],
        "discrepancy_task_id": task.id,
    }

@app.get("/discrepancy/{task_id}")
async def get_discrepancy(
    task_id: str, current_user: User = Depends(get_current_user)
):
    if current_user.role not in ["admin", "manager"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    task_result = celery_app.AsyncResult(task_id)
    state = getattr(task_result, "state", getattr(task_result, "status", None))
    logger.info(f"Checking discrepancy task {task_id}, state: {state}")
    if not task_result.ready():
        return {"status": "PENDING", "detail": "Task is still processing"}
    if task_result.failed():
        raise HTTPException(
            status_code=500,
            detail=f"Discrepancy calculation failed: {task_result.get(propagate=False)}",
        )
    return {"status": "SUCCESS", "result": task_result.get()}

@app.get("/report/{task_id}")
async def get_report(
    task_id: str, current_user: User = Depends(get_current_user)
):
    if current_user.role not in ["admin", "manager"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    task_result = celery_app.AsyncResult(task_id)
    state = getattr(task_result, "state", getattr(task_result, "status", None))
    logger.info(f"Checking report task {task_id}, state: {state}")
    if not task_result.ready():
        return {"status": "PENDING", "detail": "Task is still processing"}
    if task_result.failed():
        raise HTTPException(
            status_code=500,
            detail=f"Report generation failed: {task_result.get(propagate=False)}",
        )
    return {"status": "SUCCESS", "result": task_result.get()}

@app.get("/usage-report")
async def get_usage_report(
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role not in ["admin", "manager"]:
        raise HTTPException(status_code=403, detail="Not authorized")
    if start_date:
        # ensure it's in UTC and then drop tzinfo
        start_date = start_date.astimezone(timezone.utc).replace(tzinfo=None)
    if end_date:
        end_date = end_date.astimezone(timezone.utc).replace(tzinfo=None)
        # ──────────────────────────────────────────────

    query = select(InventoryLog)
    if start_date:
        query = query.where(InventoryLog.timestamp >= start_date)
    if end_date:
        query = query.where(InventoryLog.timestamp <= end_date)

    result = await db.execute(query)


    logs = result.scalars().all()
    usage_data = {}
    for log in logs:
        date = log.timestamp.date().isoformat()
        usage_data[date] = usage_data.get(date, 0) + log.quantity
    return {"usage": usage_data}
# main.py






@app.get("/servings-log")
async def get_servings_log(
    skip: int = 0,
    limit: int = 10,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if current_user.role not in ["admin", "manager"]:
        raise HTTPException(status_code=403, detail="Not authorized")

    # 1️⃣ Use selectinload(MealServing.user) to eager-load the 'user' relationship:
    query = (
        select(MealServing)
        .options(selectinload(MealServing.user))
    )

    if start_date:
        query = query.where(MealServing.timestamp >= start_date)
    if end_date:
        query = query.where(MealServing.timestamp <= end_date)

    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    servings = result.scalars().all()

    # Now s.user is already loaded, so .username won’t trigger a lazy-load
    return [
        {"meal_id": s.meal_id, "user": s.user.username, "timestamp": s.timestamp}
        for s in servings
    ]


@app.websocket("/ws/inventory")
async def websocket_inventory(
    websocket: WebSocket, token: str, db: AsyncSession = Depends(get_db)
):
    try:
        if JWT_SECRET is None:
            await websocket.close(code=1011, reason="Internal server error")
            return

        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            username: str = payload.get("sub")
            if username is None:
                await websocket.close(code=1008, reason="Invalid token")
                return
        except JWTError:
            await websocket.close(code=1008, reason="Invalid token")
            return

        result = await db.execute(select(User).where(User.username == username))
        user = result.scalars().first()
        if not user or user.role not in ["admin", "manager"]:
            await websocket.close(code=1008, reason="Not authorized")
            return

        await websocket.accept()
        redis_client = await redis.from_url(
            "redis://redis:6379/0", encoding="utf-8", decode_responses=True
        )
        pubsub = redis_client.pubsub()
        await pubsub.subscribe("inventory_updates")

        try:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    await websocket.send_json(
                        {"channel": message["channel"], "data": message["data"]}
                    )
        except WebSocketDisconnect:
            logger.info(f"WebSocket disconnected for user {user.username}")
        finally:
            await pubsub.unsubscribe("inventory_updates")
            await redis_client.aclose()
    except Exception as e:
        logger.error(f"WebSocket setup error: {e}")
        await websocket.close(code=1011, reason="Internal error")
        raise






from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from .models import User
from .schemas import Product, ProductSchema
from .database import get_db
from .auth import get_current_user

# Import patternlar reporting.py dan
from .reporting import (
    AuthorizationHandler,
    AdminAuthorizationHandler,
    ManagerAuthorizationHandler,
    CookAuthorizationHandler,
    ProductFactory,
    MealFactory,
    DatabaseSource,
    CacheSource,
)

app = FastAPI()

# Misol: Chain of Responsibility bilan authorization tekshirish uchun funksiya
async def check_authorization(user: User, action: str) -> bool:
    handler_chain = AdminAuthorizationHandler(
        ManagerAuthorizationHandler(
            CookAuthorizationHandler(None)
        )
    )
    return await handler_chain.handle(user, action)

# Eski kod: oddiy status endpoint
@app.get("/")
async def root():
    return {"message": "Welcome to the API"}

# Mahsulot yaratish, Factory pattern ishlatilgan
@app.post("/products", response_model=ProductSchema)
async def create_product_endpoint(
    product: Product,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Chain of responsibility orqali authorization
    is_authorized = await check_authorization(current_user, "edit")
    if not is_authorized:
        raise HTTPException(status_code=403, detail="Not authorized to create product")

    factory = ProductFactory()
    created_product = await factory.create(db, product)
    return created_product

# Masalan, ma'lumot olish uchun Bridge pattern misoli
@app.get("/products")
async def get_products(
    db: AsyncSession = Depends(get_db),
):
    db_source = DatabaseSource(db)
    products = await db_source.get_data()
    return {"products": products}

# Protected action uchun Chain of Responsibility dan foydalanish
@app.post("/actions")
async def perform_action(
    action: str,
    current_user: User = Depends(get_current_user),
):
    authorized = await check_authorization(current_user, action)
    if not authorized:
        raise HTTPException(status_code=403, detail="Not authorized for this action")

    return {"message": f"Action '{action}' permitted for user {current_user.username}"}
