"""Aggregated REST routers of the current API version."""

from fastapi import APIRouter

from app.domains.payments.routers.payment_routers import router as payments_router

rest_v1_routers: list[APIRouter] = [payments_router]
