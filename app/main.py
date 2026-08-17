from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from app.core.config import settings
from app.db.database import Base, engine, run_lightweight_migrations, SessionLocal
from app.db import models  # noqa: F401 - registra los modelos en Base antes de create_all
from app.api.routes import router as chat_router
from app.api.whatsapp import router as whatsapp_router
from app.api.knowledge import router as knowledge_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────────
    # Crea tablas si no existen (entornos nuevos) y aplica migraciones ligeras
    # idempotentes para bases de datos SQLite ya existentes.
    Base.metadata.create_all(bind=engine)
    run_lightweight_migrations()

    # Purga sesiones de visitantes caducadas (>7 días sin actividad).
    # Se ejecuta una vez al arrancar el servidor; la purga lazy en /chat cubre
    # los días en los que el proceso no se reinicia.
    try:
        from app.db.crud import purgar_sesiones_visitantes_caducadas
        db = SessionLocal()
        try:
            purgar_sesiones_visitantes_caducadas(db)
        finally:
            db.close()
    except Exception as e:
        print(f"[STARTUP] Purga de visitantes no crítica, se omite: {e}")

    yield
    # ── Shutdown (nada que hacer por ahora) ──────────────────────────────────


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Backend API para Copiloto de Transparencia de Facturación (Lucía)",
    lifespan=lifespan,
)

# Set up CORS for frontend channels
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_router, prefix=settings.API_V1_STR)
app.include_router(whatsapp_router)
app.include_router(knowledge_router)

# Serve Web Client
app.mount("/static", StaticFiles(directory="app/static"), name="static")

@app.get("/")
def read_root():
    return RedirectResponse(url="/static/index.html")

@app.get("/health")
def health_check():
    return {"status": "ok", "project": settings.PROJECT_NAME, "version": settings.VERSION}
