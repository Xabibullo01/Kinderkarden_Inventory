from celery import shared_task
from .database import get_db_sync
from .models import InventoryLog, MealServing
from sqlalchemy.orm import Session
from sqlalchemy import select
from datetime import datetime, timedelta, timezone
from .crud import get_portion_estimates
from .celery_app import celery_app
import logging

logger = logging.getLogger('celery')

@celery_app.task(name='tasks.generate_monthly_report')
def generate_monthly_report():
    logger.info("Starting generate_monthly_report task")
    db: Session = next(get_db_sync())
    try:
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=30)
        logger.info(f"Querying logs from {start_date} to {end_date}")
        logs = db.execute(
            select(InventoryLog).where(
                InventoryLog.timestamp >= start_date,
                InventoryLog.timestamp <= end_date
            )
        ).scalars().all()
        logger.info(f"Found {len(logs)} logs")
        report = {"total_deliveries": 0, "total_consumption": 0}
        for log in logs:
            if log.change_type == "delivery":
                report["total_deliveries"] += log.quantity
            elif log.change_type == "consumption":
                report["total_consumption"] += log.quantity
        logger.info("Task completed successfully")
        return {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "report": report
        }
    except Exception as e:
        logger.error(f"Task failed: {str(e)}")
        raise Exception(f"Failed to generate monthly report: {str(e)}")
    finally:
        db.close()

@celery_app.task(name='tasks.calculate_discrepancy_rate')
def calculate_discrepancy_rate():
    logger.info("Starting calculate_discrepancy_rate task")
    db: Session = next(get_db_sync())
    try:
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=30)
        servings = db.execute(
            select(MealServing).where(
                MealServing.timestamp >= start_date,
                MealServing.timestamp <= end_date
            )
        ).scalars().all()
        servings_count = len(servings)
        potential_portions = sum(p["portions"] for p in get_portion_estimates(db))
        discrepancy_rate = ((potential_portions - servings_count) / potential_portions * 100) if potential_portions > 0 else 0
        logger.info("Task completed successfully")
        return {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "servings": servings_count,
            "potential_portions": potential_portions,
            "discrepancy_rate": discrepancy_rate
        }
    except Exception as e:
        logger.error(f"Task failed: {str(e)}")
        raise Exception(f"Failed to calculate discrepancy rate: {str(e)}")
    finally:
        db.close()