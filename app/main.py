import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .admin_routes import router as admin_router
from .config import get_settings
from .database import init_db
from .device_routes import router as device_router
from .webhook_worker import run_webhook_worker


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    stop = asyncio.Event()
    worker = asyncio.create_task(run_webhook_worker(get_settings(), stop))
    yield
    stop.set()
    await worker


settings = get_settings()
app = FastAPI(title="SMRI Attendance Logging Backend", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def protect_admin(request: Request, call_next):
    path = request.url.path
    if path.startswith("/admin") and path not in {"/admin/login", "/admin/logout"}:
        if not request.session.get("admin_authenticated"):
            return RedirectResponse("/admin/login", status_code=303)
    return await call_next(request)


app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie="smri_attendance_session",
    same_site="lax",
    https_only=False,
    max_age=60 * 60 * 12,
)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(device_router)
app.include_router(admin_router)


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse("/admin")


@app.get("/health")
async def health():
    return {"status": "ok"}

