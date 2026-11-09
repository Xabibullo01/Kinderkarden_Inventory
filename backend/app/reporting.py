from abc import ABC, abstractmethod

# Chain of Responsibility: Authorization Handler
class AuthorizationHandler(ABC):
    def __init__(self, successor=None):
        self.successor = successor

    @abstractmethod
    async def handle(self, user, action):
        if self.successor:
            return await self.successor.handle(user, action)
        return False


class AdminAuthorizationHandler(AuthorizationHandler):
    async def handle(self, user, action):
        if user.role == "admin":
            return True
        return await super().handle(user, action)


class ManagerAuthorizationHandler(AuthorizationHandler):
    async def handle(self, user, action):
        if user.role == "manager" and action in ["view", "edit"]:
            return True
        return await super().handle(user, action)


class CookAuthorizationHandler(AuthorizationHandler):
    async def handle(self, user, action):
        if user.role == "cook" and action == "serve":
            return True
        return await super().handle(user, action)


# Factory Pattern: Abstract Factory for Products and Meals
class ItemFactory(ABC):
    @abstractmethod
    async def create(self, db, item_data, user_id=None):
        pass


class ProductFactory(ItemFactory):
    async def create(self, db, item_data, user_id=None):
        from .crud import create_product
        return await create_product(db, item_data, user_id)


class MealFactory(ItemFactory):
    async def create(self, db, item_data, user_id=None):
        from .crud import create_meal
        return await create_meal(db, item_data)


# Bridge Pattern: Abstract Data Source and Concrete Implementations
class DataSource(ABC):
    @abstractmethod
    async def get_data(self):
        pass


class DatabaseSource(DataSource):
    def __init__(self, db):
        self.db = db

    async def get_data(self):
        from sqlalchemy import select
        from .models import Product
        result = await self.db.execute(select(Product))
        return [
            {"id": p.id, "name": p.name, "quantity": p.quantity, "threshold": p.threshold}
            for p in result.scalars().all()
        ]


class CacheSource(DataSource):
    def __init__(self, cache_client):
        self.cache_client = cache_client

    async def get_data(self):
        # example getting cached data
        data = await self.cache_client.get("products_cache")
        return data


