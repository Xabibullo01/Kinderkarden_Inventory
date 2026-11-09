"""Design-pattern examples (Chain of Responsibility, Factory, Bridge).

These used to live at the bottom of main.py and created a *second* ``FastAPI()``
instance, which silently replaced the real app and hid all the main endpoints.
Now they are a normal router mounted under ``/patterns``.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import get_current_user
from .database import get_db
from .models import User
from .reporting import (
    AdminAuthorizationHandler,
    CookAuthorizationHandler,
    DatabaseSource,
    ManagerAuthorizationHandler,
    ProductFactory,
)
from .schemas import ProductCreate, ProductSchema

router = APIRouter(prefix="/patterns", tags=["design-patterns"])


async def check_authorization(user: User, action: str) -> bool:
    """Chain of Responsibility: admin -> manager -> cook handlers."""
    handler_chain = AdminAuthorizationHandler(
        ManagerAuthorizationHandler(CookAuthorizationHandler(None))
    )
    return await handler_chain.handle(user, action)


@router.post("/products", response_model=ProductSchema)
async def create_product_with_factory(
    product: ProductCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Factory pattern: creates a product through ProductFactory."""
    if not await check_authorization(current_user, "edit"):
        raise HTTPException(status_code=403, detail="Not authorized to create product")
    return await ProductFactory().create(db, product, current_user.id)


@router.get("/products")
async def get_products_via_bridge(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Bridge pattern: reads products through a DataSource abstraction."""
    products = await DatabaseSource(db).get_data()
    return {"products": products}


@router.post("/actions")
async def perform_action(action: str, current_user: User = Depends(get_current_user)):
    if not await check_authorization(current_user, action):
        raise HTTPException(status_code=403, detail="Not authorized for this action")
    return {"message": f"Action '{action}' permitted for user {current_user.username}"}
